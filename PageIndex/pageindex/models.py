"""
models.py
~~~~~~~~~
Pydantic models for the three agent contracts.
These are the canonical data structures that flow through the pipeline.

  Document(s)
      ↓ Clerk Agent
  StandardizedPartySubmission   (one per party document)
      ↓ Registrar Agent
  AdversarialMatrix             (human review gate here)
      ↓ Judge Agent (one call per issue)
  DraftCourtOrder
"""

from __future__ import annotations
from typing import Literal, Optional
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Clerk Agent output
# ---------------------------------------------------------------------------

class ExtractedFact(BaseModel):
    statement: str = Field(description="A factual statement made by the party.")
    page_index: int = Field(description="Page number in the source document where this fact appears.")
    verified: bool = Field(
        default=False,
        description="True if this statement was found verbatim or near-verbatim in the source text."
    )


class CitedLaw(BaseModel):
    citation: str = Field(description="Statute, rule, or case citation as written in the document.")
    page_index: Optional[int] = Field(default=None, description="Page number where this citation appears.")
    verified: bool = Field(
        default=False,
        description="True if this citation string was found in the source document text."
    )


class StandardizedPartySubmission(BaseModel):
    """Output of the Clerk Agent. One instance per party document."""
    party_role: Literal["Petitioner", "Respondent"] = Field(
        description="Which party filed this document."
    )
    document_type: Literal["Petition", "Reply", "Rejoinder", "Affidavit", "Exhibit", "Other"] = Field(
        default="Petition",
        description="Type of legal document."
    )
    extracted_facts: list[ExtractedFact] = Field(
        description="Key factual statements extracted from the document, each anchored to a page."
    )
    issues_raised: list[str] = Field(
        description="Legal issues explicitly raised by this party."
    )
    cited_laws_and_cases: list[CitedLaw] = Field(
        description="All statutes, rules, and case citations referenced."
    )
    prayers: list[str] = Field(
        description="Specific reliefs sought by this party."
    )


# ---------------------------------------------------------------------------
# Registrar Agent output
# ---------------------------------------------------------------------------

class PartyStance(BaseModel):
    arguments: list[str] = Field(description="Arguments made by this party on this issue.")
    supporting_citations: list[str] = Field(description="Citations invoked in support of these arguments.")


class FramedIssue(BaseModel):
    issue_id: str = Field(description="Unique identifier e.g. I-1, I-2.")
    neutral_issue_statement: str = Field(
        description="The legal issue stated neutrally, without favouring either party."
    )
    petitioner_stance: PartyStance
    respondent_stance: PartyStance


class AdversarialMatrix(BaseModel):
    """Output of the Registrar Agent. Human must approve before Judge runs."""
    undisputed_background: list[str] = Field(
        description="Facts agreed upon by both parties or not contested."
    )
    framed_issues: list[FramedIssue] = Field(
        description="All legal issues framed neutrally with both stances mapped."
    )
    human_review_status: Literal["pending", "approved", "rejected"] = Field(
        default="pending",
        description="Gate field. Judge Agent will not run unless this is 'approved'."
    )


# ---------------------------------------------------------------------------
# Judge Agent output (one per issue, aggregated into DraftCourtOrder)
# ---------------------------------------------------------------------------

class ReasonedDecision(BaseModel):
    """IRAC analysis for a single framed issue."""
    issue_id: str = Field(description="Must match an issue_id from the AdversarialMatrix.")
    issue_statement: str
    rule: str = Field(
        description="IRAC — Rule: the applicable law, statute, or precedent governing this issue."
    )
    analysis: str = Field(
        description="IRAC — Application: reasoning weighing petitioner vs respondent stances and citations."
    )
    conclusion: str = Field(
        description="IRAC — Conclusion: specific ruling e.g. 'Issue decided in favour of Petitioner'."
    )


class DraftCourtOrder(BaseModel):
    """Final output of the Judge Agent."""
    case_title: str
    background_facts: str = Field(
        description="Cohesive narrative synthesized from the undisputed background."
    )
    reasoned_decisions: list[ReasonedDecision] = Field(
        description="One entry per framed issue, produced sequentially."
    )
    final_order: str = Field(
        description="Ultimate disposition e.g. 'The petition is allowed / dismissed with costs'."
    )
