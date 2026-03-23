# Stage 7 — Drafting Agent

## What it does

The Drafter is the final stage. It takes the Judge's structured `DraftCourtOrder` and converts it into a properly formatted, jurisdiction-correct court order — the kind of document that could actually be filed.

It handles three Indian court styles:
- **indian_high_court** — writ petition format, cause title, coram, appearances, prose body
- **supreme_court** — SLP/appeal format, bench of two
- **district_court** — civil suit format

The Drafter is a formatting agent — it does **not** add or change any legal conclusions. Every holding in the DraftCourtOrder must appear in the FormalCourtOrder unchanged.

## Input

| Parameter           | Type            | Description                                              |
|---------------------|-----------------|----------------------------------------------------------|
| `case_title`        | string          | Human-readable case name                                 |
| `forum`             | string          | Court name, e.g. "High Court of Delhi at New Delhi"      |
| `jurisdiction_style`| string          | `"indian_high_court"` / `"supreme_court"` / etc.        |
| `draft_order`       | DraftCourtOrder | The Judge Agent's output (what to format)                |

## Output → `FormalCourtOrder`

```
IN THE HIGH COURT OF JUDICATURE AT NEW DELHI
WRIT PETITION (CIVIL) NO. ___ OF 2024

RAMESH KUMAR                         ...Petitioner

              versus

MUNICIPAL CORPORATION OF DELHI        ...Respondent

CORAM: HON'BLE MR. JUSTICE ___

DATE: 22nd March, 2026

For the Petitioner: Mr. ___, Advocate
For the Respondent: Mr. ___, Advocate

Heard learned counsel for the parties.

The petitioner has filed this writ petition challenging the order of termination...
[full prose body]

ORDER
1. The petition is allowed.
2. The impugned termination order dated 15.03.2024 is quashed and set aside.
3. The Respondent is directed to reinstate the Petitioner within 30 days.
4. There shall be no order as to costs.

Ordered accordingly.

                                         Sd/-
                                    (JUDGE'S NAME)
                                         JUDGE
```

## Key design notes

- Uses the **fast** model tier with **temperature = 0.2** — formatting is well-defined but slight variation produces more natural-sounding prose
- Uses **placeholder conventions** for missing info: `___` for case numbers, `₹___/-` for amounts
- The frontend's Drafter tab has an **export button** that saves the full order as a .txt file
- This is the **terminal stage** — status → `complete` after this runs
