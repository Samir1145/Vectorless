# Adversarial Multi-Agent Synthesis Pipeline

Each folder here is one agent in the pipeline. Open any folder to see everything about that agent in one place.

## Pipeline Sequence

```
  Court documents (PDF)
         │
         ▼
┌─────────────────┐
│  1. clerk/      │  Extracts facts, issues, citations, prayers from each party's document
└────────┬────────┘
         │  StandardizedPartySubmission (x2 — one per party)
         ▼
┌─────────────────┐
│  2. verifier/   │  Cross-checks every extracted fact and citation against the source document
└────────┬────────┘
         │  VerifiedPartySubmission (x2 — confidence score + flags)
         ▼
┌─────────────────┐
│  3. registrar/  │  Aligns both parties issue-by-issue into a neutral AdversarialMatrix
└────────┬────────┘
         │  AdversarialMatrix (all contested issues framed neutrally)
         ▼
┌─────────────────┐
│  4. procedural/ │  Checks for procedural bars: jurisdiction, limitation, standing
└────────┬────────┘
         │  ProceduralAnalysis (issues_to_proceed list — only clean issues go forward)
         ▼
┌──────────────────────┐
│  5. devils_advocate/ │  Finds the strongest counter-argument against each party on each issue
└──────────┬───────────┘
           │  StressTestedMatrix (vulnerabilities + reviewer note)
           │
     *** HUMAN REVIEW GATE ***
     Human approves or rejects the matrix here.
     If rejected, reason is stored and fed back to registrar/ on re-run.
           │
           ▼
┌─────────────────┐
│  6. judge/      │  Applies IRAC reasoning to each issue → final order paragraph
└────────┬────────┘
         │  DraftCourtOrder (per-issue IRAC + final_order text)
         ▼
┌─────────────────┐
│  7. drafter/    │  Formats the draft into jurisdiction-correct court order prose
└────────┬────────┘
         │
         ▼
   FormalCourtOrder (cause title, coram, body, operative portion, signature)
```

## What's in Each Agent Folder

| File          | What it contains                                              |
|---------------|---------------------------------------------------------------|
| `README.md`   | Plain English: what this agent does, input, output           |
| `agent.yaml`  | Machine-readable manifest: stage, model tier, temperature    |
| `skills.md`   | **System prompt** — the agent's expertise and identity       |
| `task.md`     | **User prompt** — the specific task + data injected at runtime |
| `schema.py`   | Pydantic models: the exact JSON structure the agent produces |
| `agent.py`    | Python function that wires everything together               |

## How an Agent Call Works

```
skills.md  ──► system message ──┐
                                 ├──► LLM (via Instructor) ──► Pydantic model (schema.py)
task.md + data ──► user message ─┘
```

The `skills.md` file is the agent's **permanent identity** — who they are, what they know.
The `task.md` file is the **specific assignment** — injected once per call with the actual data.
The `schema.py` file enforces the **output contract** — the LLM must return valid JSON matching it.
