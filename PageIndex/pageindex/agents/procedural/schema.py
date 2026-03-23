"""
procedural/schema.py
~~~~~~~~~~~~~~~~~~~~
Output schema for the Procedural Agent.

The Procedural Agent screens every framed issue for procedural bars before
substantive adjudication. It produces a ProceduralAnalysis that separates
issues into two buckets: issues_to_proceed and issues_flagged.

Only issues in issues_to_proceed reach the Devil's Advocate and Judge.

This object flows into:
  - Devil's Advocate Agent  (receives only issues_to_proceed)
  - Judge Agent             (adjudicates only issues_to_proceed)
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ProceduralIssueFlag(BaseModel):
    """Procedural assessment for one framed issue."""
    issue_id:       str = Field(description="Must match an issue_id from the AdversarialMatrix.")
    procedural_bar: Literal["limitation", "jurisdiction", "standing", "res_judicata", "none"] = Field(
        description="Type of procedural bar found, or 'none' if the issue is clean.",
    )
    recommendation: Literal["drop", "reframe", "proceed"] = Field(
        description="drop = barred and cannot be cured; reframe = fixable; proceed = clean.",
    )
    severity:       Literal["fatal", "curable", "advisory"] = Field(
        description="fatal = cannot be cured; curable = fixable with reframing; advisory = note only.",
    )
    reasoning:      str = Field(description="Explanation for the flag and recommendation.")


class ProceduralAnalysis(BaseModel):
    """
    Output of the Procedural Agent — sifts the AdversarialMatrix for procedural bars.

    Produced by:  Procedural Agent   (stage 4)
    Consumed by:  Devil's Advocate   (stage 5) — receives only issues_to_proceed
                  Judge Agent        (stage 6) — adjudicates only issues_to_proceed
    """
    jurisdiction_finding: Literal["maintainable", "not_maintainable", "unclear"] = Field(
        description="Overall assessment of whether the court has jurisdiction.",
    )
    jurisdiction_reasoning: str = Field(description="Brief reasoning behind the jurisdiction finding.")

    limitation_finding: Literal["within_time", "barred", "unclear"] = Field(
        description="Whether the claim appears filed within the limitation period.",
    )
    limitation_reasoning: str = Field(description="Brief reasoning behind the limitation finding.")

    standing_finding: Literal["established", "not_established", "unclear"] = Field(
        description="Whether the petitioner has locus standi.",
    )
    standing_reasoning: str = Field(description="Brief reasoning behind the standing finding.")

    issue_flags:       list[ProceduralIssueFlag] = Field(
        default_factory=list,
        description="Per-issue flags. Issues not listed here are implicitly clean.",
    )
    issues_to_proceed: list[str] = Field(
        description="issue_ids that are procedurally clean — passed to Devil's Advocate and Judge.",
    )
    issues_flagged:    list[str] = Field(
        default_factory=list,
        description="issue_ids that have procedural bars (drop or reframe recommended).",
    )
