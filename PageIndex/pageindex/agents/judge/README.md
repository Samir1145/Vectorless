# Stage 6 — Judge Agent

## What it does

The Judge adjudicates the case using the **IRAC method** (Issue → Rule → Application → Conclusion). It runs in two phases:

**Phase 1 — Per-issue IRAC** (`task_irac.md`): One LLM call per cleared issue. For each issue the Judge:
- States the applicable law or binding precedent (**Rule**)
- Applies it to the specific facts, engaging both parties' arguments (**Application**)
- Issues a clear ruling (**Conclusion**)

**Phase 2 — Final order synthesis** (`task_final_order.md`): One LLM call to synthesise all per-issue conclusions into a single final order paragraph.

The output is a `DraftCourtOrder` — a structured draft that the Drafter Agent will then format into formal court order prose.

## Input

| Parameter           | Type                   | Description                                      |
|---------------------|------------------------|--------------------------------------------------|
| `case_title`        | string                 | Human-readable case name                         |
| `background_facts`  | string                 | Undisputed facts narrative (from AdversarialMatrix) |
| `issue`             | FramedIssue            | One issue per IRAC call (only issues_to_proceed) |

## Output → `DraftCourtOrder`

```json
{
  "case_title": "Ramesh Kumar v. Municipal Corporation of Delhi",
  "background_facts": "The Petitioner was employed as Assistant Engineer from 2010...",
  "reasoned_decisions": [
    {
      "issue_id": "I-1",
      "issue_statement": "Whether the termination was without reasonable notice...",
      "rule": "Section 116, DMC Act, 1957 requires 30 days notice before termination of a permanent employee.",
      "analysis": "The Petitioner argues no notice was given. The Respondent claims a personal hearing...",
      "conclusion": "Issue I-1 decided in favour of the Petitioner."
    }
  ],
  "final_order": "The petition is allowed. The impugned termination order dated 15.03.2024 is quashed..."
}
```

## Key design notes

- Uses the **powerful** model tier — deep reasoning over conflicting precedents
- Issues are adjudicated **sequentially** (not in parallel) to preserve logical coherence
- The Judge **only sees issues_to_proceed** — procedurally barred issues never reach here
- There are **two task prompts**: `task_irac.md` (per issue) and `task_final_order.md` (synthesis)
- Next stage: [drafter/](../drafter/README.md)
