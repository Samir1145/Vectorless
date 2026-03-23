# Stage 5 — Devil's Advocate Agent

## What it does

The Devil's Advocate is the adversarial stress-tester. For every procedurally cleared issue, it asks: **what is the single strongest argument that defeats each party's position?**

This isn't deciding the case — it's finding the weak spots in each side's argument before a human reviews the matrix. The reviewer sees this vulnerability analysis alongside the matrix and can then make an informed decision about whether to approve it for the Judge.

After this stage, execution pauses at the **human review gate**. A human must approve or reject the matrix before the Judge runs.

## Input

| Parameter               | Type              | Description                                              |
|-------------------------|-------------------|----------------------------------------------------------|
| `case_title`            | string            | Human-readable case name                                 |
| `issues_to_adjudicate`  | list[FramedIssue] | Only the procedurally cleared issues (from stage 4)      |

## Output → `StressTestedMatrix`

```json
{
  "stress_tests": [
    {
      "issue_id": "I-1",
      "petitioner_vulnerability": {
        "strongest_counter": "The Petitioner cannot produce the show-cause notice they claim was never given — the burden to prove absence of notice is on the Petitioner.",
        "weakness_type": "factual_gap",
        "severity": "medium",
        "suggested_reframe": null
      },
      "respondent_vulnerability": {
        "strongest_counter": "The 'personal hearing' of 10 March 2024 was 5 days before termination — insufficient for compliance with natural justice principles.",
        "weakness_type": "logical_leap",
        "severity": "high",
        "suggested_reframe": null
      },
      "balance_assessment": "petitioner_stronger"
    }
  ],
  "strongest_issues_for_petitioner": ["I-1", "I-3"],
  "strongest_issues_for_respondent": [],
  "most_contested_issues": ["I-2"],
  "reviewer_note": "The Petitioner has a strong case on I-1. Key item to verify: the exact text of Section 116 DMC Act..."
}
```

## Key design notes

- Uses the **powerful** model tier with **temperature = 0.4** — adversarial creativity requires some stochasticity
- **Human review gate follows this stage** — the reviewer sees this vulnerability map before approving
- If the human rejects, they can provide a reason which is stored and injected into the Registrar on re-run
- Next stage: [judge/](../judge/README.md) — but only after human approval
