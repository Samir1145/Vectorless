"""
ocr.py
~~~~~~
OCR fallback for image-only (scanned) PDFs using ocrmypdf + Tesseract.
For pages that Tesseract cannot read (< _VISION_THRESHOLD chars), a vision
LLM is called as a second-stage fallback.

ocrmypdf runs Tesseract on each page, writes a text-layer PDF to a temp
file, then PyMuPDF reads the text layer — no numpy / ML dependencies.

Public API:
  is_image_based(pdf_path)         → bool
  extract_pages(pdf_path, model)   → list[(text, token_count)]
"""

from __future__ import annotations

import base64
import logging
import subprocess
import tempfile
import time
from functools import lru_cache
from pathlib import Path

import litellm
import pymupdf
import yaml

log = logging.getLogger(__name__)

# Average characters per page below this threshold → treat as image-based
_IMAGE_THRESHOLD = 8

# Tesseract output below this many chars per page → try vision LLM
_VISION_THRESHOLD = 50

# Resolution for rendering pages to images sent to the vision model.
# 150 DPI ≈ 1275×1650 px (letter) — good text quality, ~4 image tiles on gpt-4o
_RENDER_DPI = 150


# ---------------------------------------------------------------------------
# Config helper
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _vision_model() -> str:
    """Return the configured vision model string, or '' if disabled."""
    cfg_path = Path(__file__).parent / "config.yaml"
    try:
        with open(cfg_path) as f:
            return yaml.safe_load(f).get("pipeline", {}).get("model_vision", "").strip()
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Vision helpers
# ---------------------------------------------------------------------------

def _render_page_b64(pdf_path: str, page_index: int) -> str:
    """Render a single PDF page to a base64-encoded PNG at _RENDER_DPI."""
    doc = pymupdf.open(pdf_path)
    try:
        page = doc[page_index]
        mat  = pymupdf.Matrix(_RENDER_DPI / 72, _RENDER_DPI / 72)
        pix  = page.get_pixmap(matrix=mat, colorspace=pymupdf.csGRAY)
        return base64.b64encode(pix.tobytes("png")).decode()
    finally:
        doc.close()


def _vision_extract_page(pdf_path: str, page_index: int, vision_model: str) -> str:
    """
    Ask a vision LLM to extract text from a single page image.

    Used when Tesseract returns fewer than _VISION_THRESHOLD characters —
    typically a low-quality scan, stamp-heavy page, or handwritten annotation.

    Returns extracted text string, or '' on any failure (non-fatal).
    """
    try:
        b64 = _render_page_b64(pdf_path, page_index)
        response = litellm.completion(
            model=vision_model,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{b64}"},
                    },
                    {
                        "type": "text",
                        "text": (
                            "This is a page from an Indian court document. "
                            "Extract ALL text exactly as it appears — including case numbers, "
                            "party names, dates, orders, stamps, and any marginal notes. "
                            "Preserve paragraph structure. "
                            "Output only the extracted text, nothing else."
                        ),
                    },
                ],
            }],
            max_tokens=4096,
        )
        return (response.choices[0].message.content or "").strip()
    except Exception as exc:
        log.warning(
            "OCR | vision fallback failed | page_index=%d | model=%s | %s",
            page_index, vision_model, exc,
        )
        return ""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def is_image_based(pdf_path: str, threshold: int = _IMAGE_THRESHOLD) -> bool:
    """Return True if the PDF has no meaningful selectable text (likely a scan)."""
    try:
        doc = pymupdf.open(pdf_path)
        if not doc.page_count:
            return True
        total_chars = sum(len(page.get_text().strip()) for page in doc)
        n = doc.page_count
        doc.close()
        return (total_chars / n) < threshold
    except Exception as e:
        log.warning("is_image_based check failed (%s) — assuming text-based", e)
        return False


def extract_pages(pdf_path: str, model: str | None = None) -> list[tuple[str, int]]:
    """
    Extract per-page text from a scanned PDF.

    Stage 1 — ocrmypdf + Tesseract: runs on the whole document.
    Stage 2 — vision LLM fallback: any page whose Tesseract output is below
               _VISION_THRESHOLD characters is re-extracted via the vision
               model configured in config.yaml (pipeline.model_vision).
               Vision fallback is skipped silently if model_vision is unset.

    Returns a list of (page_text, token_count) tuples, one per page —
    the same shape as utils.get_page_tokens() so it is a drop-in replacement.

    Raises:
        RuntimeError  if ocrmypdf is not installed or OCR fails entirely.
    """
    t0 = time.perf_counter()
    log.info("OCR | starting Tesseract extraction | path=%s", pdf_path)

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        out_path = tmp.name

    try:
        result = subprocess.run(
            [
                "ocrmypdf",
                "--skip-text",       # don't re-OCR pages that already have text
                "--optimize", "0",   # skip image optimisation — we only want text
                "--output-type", "pdf",
                pdf_path,
                out_path,
            ],
            capture_output=True,
            text=True,
            timeout=300,
        )

        if result.returncode not in (0, 6):
            # exit 6 = "already has text" — fine; anything else is a real error
            raise RuntimeError(
                f"ocrmypdf exited {result.returncode}: {result.stderr.strip()}"
            )

        # Read text layer from the Tesseract-OCR'd PDF
        doc = pymupdf.open(out_path)
        page_list: list[tuple[str, int]] = []
        empty_pages = 0

        for page in doc:
            text = page.get_text().strip()
            if not text:
                empty_pages += 1
            tokens = litellm.token_counter(model=model, text=text) if text else 0
            page_list.append((text, tokens))

        doc.close()

    finally:
        Path(out_path).unlink(missing_ok=True)

    tesseract_ms = (time.perf_counter() - t0) * 1000
    log.info(
        "OCR | Tesseract done | pages=%d | empty=%d | elapsed=%.0fms",
        len(page_list), empty_pages, tesseract_ms,
    )

    if not page_list:
        raise RuntimeError("ocrmypdf returned no pages — cannot process document")

    # ── Stage 2: vision LLM fallback for pages Tesseract couldn't read ───────
    vision_model = _vision_model()
    if vision_model:
        weak_pages = [
            i for i, (text, _) in enumerate(page_list)
            if len(text) < _VISION_THRESHOLD
        ]
        if weak_pages:
            log.info(
                "OCR | vision fallback triggered | model=%s | pages=%s",
                vision_model, weak_pages,
            )
            tv0 = time.perf_counter()
            upgraded = 0
            for i in weak_pages:
                vision_text = _vision_extract_page(pdf_path, i, vision_model)
                if vision_text:
                    tokens = litellm.token_counter(model=model, text=vision_text) if model else 0
                    page_list[i] = (vision_text, tokens)
                    upgraded += 1
                    log.debug(
                        "OCR | vision upgraded page %d | chars=%d",
                        i + 1, len(vision_text),
                    )
            vision_ms = (time.perf_counter() - tv0) * 1000
            log.info(
                "OCR | vision fallback done | upgraded=%d/%d | elapsed=%.0fms",
                upgraded, len(weak_pages), vision_ms,
            )
    else:
        if empty_pages:
            log.debug(
                "OCR | %d empty page(s) after Tesseract — vision fallback disabled "
                "(set pipeline.model_vision in config.yaml to enable)",
                empty_pages,
            )

    total_tokens = sum(t for _, t in page_list)
    log.info(
        "OCR | extraction complete | pages=%d | total_tokens=%d | total_elapsed=%.0fms",
        len(page_list), total_tokens, (time.perf_counter() - t0) * 1000,
    )
    return page_list
