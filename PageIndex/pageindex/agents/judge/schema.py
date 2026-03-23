"""
judge/schema.py
~~~~~~~~~~~~~~~
Output schemas for the Judge Agent.

The Judge runs in two phases:
  1. run_judge_on_issue() — one IRAC call per issue → ReasonedDecision
  2. run_judge_final_order() — synthesises all decisions → DraftCourtOrder

DraftCourtOrder is the Judge's final product. It flows into the Drafter Agent.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ReasonedDecision(BaseModel):
    """
    IRAC analysis for a single framed issue.

    Produced by: Judge Agent per-issue call
    Aggregated into: DraftCourtOrder
    """
    issue_id:        str = Field(description="Must match an issue_id from the AdversarialMatrix.")
    issue_statement: str
    rule: str = Field(
        description="IRAC — Rule: the applicable law, statute, or binding precedent.",
    )
    analysis: str = Field(
        description="IRAC — Application: reasoning weighing both parties' arguments and citations.",
    )
    conclusion: str = Field(
        description="IRAC — Conclusion: specific ruling, e.g. 'Issue I-1 decided in favour of the Petitioner.'",
    )


class DraftCourtOrder(BaseModel):
    """
    Final output of the Judge Agent — synthesised from all per-issue ReasonedDecisions.

    Produced by:  Judge Agent  (stage 6)
    Consumed by:  Drafter Agent (stage 7) — to be formatted into formal prose
    """
    case_title:        str                   = Field(description="The human-readable case name.")
    background_facts:  str                   = Field(description="Cohesive narrative of undisputed facts.")
    reasoned_decisions: list[ReasonedDecision] = Field(description="One entry per adjudicated issue.")
    final_order:       str                   = Field(
        description="Ultimate disposition: 'The petition is allowed/dismissed...' with key directions.",
    )
