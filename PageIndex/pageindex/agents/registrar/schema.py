"""
registrar/schema.py
~~~~~~~~~~~~~~~~~~~
Output schema for the Registrar Agent.

The Registrar produces an AdversarialMatrix — a neutral, side-by-side map
of every contested legal issue with both parties' positions documented.

FramedIssue is defined here because it is the Registrar's core output unit.
It flows through every downstream agent (Procedural, Devil's Advocate, Judge).

This object flows into:
  - Procedural Agent    (to check for procedural bars per issue)
  - Devil's Advocate    (to stress-test each cleared issue)
  - Judge Agent         (to adjudicate each cleared issue)
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class PartyStance(BaseModel):
    """One party's position on a single framed issue."""
    arguments:           list[str] = Field(description="Arguments made by this party on this issue.")
    supporting_citations: list[str] = Field(description="Citations invoked in support of these arguments.")


class FramedIssue(BaseModel):
    """
    A single contested legal issue, framed neutrally by the Registrar.

    The issue_id (e.g. I-1, I-2) is the provenance anchor that threads
    through all downstream agents — Procedural, Devil's Advocate, Judge.
    """
    issue_id:               str         = Field(description="Unique identifier, e.g. I-1, I-2, I-3.")
    neutral_issue_statement: str        = Field(description="The legal question stated without favouring either party.")
    petitioner_stance:       PartyStance
    respondent_stance:       PartyStance


class AdversarialMatrix(BaseModel):
    """
    Output of the Registrar Agent.

    Produced by:  Registrar Agent  (stage 3)
    Consumed by:  Procedural Agent (stage 4), Devil's Advocate (stage 5), Judge (stage 6)

    human_review_status is always set to 'pending' by the Registrar.
    A human must change it to 'approved' before the Judge Agent runs.
    """
    undisputed_background: list[str]      = Field(description="Facts agreed upon or not contested by either party.")
    framed_issues:         list[FramedIssue] = Field(description="All contested issues framed neutrally with both stances.")
    human_review_status:   Literal["pending", "approved", "rejected"] = Field(
        default="pending",
        description="Gate field. Judge will not run unless this is 'approved'.",
    )
