"""
clerk/agent.py
~~~~~~~~~~~~~~
Clerk Agent — Stage 1 of the Adversarial Pipeline.

Reads one party's court document and extracts a structured record of their
factual claims, legal issues, citations, and prayers.

Public API:
    run_clerk(model, party_role, document_type, document_text)
        -> StandardizedPartySubmission
"""

from __future__ import annotations

import logging
from pathlib import Path

from .schema import StandardizedPartySubmission
from ...shared.llm import (
    _chat,
    _agent_temperature,
    _resolve_model,
    _truncate_doc,
    load_file_prompt,
    load_skills_file,
)

log = logging.getLogger(__name__)

_DIR = Path(__file__).parent   # pageindex/agents/clerk/


def run_clerk(
    model: str,
    party_role: str,
    document_type: str,
    document_text: str,
) -> StandardizedPartySubmission:
    """
    Extract structured facts, issues, citations, and prayers from one
    party's document.

    Args:
        model:         LiteLLM model string (fallback if config tier is absent)
        party_role:    "Petitioner" or "Respondent"
        document_type: e.g. "Petition", "Reply", "Affidavit"
        document_text: Full plain-text content of the document

    Returns:
        StandardizedPartySubmission
    """
    _model = _resolve_model("fast", model)
    _temp  = _agent_temperature("clerk")

    log.info("Clerk Agent | party_role=%s | document_type=%s | model=%s", party_role, document_type, _model)

    prompt = load_file_prompt(
        _DIR / "task.md",
        party_role=party_role,
        document_type=document_type,
        document_text=_truncate_doc(document_text, _model),
    )

    result: StandardizedPartySubmission = _chat(
        _model, prompt, StandardizedPartySubmission,
        system=load_skills_file(_DIR / "skills.md"),
        temperature=_temp,
        _label="clerk",
    )

    log.info(
        "Clerk Agent done | party_role=%s | facts=%d | issues=%d | citations=%d | prayers=%d",
        party_role,
        len(result.extracted_facts),
        len(result.issues_raised),
        len(result.cited_laws_and_cases),
        len(result.prayers),
    )
    return result
