# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**PageIndex** is a reasoning-based RAG (Retrieval-Augmented Generation) system that builds hierarchical tree indexes from PDF/Markdown documents using LLM reasoning instead of vector embeddings. The backend is Python/Flask; the frontend is React + Vite.

## Development Commands

### Backend
```bash
cd PageIndex
pip3 install -r requirements.txt
cp .env.example .env  # set OPENAI_API_KEY or CHATGPT_API_KEY
python3 server.py     # Flask API server on :8000
```

CLI mode (no server):
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

## Architecture

### Core Algorithm (`pageindex/page_index.py`)
The central piece. Given a PDF, it:
1. Detects existing table of contents via LLM
2. Recursively builds a hierarchical tree of document sections
3. Generates per-node summaries and assigns node IDs
4. Returns a nested JSON structure

`page_index_md.py` handles the same flow for Markdown files using heading-level parsing. `utils.py` provides `llm_completion()` / `llm_acompletion()` with 10-retry logic, PDF text extraction, and token counting via LiteLLM.

### Backend (`server.py` + `db.py`)
`server.py` exposes 14 REST endpoints. Document processing runs in background threads with cancellation support. Logs are collected in a circular in-memory buffer (500 entries) and streamed to the frontend via Server-Sent Events (`GET /api/logs/stream`).

`db.py` manages a SQLite database with these key tables:
- `documents` — file metadata and processing status
- `document_trees` — full JSON tree + statistics
- `tree_nodes` — flattened nodes for querying
- `search_fts` — FTS5 virtual table (auto-synced via triggers)

The tree is stored as a JSON blob in `document_trees` and also flattened into `tree_nodes` + FTS5 at indexing time.

### Frontend (`frontend/src/App.jsx`)
Single large React component (~877 lines). It polls document status every 2 seconds and logs every 1.5 seconds. Key state: `treeData` (loaded tree), `pdfPage` (current PDF page), `searchResults` (FTS results). PDF rendering uses `react-pdf`. The frontend hardcodes `http://localhost:8000` as the API base.

### Configuration (`pageindex/config.yaml`)
Controls LLM model and processing parameters:
```yaml
model: "gpt-4o-2024-11-20"
toc_check_page_num: 20
max_page_num_each_node: 10
max_token_num_each_node: 20000
if_add_node_summary: "yes"
```

### Environment
- Backend: `OPENAI_API_KEY` (or legacy `CHATGPT_API_KEY`) in `PageIndex/.env`
- LLM calls go through **LiteLLM**, so other providers (Anthropic, etc.) work by changing `model` in config.yaml

## Key Data Flow

```
Upload PDF → POST /api/documents/upload
  → background thread: page_index_main()
    → LLM detects TOC → builds tree → generates summaries
  → tree saved to SQLite (JSON blob + flattened nodes + FTS5)
  → status set to "done"
Frontend polls status → fetches tree → renders expandable tree
  → clicking a node scrolls the PDF viewer to that page range
```

## File Layout

```
PageIndex/              # Backend
├── pageindex/          # Core algorithm package
│   ├── page_index.py   # Main tree-building algorithm
│   ├── page_index_md.py
│   ├── utils.py        # LLM helpers, PDF parsing
│   └── config.yaml
├── server.py           # Flask REST API
├── db.py               # SQLite schema & queries
├── run_pageindex.py    # CLI entry point
├── uploads/            # Stored PDFs
└── results/            # Generated JSON outputs

frontend/               # React frontend
└── src/
    ├── App.jsx         # Entire UI (single component)
    └── App.css
```
