# Stage 4 — Procedural Agent

## What it does

The Procedural Agent is the gatekeeper. Before any substantive adjudication happens, it checks whether the case and each issue can even be heard — three threshold questions at the case level, and a bar check per issue.

**Case-level checks:**
- **Jurisdiction** — does this court have the power to hear this type of dispute?
- **Limitation** — was the claim filed within the time limit set by the Limitation Act?
- **Standing** — does the Petitioner have a sufficient legal interest to bring this case?

**Per-issue check:**
- For each framed issue, is there a specific procedural bar? (limitation, jurisdiction, standing, res judicata)

Issues that pass go into `issues_to_proceed` and are the only ones the Devil's Advocate and Judge will see.

## Input

| Parameter    | Type               | Description                                         |
|--------------|--------------------|-----------------------------------------------------|
| `case_title` | string             | Human-readable case name                            |
| `matrix`     | AdversarialMatrix  | The Registrar's output (all framed issues)          |

## Output → `ProceduralAnalysis`

```json
{
  "jurisdiction_finding": "maintainable",
  "jurisdiction_reasoning": "High Court has jurisdiction under Article 226 — impugned order is of a statutory body.",
  "limitation_finding": "within_time",
  "limitation_reasoning": "Order dated March 2024, petition filed April 2024 — well within 3 years.",
  "standing_finding": "established",
  "standing_reasoning": "Petitioner is the directly affected employee.",
  "issue_flags": [],
  "issues_to_proceed": ["I-1", "I-2", "I-3"],
  "issues_flagged": []
}
```

## Key design notes

- Uses the **balanced** model tier — requires familiarity with procedural law, not just extraction
- **Procedurally barred issues never reach the Judge** — the Judge only adjudicates `issues_to_proceed`
- `severity` field: `fatal` bars the issue entirely; `curable` means it can be fixed by reframing; `advisory` is a note only
- Next stage: [devils_advocate/](../devils_advocate/README.md)
