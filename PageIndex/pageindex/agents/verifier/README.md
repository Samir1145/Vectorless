# Stage 2 — Verifier Agent

## What it does

The Verifier is the fact-checker. It takes the Clerk's structured extraction and goes back to the original document to verify every item:

- **Confidence score** (0.0–1.0) — how reliable is the Clerk's extraction overall?
- **Flags** — specific problems: unsupported facts, missing citations, overstated claims
- **Citation audit** — for every citation the Clerk found, did it actually appear in the document?
- **Internal contradictions** — does the party contradict itself within its own document?

## Input

| Parameter    | Type                          | Description                                    |
|--------------|-------------------------------|------------------------------------------------|
| `submission` | StandardizedPartySubmission   | The Clerk Agent's output (what to audit)       |
| `document_text` | string                     | The original document (source of truth)        |

## Output → `VerifiedPartySubmission`

```json
{
  "overall_confidence": 0.91,
  "flags": [
    {
      "flag_type": "citation_not_found",
      "severity": "warning",
      "affected_field": "cited_laws_and_cases[2]",
      "description": "Citation 'Chhotu Ram v. State of UP, AIR 1981 All 23' was not found in the document text."
    }
  ],
  "citation_audit": [
    { "citation": "Section 116, DMC Act", "found_in_page_text": true, "exact_quote": "...under Section 116 of the DMC Act..." },
    { "citation": "Chhotu Ram v. State of UP, AIR 1981 All 23", "found_in_page_text": false, "exact_quote": null }
  ],
  "internal_contradictions": []
}
```

## Key design notes

- Runs **immediately after** the Clerk Agent in the same thread (no separate trigger needed)
- Uses the **fast** model tier — cross-referencing is well-structured and doesn't need deep reasoning
- Verifier failure is **non-fatal** — if it errors, the Clerk output is still preserved and usable
- The Registrar Agent receives this audit and uses it to annotate unverified citations in the matrix
- Next stage: [registrar/](../registrar/README.md)
