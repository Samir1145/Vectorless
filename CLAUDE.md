# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This repository contains two integrated systems:

1. **PageIndex** — a reasoning-based RAG system that builds hierarchical tree indexes from PDF/Markdown documents using LLM reasoning instead of vector embeddings. It is the document ingestion layer.

2. **Adversarial Multi-Agent Synthesis Pipeline** — a 7-agent legal adjudication pipeline built on top of PageIndex. It processes court documents, frames contested issues, stress-tests arguments, and produces a formally drafted court order. All agents use structured outputs via Instructor + LiteLLM.

The backend is Python/Flask; the frontend is React + Vite (single component).

---

## Development Commands

### Backend
```bash
cd PageIndex
pip3 install -r requirements.txt
cp .env.example .env  # set OPENAI_API_KEY or CHATGPT_API_KEY
python3 server.py     # Flask API server on :8000
```

CLI mode (PageIndex tree-building only, no server):
```bash
python3 run_pageindex.py --pdf_path document.pdf --model gpt-4o-2024-11-20
```

### Frontend
```bash
cd frontend
npm install
npm run dev      # Dev server on :5173 (HMR)
npm run build    # Production build
npm run lint     # ESLint
npm run preview  # Preview production build
```

### Kill/restart server (common need)
```bash
lsof -i :8000 -sTCP:LISTEN   # find PID
kill -9 <PID>
python3 server.py &
```

---

## Architecture

### PageIndex Core (`pageindex/page_index.py`)
Given a PDF:
1. Detects existing table of contents via LLM
2. Recursively builds a hierarchical tree of document sections
3. Generates per-node summaries and assigns `node_id`s
4. Returns a nested JSON structure

`page_index_md.py` handles Markdown files. `utils.py` provides `llm_completion()` / `llm_acompletion()` with 10-retry logic, PDF text extraction, and token counting.

### Adversarial Pipeline (`pageindex/agents.py`, `pageindex/pipeline.py`)

Seven agents, each a structured LLM call via Instructor. All use `_chat(model, prompt, ResponseModel, system=skills, temperature=t)`.

| # | Agent | Model tier | Temp | Input → Output |
|---|-------|-----------|------|----------------|
| 1 | **Clerk** | fast | 0 | Document text → `StandardizedPartySubmission` |
| 2 | **Verifier** | fast | 0 | Clerk output + doc text → `VerifiedPartySubmission` |
| 3 | **Registrar** | balanced | 0 | Both submissions + audits → `AdversarialMatrix` |
| 4 | **Procedural** | balanced | 0 | AdversarialMatrix → `ProceduralAnalysis` |
| 5 | **Devil's Advocate** | powerful | 0.4 | Cleared issues → `StressTestedMatrix` |
| 6 | **Judge** | powerful | 0 | Per issue IRAC → `ReasonedDecision` × N + `DraftCourtOrder` |
| 7 | **Drafter** | fast | 0.2 | DraftCourtOrder → `FormalCourtOrder` |

**Clerk + Verifier are chained** — `run_pipeline_clerk()` runs both in sequence per document within the same thread (Petitioner and Respondent in parallel). Status goes directly from `clerk_running` → `verifier_done`.

**Human review gate** sits after Devil's Advocate. The human approves or rejects the matrix. On rejection, the reason is stored in `case_results.rejection_reason` and injected into the Registrar prompt on re-run.

### Model Tiering (`pageindex/config.yaml`)
```yaml
pipeline:
  model_fast:     "gpt-4o-mini"        # Clerk, Verifier, Drafter
  model_balanced: "gpt-4o-2024-11-20"  # Registrar, Procedural
  model_powerful: "gpt-4o-2024-11-20"  # Judge, Devil's Advocate
  temperature:
    devils_advocate: 0.4
    drafter: 0.2
    # all others: 0
  max_doc_tokens: 80000  # documents longer than this are truncated before injection
```

### Skills Files (`pageindex/prompts/skills/`)
Each agent has a `{name}_skills.md` file loaded as the system prompt. These contain domain expertise (Indian procedural law, IRAC discipline, citation formats, drafting conventions). The task prompt is the user message. This split enables prompt caching.

```
clerk_skills.md
verifier_skills.md
registrar_skills.md
procedural_skills.md
devils_advocate_skills.md
judge_skills.md
drafter_skills.md
```

### Prompt Templates (`pageindex/prompts/`)
All prompts are `.md` files with `{{variable}}` placeholders loaded by `prompt_loader.py`. Use `load_prompt(name, **vars)` for task prompts and `load_skills(name)` for system prompts. The loader is LRU-cached; call `reload_prompts()` to clear.

### Pipeline Status Machine
```
pending
→ clerk_running → verifier_done          ← Clerk+Verifier chained
→ registrar_running → registrar_done
→ procedural_running → procedural_done
→ devils_advocate_running → review_pending
→ review_approved | review_rejected      ← Human gate
→ judge_running → judge_done
→ drafter_running → complete
→ error
```

### Backend (`server.py` + `db.py`)
`server.py` exposes REST endpoints for both PageIndex documents and pipeline cases. Each pipeline stage has a `POST /api/cases/<id>/run/<stage>` endpoint that spawns a background thread. Logs stream via SSE (`GET /api/logs/stream`).

`db.py` manages SQLite (WAL mode) with idempotent schema migrations in `_MIGRATIONS`. Key tables:

**PageIndex tables:**
- `documents` — file metadata and processing status
- `document_trees` — full JSON tree + statistics
- `tree_nodes` — flattened nodes for querying
- `search_fts` — FTS5 virtual table (auto-synced via triggers)
- `document_pages` — per-page extracted text (used by pipeline agents)

**Pipeline tables:**
- `cases` — case metadata and current status
- `case_documents` — per-party documents with `clerk_output`, `verifier_output` JSON blobs
- `case_results` — all pipeline outputs: `adversarial_matrix`, `sifted_matrix`, `stress_tested_matrix`, `draft_court_order`, `formal_court_order`, `human_review_status`, `rejection_reason`

### Pydantic Models (`pageindex/models.py`)
Canonical data contracts between agents. Key models:
- `StandardizedPartySubmission` — Clerk output (facts, issues, citations, prayers)
- `VerifiedPartySubmission` — Verifier output (confidence, flags, citation audit)
- `AdversarialMatrix` — Registrar output (framed issues, undisputed background)
- `ProceduralAnalysis` — Procedural Agent output (jurisdiction/limitation/standing findings, issues_to_proceed)
- `StressTestedMatrix` — Devil's Advocate output (per-issue vulnerability analysis)
- `DraftCourtOrder` / `FormalCourtOrder` — Judge and Drafter outputs
- `FramedIssue` — carries `issue_id` as provenance anchor through the entire chain

### Frontend (`frontend/src/App.jsx`)
Single React component (~2000 lines). Two modes: Document mode (PageIndex tree viewer + PDF) and Case mode (pipeline UI).

Case mode has 8-step progress bar and 8 tabs: Setup, Clerk, Verify, Registrar, Procedure, Stress (Devil's Advocate), Judge, Draft. Uses `statusRank()` helper for progress bar logic. Polls case status every 2 seconds. Human review gate is in the Devil's Advocate tab.

### Environment
- Backend: `OPENAI_API_KEY` (or legacy `CHATGPT_API_KEY`) in `PageIndex/.env`
- All LLM calls go through **LiteLLM**, so any provider works by changing model strings in `config.yaml`

---

## Key Data Flows

### PageIndex (document indexing)
```
Upload PDF → POST /api/documents/upload
  → background thread: page_index_main()
    → LLM detects TOC → builds tree → generates summaries
  → tree saved to SQLite (JSON blob + flattened nodes + FTS5)
  → status: "done"
Frontend polls → fetches tree → renders expandable tree
  → clicking node scrolls PDF viewer to that page range
```

### Adversarial Pipeline (case adjudication)
```
Create case → POST /api/cases  (attach Petitioner + Respondent documents)
  → Run Clerk  POST /api/cases/<id>/run/clerk
      → Clerk extracts facts/issues/citations (fast model)
      → Verifier audits against source doc   (fast model, same thread)
      → status: verifier_done
  → Run Registrar  POST /api/cases/<id>/run/registrar
      → frames all contested issues neutrally (balanced model)
      → injects rejection_reason if prior matrix was rejected
      → status: registrar_done
  → Run Procedural  POST /api/cases/<id>/run/procedural
      → checks jurisdiction, limitation, standing, per-issue bars
      → status: procedural_done
  → Run Devil's Advocate  POST /api/cases/<id>/run/devils_advocate
      → stress-tests each cleared issue from both sides (powerful, temp=0.4)
      → status: review_pending
  → Human review  POST /api/cases/<id>/review  { action: approve|reject, reason }
      → approve → status: review_approved
      → reject  → stores reason, status: review_rejected (re-run Registrar to rebuild)
  → Run Judge  POST /api/cases/<id>/run/judge
      → IRAC per issue (powerful model, sequential)
      → synthesises final_order paragraph
      → status: judge_done
  → Run Drafter  POST /api/cases/<id>/run/drafter
      → formats DraftCourtOrder into formal prose (fast model, temp=0.2)
      → status: complete
```

---

## File Layout

```
PageIndex/                        # Backend root
├── pageindex/                    # Core package
│   ├── page_index.py             # PageIndex tree-building algorithm
│   ├── page_index_md.py          # Markdown variant
│   ├── utils.py                  # LLM helpers, PDF parsing, token counting
│   ├── agents.py                 # All 7 pipeline agent functions
│   ├── pipeline.py               # Pipeline orchestration (stage runners)
│   ├── models.py                 # Pydantic models for all agents
│   ├── prompt_loader.py          # load_prompt() + load_skills() + LRU cache
│   ├── config.yaml               # Model tiers, temperatures, token budget
│   └── prompts/
│       ├── *.md                  # Task prompts (one per agent/algorithm step)
│       └── skills/
│           └── *_skills.md       # System-prompt persona files (one per agent)
├── server.py                     # Flask REST API (~1000 lines)
├── db.py                         # SQLite schema, migrations, query functions
├── run_pageindex.py              # CLI entry point
├── uploads/                      # Stored PDFs
└── results/                      # Generated JSON outputs (CLI mode)

frontend/
└── src/
    ├── App.jsx                   # Entire UI — Document mode + Case mode
    └── App.css                   # All styles
```
