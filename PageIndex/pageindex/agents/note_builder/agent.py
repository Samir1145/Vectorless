"""
note_builder/agent.py
~~~~~~~~~~~~~~~~~~~~~
Note Builder Agent — document-level AI note generation.

Reads a document's page text (with PageIndex section markers) and produces
structured notes: summaries, legal flags, key quotes, and cross-references.

Public API:
    run_note_builder(model, document_text) -> GeneratedNotes
"""

from __future__ import annotations

import logging
from pathlib import Path

from .schema import GeneratedNotes
from ...shared.llm import (
    _chat,
    _agent_temperature,
    _resolve_model,
    _truncate_doc,
    load_file_prompt,
    load_skills_file,
)

log = logging.getLogger(__name__)

_DIR = Path(__file__).parent   # pageindex/agents/note_builder/


def run_note_builder(
    model: str,
    document_text: str,
) -> GeneratedNotes:
    """
    Generate structured notes from a document's full text.

    Args:
        model:         LiteLLM model string (fallback if config tier is absent)
        document_text: Full plain-text content with [PAGE N] and [§ node_id] markers

    Returns:
        GeneratedNotes
    """
    _model = _resolve_model("balanced", model)
    _temp  = _agent_temperature("note_builder")

    log.info("Note Builder Agent | model=%s | text_len=%d", _model, len(document_text))

    prompt = load_file_prompt(
        _DIR / "task.md",
        document_text=_truncate_doc(document_text, _model),
    )

    result: GeneratedNotes = _chat(
        _model, prompt, GeneratedNotes,
        system=load_skills_file(_DIR / "skills.md"),
        temperature=_temp,
        _label="note_builder",
    )

    log.info("Note Builder Agent done | notes=%d", len(result.notes))
    return result
