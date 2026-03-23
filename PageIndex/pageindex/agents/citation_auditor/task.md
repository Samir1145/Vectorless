{# variables: citation, case_title, court, decision_date, actual_snippet, party_claims #}
You are verifying whether a party's use of a legal precedent is accurate.

## Citation
{{citation}}

## Case found in Indian Kanoon
**Title:** {{case_title}}
**Court:** {{court}}
**Date:** {{decision_date}}

**Retrieved text / headline:**
{{actual_snippet}}

## Party's claims that reference this citation
{{party_claims}}

## Your task

1. Write a one-sentence `actual_holding` summarising what the retrieved text says this case decided.
2. Assess whether the party's claims accurately reflect that holding (`claimed_holding_matches`).
3. If there is a discrepancy, describe it briefly in `discrepancy_note`.
4. Based on the retrieved text, assess whether the case appears to have been overruled or significantly limited (`is_overruled`). Set to `null` if the snippet gives no indication either way.

Be conservative — if the snippet is too brief to compare, set `claimed_holding_matches: null` and note that in `discrepancy_note`.
