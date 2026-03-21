"""
agents.py
~~~~~~~~~
The three agents of the Adversarial Multi-Agent Synthesis Pipeline.

Each agent is a thin wrapper around an Instructor-patched LiteLLM call that
validates and coerces the LLM response into the corresponding Pydantic model.

  run_clerk(model, party_role, document_type, document_text)
      → StandardizedPartySubmission

  run_registrar(model, petitioner_submission, respondent_submission)
      → AdversarialMatrix

  run_judge_on_issue(model, case_title, background_facts, issue)
      → ReasonedDecision

  run_judge_final_order(model, case_title, background_facts, reasoned_decisions)
      → str   (the final_order paragraph)

All functions are synchronous; the pipeline layer (pipeline.py) handles
concurrency where needed (e.g., running both Clerk calls in parallel).
"""

from __future__ import annotations

import json
import logging

import instructor
import litellm

from .models import (
    AdversarialMatrix,
    DraftCourtOrder,
    FramedIssue,
    ReasonedDecision,
    StandardizedPartySubmission,
)
from .prompt_loader import load_prompt

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Instructor client (wraps LiteLLM)
# ---------------------------------------------------------------------------

_client = instructor.from_litellm(litellm.completion)


def _chat(model: str, prompt: str, response_model):
    """Single-turn structured completion via Instructor."""
    return _client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        response_model=response_model,
        temperature=0,
        max_retries=3,
    )


# ---------------------------------------------------------------------------
# Clerk Agent
# ---------------------------------------------------------------------------

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
        model:         LiteLLM model string (e.g. "gpt-4o-2024-11-20")
        party_role:    "Petitioner" or "Respondent"
        document_type: e.g. "Petition", "Reply", "Affidavit"
        document_text: Full plain-text content of the document

    Returns:
        StandardizedPartySubmission Pydantic model
    """
    log.info("Clerk Agent | party_role=%s | document_type=%s | model=%s", party_role, document_type, model)
    prompt = load_prompt(
        "clerk_agent",
        party_role=party_role,
        document_type=document_type,
        document_text=document_text,
    )
    result: StandardizedPartySubmission = _chat(model, prompt, StandardizedPartySubmission)
    log.info(
        "Clerk Agent done | party_role=%s | facts=%d | issues=%d | citations=%d | prayers=%d",
        party_role,
        len(result.extracted_facts),
        len(result.issues_raised),
        len(result.cited_laws_and_cases),
        len(result.prayers),
    )
    return result


# ---------------------------------------------------------------------------
# Registrar Agent
# ---------------------------------------------------------------------------

def run_registrar(
    model: str,
    petitioner_submission: StandardizedPartySubmission,
    respondent_submission: StandardizedPartySubmission,
) -> AdversarialMatrix:
    """
    Frame all contested issues neutrally by comparing both parties'
    submissions and produce the AdversarialMatrix.

    Args:
        model:                  LiteLLM model string
        petitioner_submission:  Output of run_clerk for the Petitioner
        respondent_submission:  Output of run_clerk for the Respondent

    Returns:
        AdversarialMatrix with human_review_status = "pending"
    """
    log.info("Registrar Agent | model=%s", model)
    prompt = load_prompt(
        "registrar_agent",
        petitioner_submission_json=petitioner_submission.model_dump_json(indent=2),
        respondent_submission_json=respondent_submission.model_dump_json(indent=2),
    )
    result: AdversarialMatrix = _chat(model, prompt, AdversarialMatrix)
    log.info(
        "Registrar Agent done | undisputed_facts=%d | framed_issues=%d",
        len(result.undisputed_background),
        len(result.framed_issues),
    )
    # Always reset to pending — human must approve before Judge runs
    result.human_review_status = "pending"
    return result


# ---------------------------------------------------------------------------
# Judge Agent — per-issue IRAC
# ---------------------------------------------------------------------------

def run_judge_on_issue(
    model: str,
    case_title: str,
    background_facts: str,
    issue: FramedIssue,
) -> ReasonedDecision:
    """
    Apply IRAC reasoning to a single framed issue.

    Args:
        model:            LiteLLM model string
        case_title:       Human-readable case name
        background_facts: Narrative of undisputed facts
        issue:            A single FramedIssue from the AdversarialMatrix

    Returns:
        ReasonedDecision for this issue
    """
    log.info("Judge Agent (IRAC) | issue_id=%s | model=%s", issue.issue_id, model)
    prompt = load_prompt(
        "judge_agent_irac",
        case_title=case_title,
        background_facts=background_facts,
        issue_json=issue.model_dump_json(indent=2),
    )
    result: ReasonedDecision = _chat(model, prompt, ReasonedDecision)
    log.info("Judge Agent done | issue_id=%s | conclusion=%s", result.issue_id, result.conclusion[:80])
    return result


# ---------------------------------------------------------------------------
# Judge Agent — final order synthesis
# ---------------------------------------------------------------------------

def run_judge_final_order(
    model: str,
    case_title: str,
    background_facts: str,
    reasoned_decisions: list[ReasonedDecision],
) -> str:
    """
    Synthesise all per-issue ReasonedDecisions into a final order paragraph.

    Returns the final_order string (plain text).
    """
    log.info("Judge Agent (final order) | issues=%d | model=%s", len(reasoned_decisions), model)
    decisions_json = json.dumps(
        [rd.model_dump() for rd in reasoned_decisions], indent=2
    )
    prompt = load_prompt(
        "judge_agent_final_order",
        case_title=case_title,
        background_facts=background_facts,
        reasoned_decisions_json=decisions_json,
    )
    # Final order is free-form text; use a thin wrapper model
    from pydantic import BaseModel

    class _FinalOrder(BaseModel):
        final_order: str

    result: _FinalOrder = _chat(model, prompt, _FinalOrder)
    log.info("Judge Agent final order done | length=%d chars", len(result.final_order))
    return result.final_order
