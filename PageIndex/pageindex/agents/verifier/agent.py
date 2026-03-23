"""
verifier/agent.py
~~~~~~~~~~~~~~~~~
Verifier Agent — Stage 2 of the Adversarial Pipeline.

Cross-checks every fact and citation in a StandardizedPartySubmission
against the original source document. Produces a confidence-scored audit.

Runs in the same thread as the Clerk — immediately after run_clerk()
completes for one party, run_verifier() is called in the same worker.

Public API:
    run_verifier(model, party_role, document_type, submission, document_text)
        -> VerifiedPartySubmission
"""

from __future__ import annotations

import logging
from pathlib import Path

from .schema import VerifiedPartySubmission
from ..clerk.schema import StandardizedPartySubmission
from ...shared.llm import (
    _chat,
    _agent_temperature,
    _resolve_model,
    _truncate_doc,
    load_file_prompt,
    load_skills_file,
)

log = logging.getLogger(__name__)

_DIR = Path(__file__).parent   # pageindex/agents/verifier/


def run_verifier(
    model: str,
    party_role: str,
    document_type: str,
    submission: StandardizedPartySubmission,
    document_text: str,
) -> VerifiedPartySubmission:
    """
    Audit a StandardizedPartySubmission against the original document text.

    Args:
        model:         LiteLLM model string (fallback if config tier is absent)
        party_role:    "Petitioner" or "Respondent"
        document_type: e.g. "Petition", "Reply", "Affidavit"
        submission:    The Clerk Agent output to audit
        document_text: Full plain-text content of the original document

    Returns:
        VerifiedPartySubmission with confidence score, flags, citation audit,
        and any internal contradictions found.
    """
    _model = _resolve_model("fast", model)
    _temp  = _agent_temperature("verifier")

    log.info("Verifier Agent | party_role=%s | document_type=%s | model=%s", party_role, document_type, _model)

    prompt = load_file_prompt(
        _DIR / "task.md",
        party_role=party_role,
        document_type=document_type,
        submission_json=submission.model_dump_json(indent=2),
        document_text=_truncate_doc(document_text, _model),
    )

    result: VerifiedPartySubmission = _chat(
        _model, prompt, VerifiedPartySubmission,
        system=load_skills_file(_DIR / "skills.md"),
        temperature=_temp,
        _label="verifier",
    )

    log.info(
        "Verifier Agent done | party_role=%s | confidence=%.2f | flags=%d | contradictions=%d",
        party_role,
        result.overall_confidence,
        len(result.flags),
        len(result.internal_contradictions),
    )
    return result
