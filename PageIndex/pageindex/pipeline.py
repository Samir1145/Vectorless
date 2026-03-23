"""
pipeline.py
~~~~~~~~~~~
Orchestrates the full Adversarial Multi-Agent Synthesis Pipeline.

Flow:
  1. run_pipeline_clerk(case_id)
       Reads case documents from DB, runs Clerk Agent on each (in parallel
       threads), persists clerk outputs.  Status: clerk_running → clerk_done

  2. run_pipeline_verifier(case_id)
       Audits each clerk output against the source document text.
       Status: verifier_running → verifier_done

  3. run_pipeline_registrar(case_id)
       Reads clerk + verifier outputs, runs Registrar Agent, persists
       AdversarialMatrix.  Status: registrar_running → registrar_done

  4. run_pipeline_procedural(case_id)
       Sifts AdversarialMatrix for procedural bars, persists ProceduralAnalysis.
       Status: procedural_running → procedural_done

  5. run_pipeline_devils_advocate(case_id)
       Stress-tests procedurally cleared issues, persists StressTestedMatrix.
       Status: devils_advocate_running → review_pending
       *** Human review gate: must call approve_matrix() before step 6 ***

  6. run_pipeline_judge(case_id)
       Reads approved matrix, runs Judge IRAC per issue (sequential), persists
       DraftCourtOrder.  Status: judge_running → judge_done

  7. run_pipeline_drafter(case_id)
       Formats DraftCourtOrder into jurisdiction-appropriate prose.
       Status: drafter_running → complete

Each stage is called from a background thread in server.py (same pattern as
document processing).  Exceptions update the case status to "error".
"""

from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

import db
from pageindex.agents import (
    run_citation_auditor,
    run_clerk,
    run_devils_advocate,
    run_drafter,
    run_judge_final_order,
    run_judge_on_issue,
    run_procedural_agent,
    run_registrar,
    run_verifier,
)
# Schema imports now come from each agent's own schema.py
from pageindex.agents.registrar.schema import AdversarialMatrix
from pageindex.agents.registrar.schema import FramedIssue  # noqa: F401 (used by type hints below)
from pageindex.agents.judge.schema import DraftCourtOrder, ReasonedDecision
from pageindex.agents.clerk.schema import StandardizedPartySubmission
from pageindex.agents.verifier.schema import VerifiedPartySubmission
from pageindex.agents.procedural.schema import ProceduralAnalysis
from pageindex.agents.citation_auditor.schema import CitationAuditReport

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_document_text(doc_id: int | None) -> str:
    """Load all page texts for a document from DB and concatenate."""
    if doc_id is None:
        return ""
    with db.get_db() as conn:
        rows = conn.execute(
            "SELECT page_num, text FROM document_pages WHERE doc_id = ? ORDER BY page_num",
            (doc_id,),
        ).fetchall()
    if not rows:
        log.warning("No page text found in DB for doc_id=%s", doc_id)
        return ""
    return "\n\n".join(f"[Page {r['page_num']}]\n{r['text']}" for r in rows)


def _format_citation_audit_summary(audit_json: str | None) -> str:
    """
    Render a CitationAuditReport as a concise text summary for the Judge prompt.
    Returns empty string if no audit is available.
    """
    if not audit_json:
        return ""
    try:
        report = CitationAuditReport.model_validate_json(audit_json)
    except Exception:
        return ""

    if not report.results:
        return ""

    lines = [
        f"Total citations checked: {report.total_case_citations} | "
        f"Found: {report.total_found} | Not found: {report.total_not_found} | "
        f"Misrepresented: {report.total_misrepresented} | "
        f"Unverified: {report.total_unverified}",
        "",
    ]
    for r in report.results:
        if not r.is_case_citation:
            continue
        if not r.found:
            status = "NOT FOUND"
        elif r.claimed_holding_matches is False:
            status = "MISREPRESENTED"
        elif r.verification_method == "unverified":
            status = "UNVERIFIED"
        else:
            status = "VERIFIED"

        line = f"- [{status}] {r.citation} (relied on by {r.party_role})"
        if r.case_title:
            line += f" — {r.case_title}"
        lines.append(line)
        if r.discrepancy_note:
            lines.append(f"  Discrepancy: {r.discrepancy_note}")

    return "\n".join(lines)


def _format_tree_for_clerk(doc_id: int | None) -> str:
    """
    Render the PageIndex tree as enriched, section-marked text for the Clerk Agent.

    Each section is prefixed with a marker line:
        [§ 0003 | p.3-5 | GROUNDS OF APPEAL]
    followed by the section summary and the raw page text for those pages.

    The Clerk uses the § markers to populate `node_id` on each ExtractedFact,
    creating a provenance chain: fact → node_id → PageIndex section → page range.

    Falls back to plain page text if no tree nodes exist for this document.
    """
    if doc_id is None:
        return ""

    with db.get_db() as conn:
        nodes = conn.execute(
            """SELECT node_id, title, summary, start_page, end_page, level
               FROM tree_nodes
               WHERE doc_id = ?
               ORDER BY start_page, level""",
            (doc_id,),
        ).fetchall()

        pages = conn.execute(
            "SELECT page_num, text FROM document_pages WHERE doc_id = ? ORDER BY page_num",
            (doc_id,),
        ).fetchall()

    if not nodes:
        # No tree — fall back to plain page text
        if not pages:
            log.warning("No page text found in DB for doc_id=%s", doc_id)
            return ""
        return "\n\n".join(f"[Page {r['page_num']}]\n{r['text']}" for r in pages)

    # Build a lookup: page_num → text
    page_text: dict[int, str] = {r["page_num"]: r["text"] for r in pages}

    sections: list[str] = []
    for node in nodes:
        nid        = node["node_id"] or "?"
        title      = node["title"]  or "(untitled)"
        summary    = node["summary"] or ""
        start_page = node["start_page"] or 0
        end_page   = node["end_page"]   or start_page
        indent     = "  " * (node["level"] or 0)

        page_range = f"p.{start_page}" if start_page == end_page else f"p.{start_page}-{end_page}"
        header = f"{indent}[§ {nid} | {page_range} | {title}]"

        block_parts = [header]
        if summary:
            block_parts.append(f"{indent}Summary: {summary}")

        # Append the raw text for each page in this node's range
        for pnum in range(start_page, end_page + 1):
            txt = page_text.get(pnum, "").strip()
            if txt:
                block_parts.append(f"{indent}[Page {pnum}]\n{txt}")

        sections.append("\n".join(block_parts))

    return "\n\n".join(sections)


# ---------------------------------------------------------------------------
# Stage 1 — Clerk
# ---------------------------------------------------------------------------

def run_pipeline_clerk(case_id: int):
    """
    Run Clerk Agent on every case document in parallel.
    Updates DB with clerk outputs and advances case status.
    """
    log.info("Pipeline CLERK START | case_id=%s", case_id)
    db.update_case_status(case_id, "clerk_running")

    case = db.get_case(case_id)
    model = case["model"]
    case_docs = db.get_case_documents(case_id)

    if not case_docs:
        db.update_case_status(case_id, "error", "No documents attached to this case.")
        raise ValueError(f"Case {case_id} has no documents.")

    errors = []

    def _clerk_then_verify(cd: dict):
        """Run Clerk then immediately chain Verifier in the same thread."""
        # ── Clerk (uses tree-enriched text for section provenance) ─────────
        db.set_clerk_status(cd["id"], "running")
        try:
            # Prefer tree-formatted text so Clerk can populate node_id on each fact.
            # Falls back to plain page text automatically if no tree exists.
            clerk_text = _format_tree_for_clerk(cd["doc_id"])
            submission = run_clerk(
                model=model,
                party_role=cd["party_role"],
                document_type=cd["document_type"],
                document_text=clerk_text,
            )
            db.save_clerk_output(cd["id"], submission.model_dump_json())
        except Exception as exc:
            db.set_clerk_status(cd["id"], "error")
            log.error("Clerk error | case_doc_id=%s | %s", cd["id"], exc)
            return cd["id"], str(exc)

        # ── Verifier (uses raw page text for verbatim citation checking) ───
        # Verifier needs the raw source text to verify quotes verbatim; the
        # tree-formatted text may wrap text inside section blocks and confuse
        # verbatim matching. Load raw pages separately.
        db.set_verifier_status(cd["id"], "running")
        try:
            raw_text = _get_document_text(cd["doc_id"])
            audit = run_verifier(
                model=model,
                party_role=cd["party_role"],
                document_type=cd["document_type"],
                submission=submission,
                document_text=raw_text,
            )
            db.save_verifier_output(cd["id"], audit.model_dump_json())
        except Exception as exc:
            # Verifier failure is non-fatal — Clerk output is still usable
            db.set_verifier_status(cd["id"], "error")
            log.warning(
                "Verifier error (non-fatal) | case_doc_id=%s | %s", cd["id"], exc
            )

        return cd["id"], None

    with ThreadPoolExecutor(max_workers=min(len(case_docs), 4)) as pool:
        futures = {pool.submit(_clerk_then_verify, cd): cd for cd in case_docs}
        for fut in as_completed(futures):
            _, err = fut.result()
            if err:
                errors.append(err)

    if errors:
        db.update_case_status(case_id, "error", "; ".join(errors))
        raise RuntimeError(f"Clerk stage failed for case {case_id}: {errors}")

    # ── Citation Auditor (stage 2.5) — runs after all Clerk outputs are ready ──
    # Non-fatal: a failure here logs a warning but does not block the pipeline.
    try:
        fresh_docs = db.get_case_documents(case_id)
        submissions = {
            cd["party_role"]: StandardizedPartySubmission.model_validate_json(cd["clerk_output"])
            for cd in fresh_docs
            if cd.get("clerk_output")
        }
        if submissions:
            audit = run_citation_auditor(model=model, submissions=submissions)
            db.save_citation_audit(case_id, audit.model_dump_json())
            log.info(
                "Pipeline CITATION AUDIT DONE | case_id=%s | checked=%d | found=%d | "
                "not_found=%d | misrepresented=%d",
                case_id,
                audit.total_case_citations,
                audit.total_found,
                audit.total_not_found,
                audit.total_misrepresented,
            )
    except Exception as exc:
        log.warning(
            "Citation Auditor failed (non-fatal, pipeline continues) | case_id=%s | %s",
            case_id, exc,
        )

    # Jump directly to verifier_done — Clerk, Verifier, and Citation Audit all complete
    db.update_case_status(case_id, "verifier_done")
    log.info(
        "Pipeline CLERK+VERIFY+CITATION DONE | case_id=%s | docs=%d",
        case_id, len(case_docs),
    )


# ---------------------------------------------------------------------------
# Stage 2 — Registrar
# ---------------------------------------------------------------------------


def run_pipeline_registrar(case_id: int):
    """
    Build AdversarialMatrix from clerk + verifier outputs.
    Requires all case documents to have clerk_status = 'done'.
    Uses verifier_output (audit) if available to enrich the Registrar prompt.
    Status: registrar_running → registrar_done
    """
    log.info("Pipeline REGISTRAR START | case_id=%s", case_id)
    db.update_case_status(case_id, "registrar_running")

    case = db.get_case(case_id)
    model = case["model"]
    case_docs = db.get_case_documents(case_id)

    submissions: dict[str, StandardizedPartySubmission] = {}
    audits: dict[str, VerifiedPartySubmission] = {}

    for cd in case_docs:
        if not cd["clerk_output"]:
            db.update_case_status(case_id, "error", f"Missing clerk output for case_doc {cd['id']}")
            raise ValueError(f"clerk_output missing for case_doc_id={cd['id']}")
        submissions[cd["party_role"]] = StandardizedPartySubmission.model_validate_json(cd["clerk_output"])
        if cd.get("verifier_output"):
            audits[cd["party_role"]] = VerifiedPartySubmission.model_validate_json(cd["verifier_output"])

    petitioner = submissions.get("Petitioner")
    respondent = submissions.get("Respondent")

    if not petitioner or not respondent:
        missing = [r for r in ("Petitioner", "Respondent") if r not in submissions]
        db.update_case_status(case_id, "error", f"Missing submissions for: {missing}")
        raise ValueError(f"Missing clerk outputs for roles: {missing}")

    # Inject any prior rejection reason so the agent can correct its approach
    prior_result = db.get_case_result(case_id)
    rejection_feedback = (
        prior_result.get("rejection_reason") if prior_result else None
    )

    matrix = run_registrar(
        model=model,
        petitioner_submission=petitioner,
        respondent_submission=respondent,
        petitioner_audit=audits.get("Petitioner"),
        respondent_audit=audits.get("Respondent"),
        rejection_feedback=rejection_feedback,
    )
    db.save_adversarial_matrix(case_id, matrix.model_dump_json())
    db.update_case_status(case_id, "registrar_done")
    log.info("Pipeline REGISTRAR DONE | case_id=%s | issues=%d", case_id, len(matrix.framed_issues))


# ---------------------------------------------------------------------------
# Stage 4 — Procedural Agent
# ---------------------------------------------------------------------------

def run_pipeline_procedural(case_id: int):
    """
    Sift AdversarialMatrix for procedural bars and set issues_to_proceed.
    Requires adversarial_matrix to exist in case_results.
    Status: procedural_running → procedural_done
    """
    log.info("Pipeline PROCEDURAL START | case_id=%s", case_id)

    result = db.get_case_result(case_id)
    if not result or not result.get("adversarial_matrix"):
        db.update_case_status(case_id, "error", "No adversarial matrix found.")
        raise ValueError(f"Case {case_id}: adversarial_matrix not found in case_results.")

    db.update_case_status(case_id, "procedural_running")

    case = db.get_case(case_id)
    model = case["model"]
    case_title = case["title"]

    matrix = AdversarialMatrix.model_validate_json(result["adversarial_matrix"])
    analysis = run_procedural_agent(model=model, case_title=case_title, matrix=matrix)

    sifted = {
        "adversarial_matrix": matrix.model_dump(),
        "procedural_analysis": analysis.model_dump(),
    }
    db.save_sifted_matrix(case_id, json.dumps(sifted))
    db.update_case_status(case_id, "procedural_done")
    log.info(
        "Pipeline PROCEDURAL DONE | case_id=%s | issues_to_proceed=%d | issues_flagged=%d",
        case_id,
        len(analysis.issues_to_proceed),
        len(analysis.issues_flagged),
    )


# ---------------------------------------------------------------------------
# Stage 5 — Devil's Advocate Agent
# ---------------------------------------------------------------------------

def run_pipeline_devils_advocate(case_id: int):
    """
    Stress-test procedurally cleared issues before human review.
    Requires sifted_matrix to exist in case_results.
    Status: devils_advocate_running → review_pending
    """
    log.info("Pipeline DEVIL'S ADVOCATE START | case_id=%s", case_id)

    result = db.get_case_result(case_id)
    if not result or not result.get("sifted_matrix"):
        db.update_case_status(case_id, "error", "No sifted matrix found.")
        raise ValueError(f"Case {case_id}: sifted_matrix not found in case_results.")

    db.update_case_status(case_id, "devils_advocate_running")

    case = db.get_case(case_id)
    model = case["model"]
    case_title = case["title"]

    sifted_data = json.loads(result["sifted_matrix"])
    matrix = AdversarialMatrix.model_validate(sifted_data["adversarial_matrix"])

    analysis = ProceduralAnalysis.model_validate(sifted_data["procedural_analysis"])

    # Filter to only procedurally cleared issues
    issues_to_adjudicate = [
        issue for issue in matrix.framed_issues
        if issue.issue_id in analysis.issues_to_proceed
    ]
    if not issues_to_adjudicate:
        log.warning("No issues to stress-test after procedural sifting | case_id=%s", case_id)
        issues_to_adjudicate = matrix.framed_issues  # fall back to all issues

    stress = run_devils_advocate(
        model=model,
        case_title=case_title,
        issues_to_adjudicate=issues_to_adjudicate,
    )

    stress_tested = {
        "sifted_matrix": sifted_data,
        "stress_tested_matrix": stress.model_dump(),
    }
    db.save_stress_tested_matrix(case_id, json.dumps(stress_tested))
    db.update_case_status(case_id, "review_pending")
    log.info(
        "Pipeline DEVIL'S ADVOCATE DONE | case_id=%s | stress_tests=%d",
        case_id,
        len(stress.stress_tests),
    )


# ---------------------------------------------------------------------------
# Stage 6 — Judge
# ---------------------------------------------------------------------------

def run_pipeline_judge(case_id: int):
    """
    Run Judge Agent per issue (sequential IRAC), then synthesise final order.
    Requires human_review_status = 'approved' in case_results.
    Only adjudicates issues cleared by the Procedural Agent (issues_to_proceed).
    Status: judge_running → judge_done
    """
    log.info("Pipeline JUDGE START | case_id=%s", case_id)

    result = db.get_case_result(case_id)
    if not result or result["human_review_status"] != "approved":
        raise PermissionError(f"Case {case_id}: AdversarialMatrix not approved by human reviewer.")

    db.update_case_status(case_id, "judge_running")

    case = db.get_case(case_id)
    model = case["model"]
    case_title = case["title"]

    matrix = AdversarialMatrix.model_validate_json(result["adversarial_matrix"])
    background_facts = "\n".join(f"- {f}" for f in matrix.undisputed_background)

    # Determine which issues to adjudicate (respect procedural sifting if available)
    issues_to_decide = matrix.framed_issues
    if result.get("sifted_matrix"):
        sifted_data = json.loads(result["sifted_matrix"])
    
        analysis = ProceduralAnalysis.model_validate(sifted_data["procedural_analysis"])
        if analysis.issues_to_proceed:
            issues_to_decide = [
                issue for issue in matrix.framed_issues
                if issue.issue_id in analysis.issues_to_proceed
            ]
            log.info(
                "Judge restricted to %d/%d issues by procedural sifting | case_id=%s",
                len(issues_to_decide),
                len(matrix.framed_issues),
                case_id,
            )

    # Build citation audit summary once — injected into every issue's IRAC prompt
    citation_audit_summary = _format_citation_audit_summary(result.get("citation_audit"))
    if citation_audit_summary:
        log.info("Judge: citation audit summary available (%d chars)", len(citation_audit_summary))
    else:
        log.info("Judge: no citation audit available")

    reasoned_decisions: list[ReasonedDecision] = []
    for issue in issues_to_decide:
        decision = run_judge_on_issue(
            model=model,
            case_title=case_title,
            background_facts=background_facts,
            issue=issue,
            citation_audit_summary=citation_audit_summary,
        )
        reasoned_decisions.append(decision)

    final_order = run_judge_final_order(
        model=model,
        case_title=case_title,
        background_facts=background_facts,
        reasoned_decisions=reasoned_decisions,
    )

    court_order = DraftCourtOrder(
        case_title=case_title,
        background_facts=background_facts,
        reasoned_decisions=reasoned_decisions,
        final_order=final_order,
    )
    db.save_draft_court_order(case_id, court_order.model_dump_json())
    db.update_case_status(case_id, "judge_done")
    log.info("Pipeline JUDGE DONE | case_id=%s | issues_decided=%d", case_id, len(reasoned_decisions))
    return court_order


# ---------------------------------------------------------------------------
# Stage 7 — Drafting Agent
# ---------------------------------------------------------------------------

def run_pipeline_drafter(case_id: int, forum: str = "", jurisdiction_style: str = "indian_high_court"):
    """
    Format the DraftCourtOrder into jurisdiction-appropriate court order prose.
    Requires draft_court_order to exist in case_results (judge_done).
    Status: drafter_running → complete

    Args:
        case_id:             The case to draft
        forum:               Court name/location (e.g. "High Court of Bombay at Mumbai")
        jurisdiction_style:  "indian_high_court" | "supreme_court" | "district_court" | "custom"
    """
    log.info("Pipeline DRAFTER START | case_id=%s | style=%s", case_id, jurisdiction_style)

    result = db.get_case_result(case_id)
    if not result or not result.get("draft_court_order"):
        db.update_case_status(case_id, "error", "No draft court order found.")
        raise ValueError(f"Case {case_id}: draft_court_order not found in case_results.")

    db.update_case_status(case_id, "drafter_running")

    case = db.get_case(case_id)
    model = case["model"]
    case_title = case["title"]
    _forum = forum or case_title  # fall back to case title as forum hint

    draft_order = DraftCourtOrder.model_validate_json(result["draft_court_order"])
    formal_order = run_drafter(
        model=model,
        case_title=case_title,
        forum=_forum,
        jurisdiction_style=jurisdiction_style,
        draft_order=draft_order,
    )

    db.save_formal_court_order(case_id, formal_order.model_dump_json())
    db.update_case_status(case_id, "complete")
    log.info(
        "Pipeline DRAFTER DONE | case_id=%s | body_length=%d chars",
        case_id,
        len(formal_order.body),
    )
    return formal_order
