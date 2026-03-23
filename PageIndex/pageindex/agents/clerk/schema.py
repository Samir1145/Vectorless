"""
clerk/schema.py
~~~~~~~~~~~~~~~
Output schema for the Clerk Agent.

The Clerk produces one StandardizedPartySubmission per party document.
This object flows into:
  - Verifier Agent  (to be audited)
  - Registrar Agent (to be aligned with the opposing party's submission)
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class ExtractedFact(BaseModel):
    """A single factual claim made by the party, anchored to a page and document section."""
    statement:  str           = Field(description="The factual claim as a clear, standalone sentence.")
    page_index: int           = Field(description="1-based page number where this fact appears.")
    node_id:    Optional[str] = Field(
        default=None,
        description=(
            "PageIndex node_id of the section where this fact appears (e.g. '0003'). "
            "Populate from the [§ node_id | ...] marker in the document text. "
            "Null if the document was not processed through PageIndex."
        ),
    )
    verified:   bool = Field(
        default=False,
        description="True only if this statement appears verbatim or near-verbatim in the source text.",
    )


class CitedLaw(BaseModel):
    """A statute, rule, or case citation referenced by the party."""
    citation:   str           = Field(description="Citation exactly as written in the document.")
    page_index: Optional[int] = Field(default=None, description="Page where this citation appears.")
    verified:   bool          = Field(
        default=False,
        description="True if this citation string was found in the source document text.",
    )


class StandardizedPartySubmission(BaseModel):
    """
    Output of the Clerk Agent — one instance per party document.

    Produced by:  Clerk Agent  (stage 1)
    Consumed by:  Verifier Agent (stage 2) + Registrar Agent (stage 3)
    """
    party_role: Literal["Petitioner", "Respondent"] = Field(
        description="Which party filed this document.",
    )
    document_type: Literal["Petition", "Reply", "Rejoinder", "Affidavit", "Exhibit", "Other"] = Field(
        default="Petition",
        description="Type of legal document.",
    )
    extracted_facts:      list[ExtractedFact] = Field(description="Key factual claims, each anchored to a page.")
    issues_raised:        list[str]           = Field(description="Legal issues explicitly raised by this party.")
    cited_laws_and_cases: list[CitedLaw]      = Field(description="All statutes, rules, and case citations referenced.")
    prayers:              list[str]           = Field(description="Specific reliefs sought by this party.")
