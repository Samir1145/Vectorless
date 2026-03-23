"""
citation_auditor/schema.py
~~~~~~~~~~~~~~~~~~~~~~~~~~
Output schema for the Citation Auditor Agent (Stage 2.5).
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class CitationCheckResult(BaseModel):
    """Verification result for a single citation from one party."""

    citation:    str                            = Field(description="Citation exactly as extracted by the Clerk.")
    party_role:  Literal["Petitioner", "Respondent"] = Field(description="Party who cited this.")

    is_case_citation: bool = Field(
        description="True if this looks like a case citation (AIR, SCC, SCR…). "
                    "False for statutes/rules/articles — those are not checked externally.",
    )

    # ── External lookup result ────────────────────────────────────────────────
    found:               Optional[bool] = Field(default=None, description="Whether the citation was found in Indian Kanoon. None if not checked.")
    source_url:          Optional[str]  = Field(default=None, description="Indian Kanoon URL for the case.")
    case_title:          Optional[str]  = Field(default=None, description="Full case name as found in the database.")
    court:               Optional[str]  = Field(default=None, description="Court that decided this case.")
    decision_date:       Optional[str]  = Field(default=None, description="Date of the judgment.")

    # ── LLM comparison result (only if found=True and party cited it for a proposition) ──
    actual_holding:          Optional[str]  = Field(default=None, description="One-sentence summary of what this case actually held, from the retrieved text.")
    claimed_holding_matches: Optional[bool] = Field(default=None, description="True if the party's claims referencing this case accurately reflect the actual holding. None if no related claims found.")
    discrepancy_note:        Optional[str]  = Field(default=None, description="If claimed_holding_matches is False, brief description of the discrepancy.")
    is_overruled:            Optional[bool] = Field(default=None, description="True if the case appears to have been overruled or significantly limited.")

    verification_method: Literal["indian_kanoon", "unverified"] = Field(
        description="'indian_kanoon' if external lookup succeeded; 'unverified' if API was unavailable or call failed.",
    )
    note: Optional[str] = Field(default=None, description="Any additional context (e.g. lookup error, reason not checked).")


class CitationAuditReport(BaseModel):
    """
    Output of the Citation Auditor Agent — covers all citations from all parties.

    Produced by:  Citation Auditor Agent (stage 2.5)
    Consumed by:  Registrar Agent (stage 3) context, Judge Agent (stage 6) context,
                  Human reviewer at Devil's Advocate gate
    """

    results:               list[CitationCheckResult] = Field(description="One entry per case citation extracted across both parties.")
    indian_kanoon_available: bool  = Field(description="Whether the Indian Kanoon API key was configured and reachable.")

    # ── Aggregate statistics ──────────────────────────────────────────────────
    total_case_citations:  int = Field(description="Total case citations checked (excludes statutes/rules).")
    total_found:           int = Field(description="Citations found in Indian Kanoon.")
    total_not_found:       int = Field(description="Case citations not found (possible fabrication or citation error).")
    total_misrepresented:  int = Field(description="Citations found but where party's characterisation does not match the actual holding.")
    total_unverified:      int = Field(description="Citations that could not be verified due to API unavailability.")
