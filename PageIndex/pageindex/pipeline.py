"""
pipeline.py
~~~~~~~~~~~
Orchestrates the full Adversarial Multi-Agent Synthesis Pipeline.

Flow:
  1. run_pipeline_clerk(case_id)
       Reads case documents from DB, runs Clerk Agent on each (in parallel
       threads), persists clerk outputs.  Status: clerk_running → clerk_done

  2. run_pipeline_registrar(case_id)
       Reads clerk outputs, runs Registrar Agent, persists AdversarialMatrix.
       Status: registrar_running → registrar_done → review_pending
       *** Human review gate: must call approve_matrix() before step 3 ***

  3. run_pipeline_judge(case_id)
       Reads approved AdversarialMatrix, runs Judge IRAC for each issue
       sequentially, synthesises final order, persists DraftCourtOrder.
       Status: judge_running → judge_done

Each stage is called from a background thread in server.py (same pattern as
document processing).  Exceptions update the case status to "error".
"""

from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

import db
from pageindex.agents import (
    run_clerk,
    run_judge_final_order,
    run_judge_on_issue,
    run_registrar,
)
from pageindex.models import (
    AdversarialMatrix,
    DraftCourtOrder,
    ReasonedDecision,
    StandardizedPartySubmission,
)

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

    def _clerk_one(cd: dict):
        db.set_clerk_status(cd["id"], "running")
        try:
            doc_text = _get_document_text(cd["doc_id"])
            submission = run_clerk(
                model=model,
                party_role=cd["party_role"],
                document_type=cd["document_type"],
                document_text=doc_text,
            )
            db.save_clerk_output(cd["id"], submission.model_dump_json())
            return cd["id"], None
        except Exception as exc:
            db.set_clerk_status(cd["id"], "error")
            log.error("Clerk error | case_doc_id=%s | %s", cd["id"], exc)
            return cd["id"], str(exc)

    with ThreadPoolExecutor(max_workers=min(len(case_docs), 4)) as pool:
        futures = {pool.submit(_clerk_one, cd): cd for cd in case_docs}
        for fut in as_completed(futures):
            _, err = fut.result()
            if err:
                errors.append(err)

    if errors:
        db.update_case_status(case_id, "error", "; ".join(errors))
        raise RuntimeError(f"Clerk stage failed for case {case_id}: {errors}")

    db.update_case_status(case_id, "clerk_done")
    log.info("Pipeline CLERK DONE | case_id=%s | docs=%d", case_id, len(case_docs))


# ---------------------------------------------------------------------------
# Stage 2 — Registrar
# ---------------------------------------------------------------------------

def run_pipeline_registrar(case_id: int):
    """
    Build AdversarialMatrix from clerk outputs.
    Requires all case documents to have clerk_status = 'done'.
    """
    log.info("Pipeline REGISTRAR START | case_id=%s", case_id)
    db.update_case_status(case_id, "registrar_running")

    case = db.get_case(case_id)
    model = case["model"]
    case_docs = db.get_case_documents(case_id)

    submissions: dict[str, StandardizedPartySubmission] = {}
    for cd in case_docs:
        if not cd["clerk_output"]:
            db.update_case_status(case_id, "error", f"Missing clerk output for case_doc {cd['id']}")
            raise ValueError(f"clerk_output missing for case_doc_id={cd['id']}")
        submissions[cd["party_role"]] = StandardizedPartySubmission.model_validate_json(cd["clerk_output"])

    petitioner = submissions.get("Petitioner")
    respondent = submissions.get("Respondent")

    if not petitioner or not respondent:
        missing = [r for r in ("Petitioner", "Respondent") if r not in submissions]
        db.update_case_status(case_id, "error", f"Missing submissions for: {missing}")
        raise ValueError(f"Missing clerk outputs for roles: {missing}")

    matrix = run_registrar(model=model, petitioner_submission=petitioner, respondent_submission=respondent)
    db.save_adversarial_matrix(case_id, matrix.model_dump_json())
    db.update_case_status(case_id, "review_pending")
    log.info("Pipeline REGISTRAR DONE | case_id=%s | issues=%d", case_id, len(matrix.framed_issues))


# ---------------------------------------------------------------------------
# Stage 3 — Judge
# ---------------------------------------------------------------------------

def run_pipeline_judge(case_id: int):
    """
    Run Judge Agent per issue (sequential IRAC), then synthesise final order.
    Requires human_review_status = 'approved' in case_results.
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

    reasoned_decisions: list[ReasonedDecision] = []
    for issue in matrix.framed_issues:
        decision = run_judge_on_issue(
            model=model,
            case_title=case_title,
            background_facts=background_facts,
            issue=issue,
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
