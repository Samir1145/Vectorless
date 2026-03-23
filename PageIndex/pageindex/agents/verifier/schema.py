"""
verifier/schema.py
~~~~~~~~~~~~~~~~~~
Output schema for the Verifier Agent.

The Verifier audits a StandardizedPartySubmission (from the Clerk)
against the original document text and produces a confidence-scored report.

This object flows into:
  - Registrar Agent (optional audit context for issue framing)
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class CitationAuditEntry(BaseModel):
    """Audit result for one citation from the Clerk's submission."""
    citation:          str           = Field(description="The citation string being audited.")
    found_in_page_text: bool         = Field(description="True if this citation was located in the source document.")
    exact_quote:       Optional[str] = Field(default=None, description="Verbatim passage found in source, if any.")


class VerifierFlag(BaseModel):
    """A specific problem found in the Clerk's submission."""
    flag_type: Literal[
        "unsupported_fact",
        "internal_contradiction",
        "citation_not_found",
        "prayer_without_basis",
        "overstated_claim",
    ] = Field(description="Category of the issue found.")
    severity:       Literal["error", "warning"] = Field(
        description="error = likely false or fabricated; warning = needs human attention.",
    )
    affected_field: str = Field(description="Which field triggered this flag, e.g. 'extracted_facts[2]'.")
    description:    str = Field(description="Clear explanation of what is wrong.")


class VerifiedPartySubmission(BaseModel):
    """
    Output of the Verifier Agent — audit of one StandardizedPartySubmission.

    Produced by:  Verifier Agent  (stage 2)
    Consumed by:  Registrar Agent (stage 3) — as optional audit context
    """
    overall_confidence:     float              = Field(
        description="0.0 = very unreliable; 1.0 = fully corroborated. Most documents score 0.6–0.9.",
    )
    flags:                  list[VerifierFlag]      = Field(default_factory=list, description="Issues found. Empty = clean.")
    citation_audit:         list[CitationAuditEntry] = Field(default_factory=list, description="One entry per citation.")
    internal_contradictions: list[str]              = Field(
        default_factory=list,
        description="Plain-language descriptions of cases where the party contradicts itself.",
    )
