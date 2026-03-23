# Stage 3 — Registrar Agent

## What it does

The Registrar is the neutral officer. It reads both parties' submissions side by side and produces the **AdversarialMatrix** — a document that maps every contested legal issue with each party's arguments and citations laid out in parallel.

The goal is a perfectly neutral framing so a judge can look at any issue and see both sides without bias from the document structure.

## Input

| Parameter              | Type                        | Description                                     |
|------------------------|-----------------------------|--------------------------------------------------|
| `petitioner_submission` | StandardizedPartySubmission | Clerk output for the Petitioner                 |
| `respondent_submission` | StandardizedPartySubmission | Clerk output for the Respondent                 |
| `petitioner_audit`     | VerifiedPartySubmission     | Optional — Verifier audit for the Petitioner    |
| `respondent_audit`     | VerifiedPartySubmission     | Optional — Verifier audit for the Respondent    |
| `rejection_feedback`   | string                      | Optional — reason a prior matrix was rejected   |

## Output → `AdversarialMatrix`

```json
{
  "undisputed_background": [
    "The Petitioner was employed as Assistant Engineer from 2010 to 2024.",
    "A termination order was issued on 15 March 2024."
  ],
  "framed_issues": [
    {
      "issue_id": "I-1",
      "neutral_issue_statement": "Whether the termination order was passed without giving the Petitioner a reasonable opportunity to be heard.",
      "petitioner_stance": {
        "arguments": ["No show-cause notice was issued before termination."],
        "supporting_citations": ["Section 116, DMC Act", "Ridge v. Baldwin (1964) AC 40"]
      },
      "respondent_stance": {
        "arguments": ["The Petitioner was given a personal hearing on 10 March 2024."],
        "supporting_citations": ["Rule 14, CCS (CCA) Rules 1965"]
      }
    }
  ],
  "human_review_status": "pending"
}
```

## Key design notes

- The `human_review_status` is **always reset to "pending"** — the Registrar never sets it to approved
- Uses the **balanced** model tier — synthesis and neutral framing requires more than simple extraction
- **Rejection memory**: if the human reviewer rejected a prior matrix with a reason, that reason is injected into the prompt as a bolded directive, so the agent knows what to fix
- Unverified citations from the Verifier audit are annotated "(unverified)" in the stances
- Next stage: [procedural/](../procedural/README.md)
