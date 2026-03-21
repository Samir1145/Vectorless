"""
docling_ocr.py  →  ocr.py-compatible shim
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
OCR fallback for image-only (scanned) PDFs using ocrmypdf + Tesseract.

ocrmypdf runs Tesseract on each page, writes a text-layer PDF to a temp
file, then PyMuPDF reads the text layer — no numpy / ML dependencies.

Public API (unchanged):
  is_image_based(pdf_path)         → bool
  extract_pages(pdf_path, model)   → list[(text, token_count)]
"""

from __future__ import annotations

import logging
import subprocess
import tempfile
import time
from pathlib import Path

import litellm
import pymupdf

log = logging.getLogger(__name__)

# Average characters per page below this threshold → treat as image-based
_IMAGE_THRESHOLD = 8


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
    Extract per-page text from a scanned PDF using ocrmypdf + Tesseract.

    Returns a list of (page_text, token_count) tuples, one per page —
    the same shape as utils.get_page_tokens() so it is a drop-in replacement.

    Raises:
        RuntimeError  if ocrmypdf is not installed or OCR fails
    """
    t0 = time.perf_counter()
    log.info("OCR | starting extraction via ocrmypdf | path=%s", pdf_path)

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        out_path = tmp.name

    try:
        result = subprocess.run(
            [
                "ocrmypdf",
                "--skip-text",          # don't re-OCR pages that already have text
                "--optimize", "0",      # skip image optimisation — we only want text
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

        # Read text layer from the OCR'd PDF with PyMuPDF
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

    elapsed_ms = (time.perf_counter() - t0) * 1000
    total_tokens = sum(t for _, t in page_list)
    log.info(
        "OCR | done | pages=%d | empty=%d | total_tokens=%d | elapsed=%.0fms",
        len(page_list), empty_pages, total_tokens, elapsed_ms,
    )

    if not page_list:
        raise RuntimeError("ocrmypdf returned no pages — cannot process document")

    return page_list
