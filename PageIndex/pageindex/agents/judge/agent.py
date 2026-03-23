"""
judge/agent.py
~~~~~~~~~~~~~~
Judge Agent — Stage 6 of the Adversarial Pipeline.

Runs in two phases:
  1. run_judge_on_issue() — one IRAC call per cleared issue (sequential)
  2. run_judge_final_order() — synthesises all decisions into a final order paragraph

Both use the powerful model tier at temperature=0.

Public API:
    run_judge_on_issue(model, case_title, background_facts, issue) -> ReasonedDecision
    run_judge_final_order(model, case_title, background_facts, reasoned_decisions) -> str
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from pydantic import BaseModel

from .schema import DraftCourtOrder, ReasonedDecision
from ..registrar.schema import FramedIssue
from ...shared.llm import (
    _chat,
    _agent_temperature,
    _resolve_model,
    load_file_prompt,
    load_skills_file,
)

log = logging.getLogger(__name__)

_DIR = Path(__file__).parent   # pageindex/agents/judge/


def run_judge_on_issue(
    model: str,
    case_title: str,
    background_facts: str,
    issue: FramedIssue,
    citation_audit_summary: str = "",
) -> ReasonedDecision:
    """
    Apply IRAC reasoning to a single framed issue.

    Args:
        model:                  LiteLLM model string (fallback if config tier absent)
        case_title:             Human-readable case name
        background_facts:       Narrative of undisputed facts (from AdversarialMatrix)
        issue:                  A single FramedIssue — must be in issues_to_proceed
        citation_audit_summary: Optional summary of Citation Auditor findings to inform
                                the Judge's weighting of disputed/unverified citations.

    Returns:
        ReasonedDecision (Issue, Rule, Application, Conclusion)
    """
    _model = _resolve_model("powerful", model)
    _temp  = _agent_temperature("judge")

    log.info("Judge Agent (IRAC) | issue_id=%s | model=%s", issue.issue_id, _model)

    citation_audit_section = ""
    if citation_audit_summary:
        citation_audit_section = (
            "## Citation Audit Findings\n\n"
            "The Citation Auditor independently verified case-law citations relied on by both parties.\n"
            "Use these findings to calibrate your reliance on cited precedents:\n\n"
            + citation_audit_summary
            + "\n\nWhen a citation is marked **not found** or **misrepresented**, discount it in your "
            "analysis. When marked **unverified** (API unavailable), treat as unconfirmed and note the limitation."
        )

    prompt = load_file_prompt(
        _DIR / "task_irac.md",
        case_title=case_title,
        background_facts=background_facts,
        issue_json=issue.model_dump_json(indent=2),
        citation_audit_section=citation_audit_section,
    )

    result: ReasonedDecision = _chat(
        _model, prompt, ReasonedDecision,
        system=load_skills_file(_DIR / "skills.md"),
        temperature=_temp,
        _label="judge",
    )

    log.info("Judge Agent done | issue_id=%s | conclusion=%s", result.issue_id, result.conclusion[:80])
    return result


def run_judge_final_order(
    model: str,
    case_title: str,
    background_facts: str,
    reasoned_decisions: list[ReasonedDecision],
) -> str:
    """
    Synthesise all per-issue ReasonedDecisions into a final order paragraph.

    Args:
        model:              LiteLLM model string (fallback if config tier absent)
        case_title:         Human-readable case name
        background_facts:   Narrative of undisputed facts
        reasoned_decisions: All per-issue IRAC outputs

    Returns:
        final_order string — the ultimate disposition paragraph
    """
    _model = _resolve_model("powerful", model)
    _temp  = _agent_temperature("judge")

    log.info("Judge Agent (final order) | issues=%d | model=%s", len(reasoned_decisions), _model)

    prompt = load_file_prompt(
        _DIR / "task_final_order.md",
        case_title=case_title,
        background_facts=background_facts,
        reasoned_decisions_json=json.dumps([rd.model_dump() for rd in reasoned_decisions], indent=2),
    )

    class _FinalOrder(BaseModel):
        final_order: str

    result: _FinalOrder = _chat(
        _model, prompt, _FinalOrder,
        system=load_skills_file(_DIR / "skills.md"),
        temperature=_temp,
        _label="judge_final",
    )

    log.info("Judge Agent final order done | length=%d chars", len(result.final_order))
    return result.final_order
