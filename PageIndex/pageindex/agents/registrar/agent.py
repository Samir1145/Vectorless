"""
registrar/agent.py
~~~~~~~~~~~~~~~~~~
Registrar Agent — Stage 3 of the Adversarial Pipeline.

Reads both parties' submissions (and optional Verifier audits) and produces
the AdversarialMatrix — a neutral, issue-by-issue map of the dispute.

If the human reviewer previously rejected a matrix, the rejection reason is
injected into this prompt so the agent can correct its approach.

Public API:
    run_registrar(model, petitioner_submission, respondent_submission,
                  petitioner_audit=None, respondent_audit=None, rejection_feedback=None)
        -> AdversarialMatrix
"""

from __future__ import annotations

import logging
from pathlib import Path

from .schema import AdversarialMatrix
from ..clerk.schema import StandardizedPartySubmission
from ..verifier.schema import VerifiedPartySubmission
from ...shared.llm import (
    _chat,
    _agent_temperature,
    _resolve_model,
    load_file_prompt,
    load_skills_file,
)

log = logging.getLogger(__name__)

_DIR = Path(__file__).parent   # pageindex/agents/registrar/


def run_registrar(
    model: str,
    petitioner_submission: StandardizedPartySubmission,
    respondent_submission: StandardizedPartySubmission,
    petitioner_audit: VerifiedPartySubmission | None = None,
    respondent_audit: VerifiedPartySubmission | None = None,
    rejection_feedback: str | None = None,
) -> AdversarialMatrix:
    """
    Frame all contested issues neutrally and produce the AdversarialMatrix.

    Args:
        model:                  LiteLLM model string (fallback if config tier absent)
        petitioner_submission:  Clerk output for the Petitioner
        respondent_submission:  Clerk output for the Respondent
        petitioner_audit:       Optional Verifier audit for the Petitioner
        respondent_audit:       Optional Verifier audit for the Respondent
        rejection_feedback:     Reason a prior matrix was rejected by the human reviewer.
                                Injected into the prompt so the agent corrects its approach.

    Returns:
        AdversarialMatrix with human_review_status = "pending"
    """
    _model = _resolve_model("balanced", model)
    _temp  = _agent_temperature("registrar")

    log.info(
        "Registrar Agent | model=%s | audits=%s | rerun=%s",
        _model, petitioner_audit is not None, bool(rejection_feedback),
    )

    _no_audit = "No verification audit available for this submission."
    _feedback = (
        f"IMPORTANT — a previous version of this matrix was rejected by the human reviewer "
        f"with the following feedback. Address these issues in your new matrix:\n\n{rejection_feedback}"
        if rejection_feedback
        else "No prior rejection — this is the first run of the Registrar Agent for this case."
    )

    prompt = load_file_prompt(
        _DIR / "task.md",
        petitioner_submission_json=petitioner_submission.model_dump_json(indent=2),
        respondent_submission_json=respondent_submission.model_dump_json(indent=2),
        petitioner_audit_json=petitioner_audit.model_dump_json(indent=2) if petitioner_audit else _no_audit,
        respondent_audit_json=respondent_audit.model_dump_json(indent=2) if respondent_audit else _no_audit,
        rejection_feedback=_feedback,
    )

    result: AdversarialMatrix = _chat(
        _model, prompt, AdversarialMatrix,
        system=load_skills_file(_DIR / "skills.md"),
        temperature=_temp,
        _label="registrar",
    )

    log.info(
        "Registrar Agent done | undisputed_facts=%d | framed_issues=%d",
        len(result.undisputed_background),
        len(result.framed_issues),
    )
    result.human_review_status = "pending"   # always reset — human must approve
    return result
