# Stage 2.5 — Citation Auditor Agent

## What it does

The Citation Auditor runs **after** both parties' Clerk + Verifier chains complete and **before** the Registrar frames the issues.

It answers two questions the Verifier cannot:
1. **Does this case actually exist?** — queries Indian Kanoon to verify the citation.
2. **Is the party accurately characterising what the case held?** — LLM comparison of the party's claims against the retrieved judgment snippet.

Statute/rule citations (Section X of Act Y) are skipped — the external lookup only makes sense for case law.

## External dependency — Indian Kanoon API

```
INDIAN_KANOON_API_KEY=your_token_here   # in PageIndex/.env
```

Get a token at https://indiankanoon.org/api/
If the key is absent the agent runs in **unverified mode** — all case citations are marked `unverified` and the pipeline continues without blocking.

## Input

| Parameter     | Type                                        | Description                        |
|---------------|---------------------------------------------|------------------------------------|
| `model`       | string                                      | LiteLLM fallback model string      |
| `submissions` | `dict[party_role → StandardizedPartySubmission]` | Both parties' Clerk outputs   |

## Output → `CitationAuditReport`

```json
{
  "indian_kanoon_available": true,
  "total_case_citations": 8,
  "total_found": 7,
  "total_not_found": 1,
  "total_misrepresented": 1,
  "total_unverified": 0,
  "results": [
    {
      "citation": "AIR 1985 SC 1585",
      "party_role": "Petitioner",
      "is_case_citation": true,
      "found": true,
      "source_url": "https://indiankanoon.org/doc/123456/",
      "case_title": "Olga Tellis v. Bombay Municipal Corporation",
      "court": "Supreme Court of India",
      "decision_date": "1985-07-10",
      "actual_holding": "The right to livelihood is part of Article 21; eviction requires prior notice and hearing.",
      "claimed_holding_matches": true,
      "discrepancy_note": null,
      "is_overruled": false,
      "verification_method": "indian_kanoon"
    },
    {
      "citation": "AIR 2024 SC 9999",
      "party_role": "Respondent",
      "is_case_citation": true,
      "found": false,
      "verification_method": "indian_kanoon",
      "note": "Citation not found in Indian Kanoon database."
    }
  ]
}
```

## Key design notes

- **Non-fatal**: each lookup is wrapped in try/except. One bad citation never stops the pipeline.
- **Fast model tier** (`gpt-4o-mini`) — the holding comparison is a focused, well-defined task.
- **Temperature 0** — deterministic comparison.
- The audit is injected as context into the **Judge** prompt so it can weigh disputed or unverified citations appropriately.
- The frontend shows the audit as a third section in the **Verify tab**, below both parties' verifier outputs.
