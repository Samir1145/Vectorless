"""
drafter/schema.py
~~~~~~~~~~~~~~~~~
Output schema for the Drafting Agent.

The Drafter converts the Judge's structured DraftCourtOrder into
jurisdiction-appropriate formal court order prose.

This is the terminal output of the pipeline — the FormalCourtOrder
is what gets saved, displayed in the UI, and exported.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class FormalCourtOrder(BaseModel):
    """
    Output of the Drafting Agent — fully formatted court order.

    Produced by:  Drafter Agent  (stage 7 — final stage)
    Consumed by:  Frontend UI display + text export
    """
    jurisdiction_style: str = Field(
        default="indian_high_court",
        description="Style applied: indian_high_court | supreme_court | district_court | custom.",
    )
    cause_title: str = Field(
        description="Full heading block, e.g. 'IN THE HIGH COURT OF JUDICATURE AT DELHI\\nWRIT PETITION (CIVIL) NO. ___ OF ____'",
    )
    coram: str = Field(
        description="Bench composition line, e.g. 'HON\\'BLE MR. JUSTICE A.K. SHARMA'.",
    )
    date: str = Field(
        description="Date formatted per jurisdiction convention, e.g. '22nd March, 2026'.",
    )
    petitioner_counsel: Optional[str] = Field(
        default=None,
        description="Counsel appearing for the Petitioner, if known.",
    )
    respondent_counsel: Optional[str] = Field(
        default=None,
        description="Counsel appearing for the Respondent, if known.",
    )
    body: str = Field(
        description="Full prose order body: appearances, recitals, reasoning summary, operative clauses.",
    )
    operative_portion: str = Field(
        description="Just the operative clauses — the actual orders — extracted separately for quick reference.",
    )
    signature_block: str = Field(
        description="Closing block, e.g. 'Sd/-\\n(A.K. SHARMA)\\nJUDGE'.",
    )
