"""
note_builder/schema.py
~~~~~~~~~~~~~~~~~~~~~~
Output schema for the Note Builder Agent.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class NoteEntry(BaseModel):
    """A single structured note anchored to a document section."""
    node_id:    Optional[str] = Field(
        default=None,
        description="PageIndex node_id of the section this note refers to (e.g. '0003'). Use the [§ node_id] marker from the document text.",
    )
    page_index: int = Field(
        description="1-based page number where this note's content appears.",
    )
    anchor_title: str = Field(
        default='',
        description="Title of the section this note is anchored to, as it appears in the document tree.",
    )
    note_type: Literal['summary', 'flag', 'quote', 'cross_ref'] = Field(
        description=(
            "Type of note: "
            "'summary' = concise summary of a section's legal significance; "
            "'flag' = legally significant admission, contradiction, or risk; "
            "'quote' = verbatim passage worth preserving; "
            "'cross_ref' = observation that references another section or the opposing party."
        ),
    )
    body: str = Field(
        description="The note content. For 'quote' type, include the exact quoted text. For 'flag', explain the significance clearly.",
    )
    severity: Optional[Literal['low', 'medium', 'high']] = Field(
        default=None,
        description="Severity level — only for 'flag' type notes. Omit for other types.",
    )


class GeneratedNotes(BaseModel):
    """Output of the Note Builder Agent — structured notes for an entire document."""
    notes: list[NoteEntry] = Field(
        description="All generated notes, ordered by page_index.",
    )
