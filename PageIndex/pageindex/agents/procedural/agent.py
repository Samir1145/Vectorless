"""
procedural/agent.py
~~~~~~~~~~~~~~~~~~~
Procedural Agent — Stage 4 of the Adversarial Pipeline.

Screens the AdversarialMatrix for procedural bars (jurisdiction, limitation,
standing, res judicata) before substantive adjudication begins.

Only issues in ProceduralAnalysis.issues_to_proceed reach stages 5 and 6.

Public API:
    run_procedural_agent(model, case_title, matrix) -> ProceduralAnalysis
"""

from __future__ import annotations

import logging
from pathlib import Path

from .schema import ProceduralAnalysis
from ..registrar.schema import AdversarialMatrix
from ...shared.llm import (
    _chat,
    _agent_temperature,
    _resolve_model,
    load_file_prompt,
    load_skills_file,
)

log = logging.getLogger(__name__)

_DIR = Path(__file__).parent   # pageindex/agents/procedural/


def run_procedural_agent(
    model: str,
    case_title: str,
    matrix: AdversarialMatrix,
) -> ProceduralAnalysis:
    """
    Screen the AdversarialMatrix for procedural bars.

    Args:
        model:      LiteLLM model string (fallback if config tier absent)
        case_title: Human-readable case name
        matrix:     The AdversarialMatrix from the Registrar Agent

    Returns:
        ProceduralAnalysis with per-issue flags and issues_to_proceed list.
    """
    _model = _resolve_model("balanced", model)
    _temp  = _agent_temperature("procedural")

    log.info("Procedural Agent | case=%r | issues=%d | model=%s", case_title, len(matrix.framed_issues), _model)

    prompt = load_file_prompt(
        _DIR / "task.md",
        case_title=case_title,
        adversarial_matrix_json=matrix.model_dump_json(indent=2),
    )

    result: ProceduralAnalysis = _chat(
        _model, prompt, ProceduralAnalysis,
        system=load_skills_file(_DIR / "skills.md"),
        temperature=_temp,
        _label="procedural",
    )

    log.info(
        "Procedural Agent done | jurisdiction=%s | issues_to_proceed=%d | issues_flagged=%d",
        result.jurisdiction_finding,
        len(result.issues_to_proceed),
        len(result.issues_flagged),
    )
    return result
