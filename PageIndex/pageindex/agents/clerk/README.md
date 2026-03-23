# Stage 1 — Clerk Agent

## What it does

The Clerk reads a court document filed by one party (Petitioner or Respondent) and extracts a structured record of everything in it:

- **Facts** — what the party claims happened, each anchored to a page number
- **Issues** — the legal questions the party says the court must decide
- **Citations** — every statute, rule, and case the party relies on
- **Prayers** — the specific reliefs the party is asking the court to grant

Think of it as a very thorough paralegal reading the document and filling out a structured form.

## Input

| Parameter       | Type   | Description                                       |
|-----------------|--------|---------------------------------------------------|
| `party_role`    | string | `"Petitioner"` or `"Respondent"`                 |
| `document_type` | string | `"Petition"`, `"Affidavit"`, `"Reply"`, etc.     |
| `document_text` | string | Full plain-text content of the PDF document      |

## Output → `StandardizedPartySubmission`

```json
{
  "party_role": "Petitioner",
  "document_type": "Petition",
  "extracted_facts": [
    { "statement": "The Petitioner was employed as Assistant Engineer since 2010.", "page_index": 2, "verified": true }
  ],
  "issues_raised": [
    "Whether the termination order dated 15 March 2024 was passed without due notice."
  ],
  "cited_laws_and_cases": [
    { "citation": "Section 116, Delhi Municipal Corporation Act, 1957", "page_index": 4, "verified": true }
  ],
  "prayers": [
    "Quash and set aside the termination order dated 15.03.2024."
  ]
}
```

## Key design notes

- Clerk + Verifier run in the **same thread** — as soon as the Clerk finishes for one party, the Verifier immediately audits its output. Both parties run in parallel.
- The Clerk uses the **fast** model tier (gpt-4o-mini) — extraction is straightforward.
- Document text is **truncated** to `max_doc_tokens` before injection if needed.
- Next stage: [verifier/](../verifier/README.md)
