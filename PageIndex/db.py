import sqlite3
import json
import os
import time
import logging
from contextlib import contextmanager
from pathlib import Path

DB_PATH = str(Path(__file__).parent / 'pageindex.db')
UPLOADS_ROOT = str(Path(__file__).parent / 'uploads')

log = logging.getLogger('db')

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS folders (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    parent_id  INTEGER REFERENCES folders(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE TABLE IF NOT EXISTS documents (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    folder_id         INTEGER REFERENCES folders(id) ON DELETE SET NULL,
    original_filename TEXT NOT NULL,
    stored_filename   TEXT NOT NULL,
    file_path         TEXT NOT NULL,
    file_size         INTEGER,
    page_count        INTEGER,
    status            TEXT NOT NULL DEFAULT 'pending',
    error_message     TEXT,
    uploaded_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    processed_at      TEXT
);

CREATE TABLE IF NOT EXISTS document_trees (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id      INTEGER NOT NULL UNIQUE REFERENCES documents(id) ON DELETE CASCADE,
    tree_json   TEXT NOT NULL,
    node_count  INTEGER,
    depth       INTEGER,
    doc_name    TEXT,
    description TEXT,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE TABLE IF NOT EXISTS tree_nodes (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id         INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    tree_id        INTEGER NOT NULL REFERENCES document_trees(id) ON DELETE CASCADE,
    node_id        TEXT,
    parent_node_id TEXT,
    title          TEXT,
    summary        TEXT,
    start_page     INTEGER,
    end_page       INTEGER,
    level          INTEGER DEFAULT 0,
    path           TEXT
);

CREATE INDEX IF NOT EXISTS idx_tree_nodes_doc ON tree_nodes(doc_id);

CREATE VIRTUAL TABLE IF NOT EXISTS search_fts USING fts5(
    title,
    summary,
    content='tree_nodes',
    content_rowid='id'
);

CREATE TRIGGER IF NOT EXISTS tree_nodes_ai AFTER INSERT ON tree_nodes BEGIN
    INSERT INTO search_fts(rowid, title, summary)
    VALUES (new.id, new.title, new.summary);
END;

CREATE TRIGGER IF NOT EXISTS tree_nodes_ad AFTER DELETE ON tree_nodes BEGIN
    INSERT INTO search_fts(search_fts, rowid, title, summary)
    VALUES ('delete', old.id, old.title, old.summary);
END;

CREATE TRIGGER IF NOT EXISTS tree_nodes_au AFTER UPDATE ON tree_nodes BEGIN
    INSERT INTO search_fts(search_fts, rowid, title, summary)
    VALUES ('delete', old.id, old.title, old.summary);
    INSERT INTO search_fts(rowid, title, summary)
    VALUES (new.id, new.title, new.summary);
END;

CREATE TABLE IF NOT EXISTS query_history (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    query_text       TEXT NOT NULL,
    doc_ids_searched TEXT,
    result_node_ids  TEXT,
    session_id       TEXT,
    created_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE TABLE IF NOT EXISTS node_access_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    node_db_id  INTEGER REFERENCES tree_nodes(id) ON DELETE CASCADE,
    doc_id      INTEGER REFERENCES documents(id) ON DELETE CASCADE,
    query_id    INTEGER REFERENCES query_history(id) ON DELETE SET NULL,
    accessed_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE TABLE IF NOT EXISTS document_pages (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id   INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    page_num INTEGER NOT NULL,
    text     TEXT    NOT NULL DEFAULT '',
    UNIQUE(doc_id, page_num)
);

CREATE INDEX IF NOT EXISTS idx_document_pages_doc ON document_pages(doc_id);

CREATE TABLE IF NOT EXISTS annotations (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id       INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    anchor_page  INTEGER NOT NULL,
    anchor_title TEXT,
    anchor_path  TEXT,
    body         TEXT NOT NULL DEFAULT '',
    node_id      TEXT,
    is_orphan    INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE INDEX IF NOT EXISTS idx_annotations_doc ON annotations(doc_id);

-- ---------------------------------------------------------------------------
-- Adversarial Multi-Agent Pipeline tables
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS cases (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    title        TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'pending',
    -- status values: pending
    --                clerk_running | clerk_done
    --                verifier_running | verifier_done
    --                registrar_running | registrar_done
    --                procedural_running | procedural_done
    --                devils_advocate_running
    --                review_pending | review_approved | review_rejected
    --                judge_running | judge_done
    --                drafter_running | complete
    --                error
    error_message TEXT,
    model        TEXT NOT NULL DEFAULT 'gpt-4o-2024-11-20',
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE TABLE IF NOT EXISTS case_documents (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id         INTEGER NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
    doc_id          INTEGER REFERENCES documents(id) ON DELETE SET NULL,
    party_role      TEXT NOT NULL,   -- 'Petitioner' or 'Respondent'
    document_type   TEXT NOT NULL DEFAULT 'Petition',
    -- Clerk output stored as JSON blob (StandardizedPartySubmission)
    clerk_output    TEXT,
    clerk_status    TEXT NOT NULL DEFAULT 'pending',    -- pending | running | done | error
    -- Verifier output stored as JSON blob (VerifiedPartySubmission audit)
    verifier_output TEXT,
    verifier_status TEXT NOT NULL DEFAULT 'pending',   -- pending | running | done | error
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE INDEX IF NOT EXISTS idx_case_documents_case ON case_documents(case_id);

CREATE TABLE IF NOT EXISTS case_results (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id               INTEGER NOT NULL UNIQUE REFERENCES cases(id) ON DELETE CASCADE,
    adversarial_matrix    TEXT,   -- JSON: AdversarialMatrix  (Registrar output)
    sifted_matrix         TEXT,   -- JSON: {adversarial_matrix, procedural_analysis}
    stress_tested_matrix  TEXT,   -- JSON: {sifted_matrix, stress_tested_matrix}
    draft_court_order     TEXT,   -- JSON: DraftCourtOrder    (Judge output)
    formal_court_order    TEXT,   -- JSON: FormalCourtOrder   (Drafting Agent output)
    human_review_status   TEXT NOT NULL DEFAULT 'pending',  -- pending | approved | rejected
    created_at            TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at            TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
"""


# ---------------------------------------------------------------------------
# Schema migrations (add columns to existing tables without data loss)
# Each statement is attempted once; OperationalError means column already exists.
# ---------------------------------------------------------------------------

_MIGRATIONS = [
    # Verifier columns on case_documents
    "ALTER TABLE case_documents ADD COLUMN verifier_output TEXT",
    "ALTER TABLE case_documents ADD COLUMN verifier_status TEXT NOT NULL DEFAULT 'pending'",
    # Soft-delete support on cases
    "ALTER TABLE cases ADD COLUMN archived INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE cases ADD COLUMN archived_at TEXT",
    # New result columns on case_results
    "ALTER TABLE case_results ADD COLUMN sifted_matrix TEXT",
    "ALTER TABLE case_results ADD COLUMN stress_tested_matrix TEXT",
    "ALTER TABLE case_results ADD COLUMN formal_court_order TEXT",
    # Rejection feedback — stores the human reviewer's reason for rejecting the matrix
    "ALTER TABLE case_results ADD COLUMN rejection_reason TEXT",
    # Citation Auditor (stage 2.5) — external case law verification report
    "ALTER TABLE case_results ADD COLUMN citation_audit TEXT",
    # Party display names — customisable per-case labels replacing "Petitioner"/"Respondent"
    "ALTER TABLE cases ADD COLUMN petitioner_name TEXT NOT NULL DEFAULT 'Petitioner'",
    "ALTER TABLE cases ADD COLUMN respondent_name TEXT NOT NULL DEFAULT 'Respondent'",
    # Note Builder Agent — source/type/severity columns on annotations
    "ALTER TABLE annotations ADD COLUMN source TEXT NOT NULL DEFAULT 'human'",
    "ALTER TABLE annotations ADD COLUMN note_type TEXT",
    "ALTER TABLE annotations ADD COLUMN severity TEXT",
    # Note Builder status on documents (pending | generating | done | failed)
    "ALTER TABLE documents ADD COLUMN notes_status TEXT NOT NULL DEFAULT 'pending'",
]


def _run_migrations(conn):
    for sql in _MIGRATIONS:
        try:
            conn.execute(sql)
            log.debug("Migration applied: %s", sql[:60])
        except sqlite3.OperationalError:
            pass  # Column already exists — idempotent


def init_db():
    log.info("Initializing database | path=%s", DB_PATH)
    log.debug("Ensuring uploads directory exists | path=%s", UPLOADS_ROOT)
    os.makedirs(UPLOADS_ROOT, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.executescript(SCHEMA)
        conn.execute("PRAGMA journal_mode=WAL;")
        _run_migrations(conn)
        conn.commit()
        log.info("Database initialized successfully | WAL mode enabled")
    finally:
        conn.close()


@contextmanager
def get_db():
    t0 = time.perf_counter()
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    try:
        yield conn
        conn.commit()
        ms = (time.perf_counter() - t0) * 1000
        log.debug("DB transaction committed (%.1fms)", ms)
    except Exception as exc:
        conn.rollback()
        ms = (time.perf_counter() - t0) * 1000
        log.error("DB transaction rolled back after %.1fms | error=%s", ms, exc)
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# LRU in-memory cache (short-term memory for recently loaded trees)
# ---------------------------------------------------------------------------

_cache: dict = {}        # doc_id -> tree dict
_cache_order: list = []  # LRU order
_CACHE_MAX = 10


def cache_get(doc_id):
    if doc_id in _cache:
        _cache_order.remove(doc_id)
        _cache_order.append(doc_id)
        log.debug("Cache HIT | doc_id=%s | cache_size=%d/%d", doc_id, len(_cache), _CACHE_MAX)
        return _cache[doc_id]
    log.debug("Cache MISS | doc_id=%s | cache_size=%d/%d", doc_id, len(_cache), _CACHE_MAX)
    return None


def cache_put(doc_id, tree):
    if doc_id in _cache:
        _cache_order.remove(doc_id)
        log.debug("Cache UPDATE | doc_id=%s | refreshing existing entry", doc_id)
    elif len(_cache) >= _CACHE_MAX:
        oldest = _cache_order.pop(0)
        del _cache[oldest]
        log.debug("Cache EVICT (LRU full) | evicted doc_id=%s | inserting doc_id=%s", oldest, doc_id)
    else:
        log.debug("Cache INSERT | doc_id=%s | cache_size=%d→%d/%d", doc_id, len(_cache), len(_cache) + 1, _CACHE_MAX)
    _cache[doc_id] = tree
    _cache_order.append(doc_id)


def cache_evict(doc_id):
    if doc_id in _cache:
        _cache_order.remove(doc_id)
        del _cache[doc_id]
        log.debug("Cache EVICT (explicit) | doc_id=%s | cache_size=%d/%d", doc_id, len(_cache), _CACHE_MAX)
    else:
        log.debug("Cache EVICT (noop) | doc_id=%s not in cache", doc_id)


# ---------------------------------------------------------------------------
# Folders
# ---------------------------------------------------------------------------

def create_folder(name, parent_id=None):
    log.info("Creating folder | name=%r | parent_id=%s", name, parent_id)
    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO folders (name, parent_id) VALUES (?, ?)",
            (name, parent_id)
        )
        row = conn.execute("SELECT * FROM folders WHERE id = ?", (cur.lastrowid,)).fetchone()
        result = dict(row)
    log.info("Folder created | id=%s | name=%r | parent_id=%s", result['id'], name, parent_id)
    return result


def get_folders():
    log.debug("Fetching all folders")
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM folders ORDER BY name").fetchall()
        results = [dict(r) for r in rows]
    log.debug("Fetched %d folder(s)", len(results))
    return results


def delete_folder(folder_id):
    log.info("Deleting folder | folder_id=%s (cascade will remove child folders + documents)", folder_id)
    with get_db() as conn:
        cur = conn.execute("DELETE FROM folders WHERE id = ?", (folder_id,))
        affected = cur.rowcount
    if affected:
        log.info("Folder deleted | folder_id=%s | rows_affected=%d", folder_id, affected)
    else:
        log.warning("Folder delete noop | folder_id=%s not found", folder_id)


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------

def create_document(folder_id, original_filename, stored_filename, file_path, file_size, page_count=None):
    log.info(
        "Creating document record | folder_id=%s | filename=%r | stored=%r | size=%s bytes | pages=%s",
        folder_id, original_filename, stored_filename, file_size, page_count
    )
    with get_db() as conn:
        cur = conn.execute(
            """INSERT INTO documents
               (folder_id, original_filename, stored_filename, file_path, file_size, page_count, status)
               VALUES (?, ?, ?, ?, ?, ?, 'pending')""",
            (folder_id, original_filename, stored_filename, file_path, file_size, page_count)
        )
        row = conn.execute("SELECT * FROM documents WHERE id = ?", (cur.lastrowid,)).fetchone()
        result = dict(row)
    log.info(
        "Document record created | doc_id=%s | filename=%r | pages=%s | status=pending",
        result['id'], original_filename, page_count
    )
    return result


def get_documents(folder_id=None):
    if folder_id is not None:
        log.debug("Fetching documents for folder_id=%s", folder_id)
    else:
        log.debug("Fetching all documents (no folder filter)")
    with get_db() as conn:
        if folder_id is not None:
            rows = conn.execute(
                "SELECT * FROM documents WHERE folder_id = ? ORDER BY uploaded_at DESC",
                (folder_id,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM documents ORDER BY uploaded_at DESC"
            ).fetchall()
        results = [dict(r) for r in rows]
    log.debug("Fetched %d document(s) | folder_id=%s", len(results), folder_id)
    return results


def get_document(doc_id):
    log.debug("Fetching document | doc_id=%s", doc_id)
    with get_db() as conn:
        row = conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
        result = dict(row) if row else None
    if result:
        log.debug(
            "Document found | doc_id=%s | filename=%r | status=%s",
            doc_id, result.get('original_filename'), result.get('status')
        )
    else:
        log.warning("Document not found | doc_id=%s", doc_id)
    return result


def update_document_status(doc_id, status, error_message=None, page_count=None, processed_at=None):
    log.info(
        "Updating document status | doc_id=%s | status=%s | page_count=%s",
        doc_id, status, page_count
    )
    if error_message:
        log.warning("Document error | doc_id=%s | error=%s", doc_id, error_message)
    with get_db() as conn:
        if status == 'done':
            conn.execute(
                """UPDATE documents
                   SET status = ?, page_count = ?,
                       processed_at = COALESCE(?, strftime('%Y-%m-%dT%H:%M:%fZ','now'))
                   WHERE id = ?""",
                (status, page_count, processed_at, doc_id)
            )
        elif status == 'failed':
            conn.execute(
                "UPDATE documents SET status = ?, error_message = ? WHERE id = ?",
                (status, error_message, doc_id)
            )
        else:
            conn.execute(
                "UPDATE documents SET status = ? WHERE id = ?",
                (status, doc_id)
            )
    log.info("Document status updated | doc_id=%s | new_status=%s", doc_id, status)


def delete_document(doc_id):
    """Delete DB record (cascade handles trees/nodes/logs). Caller removes file."""
    log.info("Deleting document record | doc_id=%s (cascade: trees, nodes, access_log)", doc_id)
    with get_db() as conn:
        cur = conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
        affected = cur.rowcount
    if affected:
        log.info("Document record deleted | doc_id=%s | rows_affected=%d", doc_id, affected)
        cache_evict(doc_id)
    else:
        log.warning("Document delete noop | doc_id=%s not found", doc_id)


# ---------------------------------------------------------------------------
# Trees
# ---------------------------------------------------------------------------

def save_tree(doc_id, tree_json_str, node_count, depth, doc_name, description):
    json_kb = len(tree_json_str) / 1024
    log.info(
        "Saving tree | doc_id=%s | doc_name=%r | node_count=%s | depth=%s | json_size=%.1fKB",
        doc_id, doc_name, node_count, depth, json_kb
    )
    with get_db() as conn:
        cur = conn.execute(
            """INSERT OR REPLACE INTO document_trees
               (doc_id, tree_json, node_count, depth, doc_name, description)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (doc_id, tree_json_str, node_count, depth, doc_name, description)
        )
        tree_id = cur.lastrowid
    log.info(
        "Tree saved | tree_id=%s | doc_id=%s | node_count=%s | depth=%s | json_size=%.1fKB",
        tree_id, doc_id, node_count, depth, json_kb
    )
    return tree_id


def get_tree(doc_id):
    log.debug("Fetching tree from DB | doc_id=%s", doc_id)
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM document_trees WHERE doc_id = ?", (doc_id,)
        ).fetchone()
        result = dict(row) if row else None
    if result:
        log.debug(
            "Tree found | doc_id=%s | tree_id=%s | node_count=%s | depth=%s",
            doc_id, result.get('id'), result.get('node_count'), result.get('depth')
        )
    else:
        log.debug("No tree found in DB | doc_id=%s", doc_id)
    return result


# ---------------------------------------------------------------------------
# Tree nodes (flatten + insert)
# ---------------------------------------------------------------------------

def flatten_and_insert_nodes(doc_id, tree_id, structure):
    """Walk PageIndex structure recursively, insert one row per node into tree_nodes."""
    log.info("Flattening tree nodes | doc_id=%s | tree_id=%s | root_nodes=%d", doc_id, tree_id, len(structure))
    t0 = time.perf_counter()
    rows = []
    _walk(structure, doc_id, tree_id, parent_node_id=None, level=0, path_prefix="", rows=rows)
    log.debug(
        "Walk complete | doc_id=%s | total_rows=%d | walk_time=%.1fms",
        doc_id, len(rows), (time.perf_counter() - t0) * 1000
    )

    # Compute depth stats
    if rows:
        max_level = max(r[8] for r in rows)  # level is index 8
        log.debug("Node level stats | doc_id=%s | max_depth=%d", doc_id, max_level)

    with get_db() as conn:
        # Clear any prior nodes for this doc (re-processing case)
        del_cur = conn.execute("DELETE FROM tree_nodes WHERE doc_id = ?", (doc_id,))
        deleted = del_cur.rowcount
        if deleted:
            log.debug("Cleared %d prior tree_nodes for doc_id=%s (re-processing)", deleted, doc_id)

        conn.executemany(
            """INSERT INTO tree_nodes
               (doc_id, tree_id, node_id, parent_node_id, title, summary,
                start_page, end_page, level, path)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            rows
        )

    elapsed = (time.perf_counter() - t0) * 1000
    log.info(
        "Nodes inserted | doc_id=%s | tree_id=%s | rows=%d | FTS5 triggers fired | total=%.1fms",
        doc_id, tree_id, len(rows), elapsed
    )


def _walk(nodes, doc_id, tree_id, parent_node_id, level, path_prefix, rows):
    for i, node in enumerate(nodes):
        path = f"{path_prefix}{i}"
        rows.append((
            doc_id,
            tree_id,
            node.get('node_id'),
            parent_node_id,
            node.get('title', ''),
            node.get('summary', ''),
            node.get('start_index'),
            node.get('end_index'),
            level,
            path,
        ))
        children = node.get('nodes', [])
        if children:
            _walk(children, doc_id, tree_id,
                  parent_node_id=node.get('node_id'),
                  level=level + 1,
                  path_prefix=path + "/",
                  rows=rows)


# ---------------------------------------------------------------------------
# Search (FTS5)
# ---------------------------------------------------------------------------

def search_nodes(query, doc_id=None, limit=20):
    scope = f"doc_id={doc_id}" if doc_id is not None else "all docs"
    log.info("FTS5 search | query=%r | scope=%s | limit=%d", query, scope, limit)
    t0 = time.perf_counter()

    with get_db() as conn:
        if doc_id is not None:
            rows = conn.execute(
                """SELECT tn.id, tn.doc_id, tn.node_id, tn.title, tn.summary,
                          tn.start_page, tn.end_page, tn.level, tn.path,
                          dt.doc_name, d.original_filename, d.folder_id,
                          rank
                   FROM search_fts sf
                   JOIN tree_nodes tn ON sf.rowid = tn.id
                   JOIN document_trees dt ON tn.tree_id = dt.id
                   JOIN documents d ON tn.doc_id = d.id
                   WHERE search_fts MATCH ? AND tn.doc_id = ?
                   ORDER BY rank
                   LIMIT ?""",
                (query, doc_id, limit)
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT tn.id, tn.doc_id, tn.node_id, tn.title, tn.summary,
                          tn.start_page, tn.end_page, tn.level, tn.path,
                          dt.doc_name, d.original_filename, d.folder_id,
                          rank
                   FROM search_fts sf
                   JOIN tree_nodes tn ON sf.rowid = tn.id
                   JOIN document_trees dt ON tn.tree_id = dt.id
                   JOIN documents d ON tn.doc_id = d.id
                   WHERE search_fts MATCH ?
                   ORDER BY rank
                   LIMIT ?""",
                (query, limit)
            ).fetchall()
        results = [dict(r) for r in rows]

    query_ms = (time.perf_counter() - t0) * 1000
    log.info(
        "FTS5 search complete | query=%r | results=%d | scope=%s | query_time=%.1fms",
        query, len(results), scope, query_ms
    )

    if results:
        top = results[0]
        log.debug(
            "Top result | node_id=%s | title=%r | doc=%r | rank=%.4f",
            top.get('node_id'), top.get('title'), top.get('doc_name'), top.get('rank', 0)
        )

    # Log to query_history + node_access_log
    if results:
        node_ids = [r['id'] for r in results]
        doc_ids = list({r['doc_id'] for r in results})
        log.debug(
            "Logging query to history | query=%r | docs_hit=%s | nodes_hit=%d",
            query, doc_ids, len(node_ids)
        )
        with get_db() as conn:
            cur = conn.execute(
                """INSERT INTO query_history (query_text, doc_ids_searched, result_node_ids)
                   VALUES (?, ?, ?)""",
                (query, json.dumps(doc_ids), json.dumps(node_ids))
            )
            qid = cur.lastrowid
            conn.executemany(
                """INSERT INTO node_access_log (node_db_id, doc_id, query_id)
                   VALUES (?, ?, ?)""",
                [(nid, None, qid) for nid in node_ids]
            )
        log.debug("Query history recorded | query_id=%s | access_log_entries=%d", qid, len(node_ids))
    else:
        log.debug("No results to log for query=%r", query)

    return results


# ---------------------------------------------------------------------------
# Page texts
# ---------------------------------------------------------------------------

def save_page_texts(doc_id: int, page_texts: list):
    """Persist extracted page texts (list of strings, 0-indexed → stored as 1-indexed page_num)."""
    log.info("Saving page texts | doc_id=%s | pages=%d", doc_id, len(page_texts))
    rows = [(doc_id, i + 1, text or '') for i, text in enumerate(page_texts)]
    with get_db() as conn:
        conn.execute("DELETE FROM document_pages WHERE doc_id = ?", (doc_id,))
        conn.executemany(
            "INSERT INTO document_pages (doc_id, page_num, text) VALUES (?, ?, ?)",
            rows,
        )
    log.info("Page texts saved | doc_id=%s | pages=%d", doc_id, len(rows))


# ---------------------------------------------------------------------------
# Annotations
# ---------------------------------------------------------------------------

def save_annotation(doc_id: int, node_id: str, anchor_page: int, anchor_title: str, anchor_path: str, body: str,
                    source: str = 'human', note_type: str = None, severity: str = None):
    """Upsert an annotation keyed by (doc_id, node_id). Returns the saved annotation dict.

    node_id is the PageIndex-assigned string (unique per node within a document).
    anchor_page / anchor_title are stored solely for re-anchoring after re-processing.
    source: 'human' | 'agent'
    note_type: 'summary' | 'flag' | 'quote' | 'cross_ref' | None
    severity: 'low' | 'medium' | 'high' | None
    """
    log.info("Saving annotation | doc_id=%s | node_id=%s | anchor_page=%s | source=%s", doc_id, node_id, anchor_page, source)
    with get_db() as conn:
        row = conn.execute(
            "SELECT id FROM annotations WHERE doc_id = ? AND node_id = ? AND source = ?",
            (doc_id, node_id, source)
        ).fetchone()
        if row:
            conn.execute(
                """UPDATE annotations
                   SET body = ?, anchor_page = ?, anchor_title = ?, anchor_path = ?,
                       note_type = ?, severity = ?,
                       updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')
                   WHERE id = ?""",
                (body, anchor_page, anchor_title, anchor_path, note_type, severity, row['id'])
            )
            ann_id = row['id']
        else:
            cur = conn.execute(
                """INSERT INTO annotations (doc_id, node_id, anchor_page, anchor_title, anchor_path, body, source, note_type, severity)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (doc_id, node_id, anchor_page, anchor_title, anchor_path, body, source, note_type, severity)
            )
            ann_id = cur.lastrowid
        result = dict(conn.execute(
            "SELECT * FROM annotations WHERE id = ?", (ann_id,)
        ).fetchone())
    log.info("Annotation saved | id=%s | doc_id=%s | node_id=%s | source=%s", ann_id, doc_id, node_id, source)
    return result


def save_agent_notes(doc_id: int, notes: list):
    """Bulk-replace all agent-generated notes for a document.

    notes: list of dicts with keys: node_id, anchor_page, anchor_title, anchor_path, body, note_type, severity
    """
    log.info("Saving agent notes | doc_id=%s | count=%d", doc_id, len(notes))
    with get_db() as conn:
        conn.execute("DELETE FROM annotations WHERE doc_id = ? AND source = 'agent'", (doc_id,))
        for n in notes:
            conn.execute(
                """INSERT INTO annotations (doc_id, node_id, anchor_page, anchor_title, anchor_path, body, source, note_type, severity)
                   VALUES (?, ?, ?, ?, ?, ?, 'agent', ?, ?)""",
                (doc_id, n.get('node_id') or f"page-{n.get('anchor_page', 1)}",
                 n.get('anchor_page', 1), n.get('anchor_title', ''), n.get('anchor_path', ''),
                 n.get('body', ''), n.get('note_type'), n.get('severity'))
            )
    log.info("Agent notes saved | doc_id=%s | count=%d", doc_id, len(notes))


def update_notes_status(doc_id: int, status: str):
    """Update the notes_status field on a document (pending | generating | done | failed)."""
    log.info("Updating notes status | doc_id=%s | status=%s", doc_id, status)
    with get_db() as conn:
        conn.execute("UPDATE documents SET notes_status = ? WHERE id = ?", (status, doc_id))


def get_all_page_texts(doc_id: int) -> str:
    """Return all page texts for a document concatenated with page markers."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT page_num, text FROM document_pages WHERE doc_id = ? ORDER BY page_num",
            (doc_id,)
        ).fetchall()
    if not rows:
        return ''
    parts = [f"[PAGE {r['page_num']}]\n{r['text']}" for r in rows]
    return '\n\n'.join(parts)


def get_annotations(doc_id: int) -> list:
    """Return all annotations for a document, ordered by page."""
    log.debug("Fetching annotations | doc_id=%s", doc_id)
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM annotations WHERE doc_id = ? ORDER BY anchor_page",
            (doc_id,)
        ).fetchall()
        return [dict(r) for r in rows]


def delete_annotation(annotation_id: int):
    log.info("Deleting annotation | id=%s", annotation_id)
    with get_db() as conn:
        conn.execute("DELETE FROM annotations WHERE id = ?", (annotation_id,))


def re_anchor_annotations(doc_id: int):
    """After re-processing, re-link annotation.node_id using page-number anchor (primary)
    and title match (tie-breaker). Marks unmatched annotations as orphans."""
    log.info("Re-anchoring annotations | doc_id=%s", doc_id)
    with get_db() as conn:
        annotations = conn.execute(
            "SELECT id, anchor_page, anchor_title FROM annotations WHERE doc_id = ?",
            (doc_id,)
        ).fetchall()
        if not annotations:
            log.debug("No annotations to re-anchor | doc_id=%s", doc_id)
            return
        nodes = conn.execute(
            "SELECT node_id, title, start_page FROM tree_nodes WHERE doc_id = ?",
            (doc_id,)
        ).fetchall()

        # Index nodes by start_page
        page_to_nodes: dict = {}
        for n in nodes:
            p = n['start_page']
            if p not in page_to_nodes:
                page_to_nodes[p] = []
            page_to_nodes[p].append(dict(n))

        re_linked = orphaned = 0
        for ann in annotations:
            candidates = page_to_nodes.get(ann['anchor_page'], [])
            if not candidates:
                conn.execute(
                    "UPDATE annotations SET is_orphan = 1 WHERE id = ?",
                    (ann['id'],)
                )
                orphaned += 1
            else:
                # Prefer title match as tie-breaker
                matched = next(
                    (n for n in candidates if n['title'] == ann['anchor_title']),
                    candidates[0]
                )
                conn.execute(
                    "UPDATE annotations SET is_orphan = 0, node_id = ? WHERE id = ?",
                    (matched['node_id'], ann['id'])
                )
                re_linked += 1

    log.info(
        "Annotations re-anchored | doc_id=%s | re_linked=%d | orphaned=%d",
        doc_id, re_linked, orphaned
    )


# ---------------------------------------------------------------------------
# Cases (Adversarial Pipeline)
# ---------------------------------------------------------------------------

def create_case(title: str, model: str = "gpt-4o-2024-11-20") -> dict:
    log.info("Creating case | title=%r | model=%s", title, model)
    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO cases (title, model) VALUES (?, ?)",
            (title, model)
        )
        row = conn.execute("SELECT * FROM cases WHERE id = ?", (cur.lastrowid,)).fetchone()
        result = dict(row)
    log.info("Case created | id=%s | title=%r", result['id'], title)
    return result


def get_case(case_id: int) -> dict | None:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM cases WHERE id = ?", (case_id,)).fetchone()
        return dict(row) if row else None


def update_case_party_names(case_id: int, role: str, name: str):
    col = 'petitioner_name' if role == 'Petitioner' else 'respondent_name'
    with get_db() as conn:
        conn.execute(f"UPDATE cases SET {col} = ? WHERE id = ?", (name, case_id))


def list_cases() -> list:
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM cases ORDER BY created_at DESC").fetchall()
        return [dict(r) for r in rows]


def list_cases_with_summary() -> list:
    """
    Like list_cases() but includes petitioner/respondent document filenames
    for display on the dashboard cards.
    """
    with get_db() as conn:
        cases = [dict(r) for r in conn.execute(
            "SELECT * FROM cases WHERE archived = 0 ORDER BY created_at DESC"
        ).fetchall()]

        if not cases:
            return cases

        case_ids = [c["id"] for c in cases]
        doc_rows = conn.execute(
            f"""SELECT cd.case_id, cd.party_role, d.original_filename
                FROM case_documents cd
                LEFT JOIN documents d ON cd.doc_id = d.id
                WHERE cd.case_id IN ({','.join('?' * len(case_ids))})""",
            case_ids,
        ).fetchall()

    doc_map: dict[int, dict] = {}
    for row in doc_rows:
        doc_map.setdefault(row["case_id"], {})[row["party_role"]] = row["original_filename"] or ""

    for case in cases:
        parties = doc_map.get(case["id"], {})
        case["petitioner_doc"] = parties.get("Petitioner", None)
        case["respondent_doc"] = parties.get("Respondent", None)

    return cases


def update_case_status(case_id: int, status: str, error_message: str | None = None):
    log.info("Updating case status | case_id=%s | status=%s", case_id, status)
    with get_db() as conn:
        conn.execute(
            """UPDATE cases SET status = ?, error_message = ?,
               updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')
               WHERE id = ?""",
            (status, error_message, case_id)
        )


def add_case_document(case_id: int, doc_id: int | None, party_role: str, document_type: str = "Petition") -> dict:
    log.info("Adding case document | case_id=%s | party_role=%s | doc_id=%s", case_id, party_role, doc_id)
    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO case_documents (case_id, doc_id, party_role, document_type) VALUES (?, ?, ?, ?)",
            (case_id, doc_id, party_role, document_type)
        )
        row = conn.execute("SELECT * FROM case_documents WHERE id = ?", (cur.lastrowid,)).fetchone()
        return dict(row)


def get_case_documents(case_id: int) -> list:
    with get_db() as conn:
        rows = conn.execute(
            """SELECT cd.*,
                      d.original_filename, d.page_count, d.uploaded_at, d.status AS doc_status
               FROM case_documents cd
               LEFT JOIN documents d ON cd.doc_id = d.id
               WHERE cd.case_id = ?
               ORDER BY cd.id""",
            (case_id,)
        ).fetchall()
        return [dict(r) for r in rows]


def detach_case_document(case_id: int, case_doc_id: int):
    with get_db() as conn:
        conn.execute(
            "DELETE FROM case_documents WHERE id = ? AND case_id = ?",
            (case_doc_id, case_id)
        )


def save_clerk_output(case_doc_id: int, clerk_output_json: str):
    log.info("Saving clerk output | case_doc_id=%s", case_doc_id)
    with get_db() as conn:
        conn.execute(
            "UPDATE case_documents SET clerk_output = ?, clerk_status = 'done' WHERE id = ?",
            (clerk_output_json, case_doc_id)
        )


def set_clerk_status(case_doc_id: int, status: str):
    with get_db() as conn:
        conn.execute(
            "UPDATE case_documents SET clerk_status = ? WHERE id = ?",
            (status, case_doc_id)
        )


def save_verifier_output(case_doc_id: int, verifier_output_json: str):
    log.info("Saving verifier output | case_doc_id=%s", case_doc_id)
    with get_db() as conn:
        conn.execute(
            "UPDATE case_documents SET verifier_output = ?, verifier_status = 'done' WHERE id = ?",
            (verifier_output_json, case_doc_id)
        )


def set_verifier_status(case_doc_id: int, status: str):
    with get_db() as conn:
        conn.execute(
            "UPDATE case_documents SET verifier_status = ? WHERE id = ?",
            (status, case_doc_id)
        )


def save_adversarial_matrix(case_id: int, matrix_json: str):
    log.info("Saving adversarial matrix | case_id=%s", case_id)
    with get_db() as conn:
        existing = conn.execute(
            "SELECT id FROM case_results WHERE case_id = ?", (case_id,)
        ).fetchone()
        if existing:
            conn.execute(
                """UPDATE case_results SET adversarial_matrix = ?, human_review_status = 'pending',
                   updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE case_id = ?""",
                (matrix_json, case_id)
            )
        else:
            conn.execute(
                "INSERT INTO case_results (case_id, adversarial_matrix) VALUES (?, ?)",
                (case_id, matrix_json)
            )


def save_citation_audit(case_id: int, audit_json: str):
    log.info("Saving citation audit | case_id=%s", case_id)
    with get_db() as conn:
        existing = conn.execute(
            "SELECT id FROM case_results WHERE case_id = ?", (case_id,)
        ).fetchone()
        if existing:
            conn.execute(
                """UPDATE case_results SET citation_audit = ?,
                   updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE case_id = ?""",
                (audit_json, case_id),
            )
        else:
            conn.execute(
                "INSERT INTO case_results (case_id, citation_audit) VALUES (?, ?)",
                (case_id, audit_json),
            )


def get_case_result(case_id: int) -> dict | None:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM case_results WHERE case_id = ?", (case_id,)).fetchone()
        return dict(row) if row else None


def approve_matrix(case_id: int):
    log.info("Human approved adversarial matrix | case_id=%s", case_id)
    with get_db() as conn:
        conn.execute(
            """UPDATE case_results SET human_review_status = 'approved',
               updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE case_id = ?""",
            (case_id,)
        )
    update_case_status(case_id, "review_approved")


def reject_matrix(case_id: int, reason: str = ""):
    log.info("Human rejected adversarial matrix | case_id=%s | reason=%r", case_id, reason[:80] if reason else "")
    with get_db() as conn:
        conn.execute(
            """UPDATE case_results
               SET human_review_status = 'rejected',
                   rejection_reason    = ?,
                   updated_at          = strftime('%Y-%m-%dT%H:%M:%fZ','now')
               WHERE case_id = ?""",
            (reason or None, case_id)
        )
    update_case_status(case_id, "review_rejected")


def save_sifted_matrix(case_id: int, sifted_json: str):
    """Save the assembled {adversarial_matrix, procedural_analysis} envelope."""
    log.info("Saving sifted matrix | case_id=%s", case_id)
    with get_db() as conn:
        existing = conn.execute(
            "SELECT id FROM case_results WHERE case_id = ?", (case_id,)
        ).fetchone()
        if existing:
            conn.execute(
                """UPDATE case_results SET sifted_matrix = ?,
                   updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE case_id = ?""",
                (sifted_json, case_id)
            )
        else:
            conn.execute(
                "INSERT INTO case_results (case_id, sifted_matrix) VALUES (?, ?)",
                (case_id, sifted_json)
            )


def save_stress_tested_matrix(case_id: int, stress_json: str):
    """Save the assembled {sifted_matrix, stress_tested_matrix} envelope."""
    log.info("Saving stress-tested matrix | case_id=%s", case_id)
    with get_db() as conn:
        conn.execute(
            """UPDATE case_results SET stress_tested_matrix = ?,
               updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE case_id = ?""",
            (stress_json, case_id)
        )


def save_draft_court_order(case_id: int, order_json: str):
    log.info("Saving draft court order | case_id=%s", case_id)
    with get_db() as conn:
        conn.execute(
            """UPDATE case_results SET draft_court_order = ?,
               updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE case_id = ?""",
            (order_json, case_id)
        )


def save_formal_court_order(case_id: int, formal_json: str):
    log.info("Saving formal court order | case_id=%s", case_id)
    with get_db() as conn:
        conn.execute(
            """UPDATE case_results SET formal_court_order = ?,
               updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE case_id = ?""",
            (formal_json, case_id)
        )


def delete_case(case_id: int):
    """Soft-delete: move to archive."""
    log.info("Archiving case | case_id=%s", case_id)
    with get_db() as conn:
        conn.execute(
            "UPDATE cases SET archived = 1, archived_at = strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id = ?",
            (case_id,)
        )


def restore_case(case_id: int):
    log.info("Restoring case | case_id=%s", case_id)
    with get_db() as conn:
        conn.execute(
            "UPDATE cases SET archived = 0, archived_at = NULL WHERE id = ?",
            (case_id,)
        )


def purge_case(case_id: int):
    """Permanently delete an archived case."""
    log.info("Purging case | case_id=%s", case_id)
    with get_db() as conn:
        conn.execute("DELETE FROM cases WHERE id = ? AND archived = 1", (case_id,))


def list_archived_cases() -> list:
    with get_db() as conn:
        cases = [dict(r) for r in conn.execute(
            "SELECT * FROM cases WHERE archived = 1 ORDER BY archived_at DESC"
        ).fetchall()]

        if not cases:
            return cases

        case_ids = [c["id"] for c in cases]
        doc_rows = conn.execute(
            f"""SELECT cd.case_id, cd.party_role, d.original_filename
                FROM case_documents cd
                LEFT JOIN documents d ON cd.doc_id = d.id
                WHERE cd.case_id IN ({','.join('?' * len(case_ids))})""",
            case_ids,
        ).fetchall()

    doc_map: dict[int, dict] = {}
    for row in doc_rows:
        doc_map.setdefault(row["case_id"], {})[row["party_role"]] = row["original_filename"] or ""

    for case in cases:
        parties = doc_map.get(case["id"], {})
        case["petitioner_doc"] = parties.get("Petitioner", None)
        case["respondent_doc"] = parties.get("Respondent", None)

    return cases


def get_page_text(doc_id: int, page_num: int):
    """Return {'page_num': N, 'text': '...', 'total_pages': M} or None if not stored."""
    log.debug("Fetching page text | doc_id=%s | page_num=%s", doc_id, page_num)
    with get_db() as conn:
        row = conn.execute(
            "SELECT text FROM document_pages WHERE doc_id = ? AND page_num = ?",
            (doc_id, page_num),
        ).fetchone()
        total = conn.execute(
            "SELECT COUNT(*) FROM document_pages WHERE doc_id = ?",
            (doc_id,),
        ).fetchone()[0]
    if row is None:
        log.debug("Page text not found in DB | doc_id=%s | page_num=%s", doc_id, page_num)
        return None
    return {'page_num': page_num, 'text': row[0], 'total_pages': total}
