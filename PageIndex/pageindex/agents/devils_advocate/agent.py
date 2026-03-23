"""
devils_advocate/agent.py
~~~~~~~~~~~~~~~~~~~~~~~~
Devil's Advocate Agent — Stage 5 of the Adversarial Pipeline.

Stress-tests every procedurally cleared issue by generating the strongest
possible counter-argument against each party's stance. This gives the human
reviewer a vulnerability map before they decide whether to approve the matrix.

Uses temperature=0.4 — adversarial creativity benefits from some stochasticity.

Public API:
    run_devils_advocate(model, case_title, issues_to_adjudicate)
        -> StressTestedMatrix
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from .schema import StressTestedMatrix
from ..registrar.schema import FramedIssue
from ...shared.llm import (
    _chat,
    _agent_temperature,
    _resolve_model,
    load_file_prompt,
    load_skills_file,
)

log = logging.getLogger(__name__)

_DIR = Path(__file__).parent   # pageindex/agents/devils_advocate/


def run_devils_advocate(
    model: str,
    case_title: str,
    issues_to_adjudicate: list[FramedIssue],
) -> StressTestedMatrix:
    """
    Stress-test procedurally cleared issues from both sides.

    Args:
        model:                  LiteLLM model string (fallback if config tier absent)
        case_title:             Human-readable case name
        issues_to_adjudicate:   FramedIssue list — only issues_to_proceed from ProceduralAnalysis

    Returns:
        StressTestedMatrix with per-issue vulnerability analysis and reviewer_note.
    """
    _model = _resolve_model("powerful", model)
    _temp  = _agent_temperature("devils_advocate")   # 0.4 — intentionally stochastic

    log.info(
        "Devil's Advocate Agent | case=%r | issues=%d | model=%s | temp=%.1f",
        case_title, len(issues_to_adjudicate), _model, _temp,
    )

    prompt = load_file_prompt(
        _DIR / "task.md",
        case_title=case_title,
        issues_json=json.dumps([i.model_dump() for i in issues_to_adjudicate], indent=2),
    )

    result: StressTestedMatrix = _chat(
        _model, prompt, StressTestedMatrix,
        system=load_skills_file(_DIR / "skills.md"),
        temperature=_temp,
        _label="devils_advocate",
    )

    log.info(
        "Devil's Advocate done | stress_tests=%d | petitioner_strong=%d | respondent_strong=%d",
        len(result.stress_tests),
        len(result.strongest_issues_for_petitioner),
        len(result.strongest_issues_for_respondent),
    )
    return result
