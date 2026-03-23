"""
citation_auditor/agent.py
~~~~~~~~~~~~~~~~~~~~~~~~~
Stage 2.5 — Citation Auditor Agent.

For every case citation extracted by the Clerk, this agent:
  1. Queries the Indian Kanoon API to verify the citation exists.
  2. If found, calls a small LLM to compare the party's characterisation
     of the case against the actual holding in the retrieved snippet.
  3. Returns a CitationAuditReport that flows into the Registrar and Judge.

Non-fatal: any single lookup failure is caught and marked 'unverified'.
The entire agent is skipped gracefully if INDIAN_KANOON_API_KEY is not set.
"""

from __future__ import annotations

import json
import logging
import os
import re
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional

from pydantic import BaseModel

from ..clerk.schema import CitedLaw, StandardizedPartySubmission
from ...shared.llm import (
    _agent_temperature,
    _chat,
    _resolve_model,
    load_file_prompt,
    load_skills_file,
)
from .schema import CitationAuditReport, CitationCheckResult

log = logging.getLogger(__name__)
_DIR = Path(__file__).parent

_IK_BASE    = "https://api.indiankanoon.org"
_IK_TIMEOUT = 10   # seconds per API call

# ── Indian law case citation markers ─────────────────────────────────────────
# A citation string containing any of these is treated as a CASE citation.
_CASE_MARKERS = re.compile(
    r"\bAIR\b|\bSCC\b|\bSCR\b|\bManu\b|\bBLJR\b|\bAll LJ\b|"
    r"\bCal LJ\b|\bBom LR\b|\bSLJ\b|\bCLT\b|\bSCJ\b|\bALD\b|"
    r"\bv\.\s|\bvs\.\s|\bvs\s",
    re.IGNORECASE,
)
# Strings that are definitely statutes/rules, not cases.
_STATUTE_MARKERS = re.compile(
    r"\bSection\b|\bArticle\b|\bRule\b|\bOrder\b|\bSchedule\b|"
    r"\bAct,\b|\bAct\b|\bRules,\b|\bConstitution\b|\bCode\b",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ik_api_key() -> str:
    return os.environ.get("INDIAN_KANOON_API_KEY", "").strip()


def _is_case_citation(citation: str) -> bool:
    """Return True if the citation looks like a case, not a statute/rule."""
    if _STATUTE_MARKERS.search(citation):
        return False
    return bool(_CASE_MARKERS.search(citation))


def _lookup_indian_kanoon(citation: str) -> Optional[dict]:
    """
    Query Indian Kanoon for a citation.

    Returns a dict with keys: found, doc_id, title, headline, court, date, url
    Returns None on network/API failure (caller should mark as unverified).
    """
    key = _ik_api_key()
    if not key:
        return None
    try:
        body = urllib.parse.urlencode({"formInput": citation, "pagenum": "0"}).encode()
        req  = urllib.request.Request(
            f"{_IK_BASE}/search/",
            data=body,
            headers={
                "Authorization": f"Token {key}",
                "Content-Type":  "application/x-www-form-urlencoded",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=_IK_TIMEOUT) as resp:
            data = json.loads(resp.read())

        docs = data.get("docs", [])
        if not docs:
            return {"found": False}

        top = docs[0]
        doc_id = top.get("tid", "")
        return {
            "found":    True,
            "doc_id":   doc_id,
            "title":    top.get("title", ""),
            "headline": top.get("headline", ""),
            "court":    top.get("docsource", ""),
            "date":     top.get("publishdate", ""),
            "url":      f"https://indiankanoon.org/doc/{doc_id}/" if doc_id else None,
        }
    except Exception as exc:
        log.warning("Indian Kanoon lookup failed | citation='%s' | %s", citation, exc)
        return None


class _HoldingComparison(BaseModel):
    actual_holding:          str
    claimed_holding_matches: Optional[bool] = None
    discrepancy_note:        Optional[str]  = None
    is_overruled:            Optional[bool] = None


def _compare_holding(
    model:      str,
    citation:   str,
    case_info:  dict,
    party_claims: list[str],
) -> _HoldingComparison:
    """Call the LLM to compare the party's characterisation against the actual case."""
    party_claims_text = (
        "\n".join(f"- {c}" for c in party_claims)
        if party_claims else
        "(No specific factual claims reference this citation directly.)"
    )
    prompt = load_file_prompt(
        _DIR / "task.md",
        citation      = citation,
        case_title    = case_info.get("title", "Unknown"),
        court         = case_info.get("court", "Unknown"),
        decision_date = case_info.get("date",  "Unknown"),
        actual_snippet= case_info.get("headline", "(no snippet available)"),
        party_claims  = party_claims_text,
    )
    return _chat(
        model, prompt, _HoldingComparison,
        system      = load_skills_file(_DIR / "skills.md"),
        temperature = _agent_temperature("citation_auditor"),
        _label      = "citation_auditor",
    )


def _find_related_claims(citation_str: str, submission: StandardizedPartySubmission) -> list[str]:
    """Return fact statements from the submission that reference this citation."""
    cit_lower = citation_str.lower()
    return [
        f.statement for f in submission.extracted_facts
        if cit_lower in f.statement.lower()
    ]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_citation_auditor(
    model:       str,
    submissions: dict[str, StandardizedPartySubmission],
) -> CitationAuditReport:
    """
    Verify all case citations across both parties' Clerk outputs.

    Args:
        model:       LiteLLM model string (fallback if config tier absent).
        submissions: Dict mapping party_role → StandardizedPartySubmission.

    Returns:
        CitationAuditReport with per-citation results and aggregate stats.
    """
    _model = _resolve_model("fast", model)
    ik_key = _ik_api_key()
    ik_available = bool(ik_key)

    if not ik_available:
        log.info(
            "Citation Auditor | INDIAN_KANOON_API_KEY not set — "
            "all citations will be marked unverified. "
            "Set the key in .env to enable external verification."
        )

    results: list[CitationCheckResult] = []

    for party_role, submission in submissions.items():
        for cited_law in submission.cited_laws_and_cases:
            cit_str = cited_law.citation
            is_case = _is_case_citation(cit_str)

            if not is_case:
                # Statute / rule — no external lookup needed
                results.append(CitationCheckResult(
                    citation            = cit_str,
                    party_role          = party_role,
                    is_case_citation    = False,
                    found               = None,
                    verification_method = "unverified",
                    note                = "Statute or rule — external lookup not applicable.",
                ))
                continue

            # ── External lookup ───────────────────────────────────────────
            if not ik_available:
                results.append(CitationCheckResult(
                    citation            = cit_str,
                    party_role          = party_role,
                    is_case_citation    = True,
                    found               = None,
                    verification_method = "unverified",
                    note                = "INDIAN_KANOON_API_KEY not configured.",
                ))
                continue

            ik_result = _lookup_indian_kanoon(cit_str)

            if ik_result is None:
                # Network/API failure
                results.append(CitationCheckResult(
                    citation            = cit_str,
                    party_role          = party_role,
                    is_case_citation    = True,
                    found               = None,
                    verification_method = "unverified",
                    note                = "Indian Kanoon API call failed.",
                ))
                continue

            if not ik_result.get("found"):
                results.append(CitationCheckResult(
                    citation            = cit_str,
                    party_role          = party_role,
                    is_case_citation    = True,
                    found               = False,
                    verification_method = "indian_kanoon",
                    note                = "Citation not found in Indian Kanoon database.",
                ))
                continue

            # ── LLM holding comparison ────────────────────────────────────
            related_claims = _find_related_claims(cit_str, submission)
            try:
                comparison = _compare_holding(
                    model         = _model,
                    citation      = cit_str,
                    case_info     = ik_result,
                    party_claims  = related_claims,
                )
                results.append(CitationCheckResult(
                    citation                = cit_str,
                    party_role              = party_role,
                    is_case_citation        = True,
                    found                   = True,
                    source_url              = ik_result.get("url"),
                    case_title              = ik_result.get("title"),
                    court                   = ik_result.get("court"),
                    decision_date           = ik_result.get("date"),
                    actual_holding          = comparison.actual_holding,
                    claimed_holding_matches = comparison.claimed_holding_matches,
                    discrepancy_note        = comparison.discrepancy_note,
                    is_overruled            = comparison.is_overruled,
                    verification_method     = "indian_kanoon",
                ))
            except Exception as exc:
                log.warning(
                    "Citation Auditor | LLM comparison failed | citation='%s' | %s",
                    cit_str, exc,
                )
                results.append(CitationCheckResult(
                    citation            = cit_str,
                    party_role          = party_role,
                    is_case_citation    = True,
                    found               = True,
                    source_url          = ik_result.get("url"),
                    case_title          = ik_result.get("title"),
                    court               = ik_result.get("court"),
                    decision_date       = ik_result.get("date"),
                    verification_method = "indian_kanoon",
                    note                = f"Found in database but LLM comparison failed: {exc}",
                ))

    # ── Aggregate stats ───────────────────────────────────────────────────────
    case_results      = [r for r in results if r.is_case_citation]
    total_found       = sum(1 for r in case_results if r.found is True)
    total_not_found   = sum(1 for r in case_results if r.found is False)
    total_misrep      = sum(1 for r in case_results if r.claimed_holding_matches is False)
    total_unverified  = sum(1 for r in case_results if r.verification_method == "unverified")

    log.info(
        "Citation Auditor done | case_citations=%d | found=%d | not_found=%d | "
        "misrepresented=%d | unverified=%d",
        len(case_results), total_found, total_not_found, total_misrep, total_unverified,
    )

    return CitationAuditReport(
        results                = results,
        indian_kanoon_available= ik_available,
        total_case_citations   = len(case_results),
        total_found            = total_found,
        total_not_found        = total_not_found,
        total_misrepresented   = total_misrep,
        total_unverified       = total_unverified,
    )
