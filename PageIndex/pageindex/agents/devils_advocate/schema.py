"""
devils_advocate/schema.py
~~~~~~~~~~~~~~~~~~~~~~~~~
Output schema for the Devil's Advocate Agent.

The Devil's Advocate stress-tests every procedurally cleared issue by finding
the strongest possible counter-argument to each party's stance.

This is the last stage before the human review gate — the reviewer sees
both the AdversarialMatrix AND this vulnerability analysis before deciding
whether to approve the matrix for the Judge.

This object flows into:
  - Human review gate  (reviewer sees this before approving/rejecting)
  - Judge Agent        (indirectly — via the approved matrix, not directly consumed)
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class IssueVulnerability(BaseModel):
    """The strongest counter-argument against one party's stance on one issue."""
    strongest_counter: str = Field(
        description="The single strongest argument that could defeat this party's stance.",
    )
    weakness_type: Literal["factual_gap", "citation_stretch", "logical_leap", "unsupported_prayer"] = Field(
        description="Primary category of vulnerability.",
    )
    severity: Literal["high", "medium", "low"] = Field(
        description="high = likely to lose on this issue; medium = contestable; low = minor.",
    )
    suggested_reframe: Optional[str] = Field(
        default=None,
        description="If reframing the issue statement would help this party, suggest how.",
    )


class IssueStressTest(BaseModel):
    """Full stress-test for one framed issue — vulnerabilities for both sides."""
    issue_id:               str              = Field(description="Must match an issue_id from issues_to_proceed.")
    petitioner_vulnerability: IssueVulnerability = Field(description="Strongest counter to the Petitioner's stance.")
    respondent_vulnerability:  IssueVulnerability = Field(description="Strongest counter to the Respondent's stance.")
    balance_assessment:    Literal["petitioner_stronger", "respondent_stronger", "balanced", "unclear"] = Field(
        description="Which side has the stronger position on this issue.",
    )


class StressTestedMatrix(BaseModel):
    """
    Output of the Devil's Advocate Agent.

    Produced by:  Devil's Advocate Agent  (stage 5)
    Consumed by:  Human reviewer — approves or rejects before Judge runs
    """
    stress_tests:                    list[IssueStressTest] = Field(description="One stress test per issue in issues_to_proceed.")
    strongest_issues_for_petitioner: list[str]             = Field(description="issue_ids where Petitioner clearly has the stronger position.")
    strongest_issues_for_respondent: list[str]             = Field(description="issue_ids where Respondent clearly has the stronger position.")
    most_contested_issues:           list[str]             = Field(description="issue_ids where neither side clearly dominates.")
    reviewer_note:                   str                   = Field(
        description="2–4 sentence advisory to the human reviewer: what to verify, reframe, or reconsider.",
    )
