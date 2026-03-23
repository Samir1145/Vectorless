"""
drafter/agent.py
~~~~~~~~~~~~~~~~
Drafting Agent — Stage 7 (final stage) of the Adversarial Pipeline.

Converts the Judge's structured DraftCourtOrder into jurisdiction-appropriate
formal court order prose. Handles Indian High Court, Supreme Court, and
District Court formatting conventions.

Uses the fast model tier at temperature=0.2 — formatting is well-defined
but slight variation produces more natural-sounding prose.

Public API:
    run_drafter(model, case_title, forum, jurisdiction_style, draft_order)
        -> FormalCourtOrder
"""

from __future__ import annotations

import logging
from pathlib import Path

from .schema import FormalCourtOrder
from ..judge.schema import DraftCourtOrder
from ...shared.llm import (
    _chat,
    _agent_temperature,
    _resolve_model,
    load_file_prompt,
    load_skills_file,
)

log = logging.getLogger(__name__)

_DIR = Path(__file__).parent   # pageindex/agents/drafter/


def run_drafter(
    model: str,
    case_title: str,
    forum: str,
    jurisdiction_style: str,
    draft_order: DraftCourtOrder,
) -> FormalCourtOrder:
    """
    Format a DraftCourtOrder into jurisdiction-appropriate court order prose.

    Args:
        model:               LiteLLM model string (fallback if config tier absent)
        case_title:          Human-readable case name
        forum:               Court name/location, e.g. "High Court of Delhi at New Delhi"
        jurisdiction_style:  "indian_high_court" | "supreme_court" | "district_court" | "custom"
        draft_order:         The Judge Agent's DraftCourtOrder

    Returns:
        FormalCourtOrder with cause title, coram, body, operative portion, and signature.
    """
    _model = _resolve_model("fast", model)
    _temp  = _agent_temperature("drafter")   # 0.2 — slight variation for natural prose

    log.info(
        "Drafting Agent | case=%r | forum=%r | style=%s | model=%s | temp=%.1f",
        case_title, forum, jurisdiction_style, _model, _temp,
    )

    prompt = load_file_prompt(
        _DIR / "task.md",
        case_title=case_title,
        forum=forum,
        jurisdiction_style=jurisdiction_style,
        draft_order_json=draft_order.model_dump_json(indent=2),
    )

    result: FormalCourtOrder = _chat(
        _model, prompt, FormalCourtOrder,
        system=load_skills_file(_DIR / "skills.md"),
        temperature=_temp,
        _label="drafter",
    )

    log.info(
        "Drafting Agent done | style=%s | body=%d chars | operative=%d chars",
        result.jurisdiction_style,
        len(result.body),
        len(result.operative_portion),
    )
    return result
