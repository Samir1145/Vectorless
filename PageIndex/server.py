import os
import sys
import json
import uuid
import time
import logging
import logging.handlers
import threading
import urllib.request
from collections import deque
from queue import Queue, Empty
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, request, jsonify, send_file, Response, g
from flask_cors import CORS

from pageindex import page_index_main
from pageindex.utils import ConfigLoader, get_page_tokens
from pageindex.agents import run_note_builder
import db

# Load .env from the same directory as this file
_ENV_PATH = Path(__file__).parent / '.env'
load_dotenv(_ENV_PATH)

# PageIndex / litellm expects OPENAI_API_KEY — support CHATGPT_API_KEY alias
if not os.environ.get('OPENAI_API_KEY') and os.environ.get('CHATGPT_API_KEY'):
    os.environ['OPENAI_API_KEY'] = os.environ['CHATGPT_API_KEY']

app = Flask(__name__)
CORS(app)


# ---------------------------------------------------------------------------
# Real-time log infrastructure
# ---------------------------------------------------------------------------

_log_buffer: deque = deque(maxlen=500)
_log_lock = threading.Lock()
_log_subscribers: list = []


def _push_log(level: str, message: str):
    message = message.strip()
    if not message:
        return
    entry = {
        'id': int(time.time() * 1000),
        'ts': datetime.now().strftime('%H:%M:%S.%f')[:-3],
        'level': level,   # debug | info | warn | error
        'msg': message,
    }
    with _log_lock:
        _log_buffer.append(entry)
        dead = []
        for q in _log_subscribers:
            try:
                q.put_nowait(entry)
            except Exception:
                dead.append(q)
        for q in dead:
            _log_subscribers.remove(q)


def _webhook_url() -> str:
    """Return the webhook URL from env var or config.yaml (env takes priority)."""
    from_env = os.environ.get('WEBHOOK_URL', '').strip()
    if from_env:
        return from_env
    try:
        import yaml
        cfg_path = Path(__file__).parent / 'pageindex' / 'config.yaml'
        with open(cfg_path) as f:
            return yaml.safe_load(f).get('monitoring', {}).get('webhook_url', '').strip()
    except Exception:
        return ''


def _fire_webhook(level: str, message: str, ts: str) -> None:
    """POST an error alert to the configured webhook in a background thread.

    Supports Slack incoming webhooks (auto-detected by URL) and generic JSON.
    The payload is:
        {"level": "error", "message": "...", "ts": "HH:MM:SS.mmm", "source": "pageindex"}
    For Slack URLs the "text" field is added for compatibility.
    """
    url = _webhook_url()
    if not url:
        return

    def _send():
        try:
            payload: dict = {
                "level":   level,
                "message": message,
                "ts":      ts,
                "source":  "pageindex",
            }
            if "hooks.slack.com" in url or "slack.com/services" in url:
                payload["text"] = f"[{level.upper()}] {message}"
            if "discord.com/api/webhooks" in url:
                payload["content"] = f"[{level.upper()}] {message}"
            body = json.dumps(payload).encode()
            req  = urllib.request.Request(
                url, data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5):
                pass
        except Exception:
            pass  # webhook failures must never crash the app

    threading.Thread(target=_send, daemon=True).start()


class _MemHandler(logging.Handler):
    _MAP = {logging.DEBUG: 'debug', logging.INFO: 'info',
            logging.WARNING: 'warn', logging.ERROR: 'error',
            logging.CRITICAL: 'error'}
    # Werkzeug request lines to suppress (noisy polling paths)
    _QUIET = ('/api/logs', '/health', '/api/documents/')
    # Werkzeug startup banners already captured via stdout — skip logger copy
    _WERKZEUG_SKIP = ('WARNING: This is a development server', 'Press CTRL+C')

    def emit(self, record):
        try:
            msg = record.getMessage()
            if record.name == 'werkzeug':
                # Drop noisy polling HTTP lines
                if any(q in msg for q in self._QUIET):
                    return
                # Drop startup banners (already captured by _StdoutCapture)
                if any(s in msg for s in self._WERKZEUG_SKIP):
                    return
            level = self._MAP.get(record.levelno, 'info')
            ts    = datetime.now().strftime('%H:%M:%S.%f')[:-3]
            _push_log(level, self.format(record))
            if level == 'error':
                _fire_webhook(level, self.format(record), ts)
        except Exception:
            pass


class _StdoutCapture:
    """Tee print() calls to terminal + log buffer."""
    def __init__(self, original, level):
        self._orig = original
        self._level = level

    def write(self, text):
        self._orig.write(text)
        stripped = text.strip()
        if stripped:
            _push_log(self._level, stripped)

    def flush(self):
        self._orig.flush()

    def __getattr__(self, name):
        return getattr(self._orig, name)


def _setup_logging():
    fmt = logging.Formatter('%(asctime)s %(levelname)-8s %(name)s: %(message)s',
                            datefmt='%Y-%m-%d %H:%M:%S')
    mem_fmt = logging.Formatter('%(name)s: %(message)s')

    mem_handler = _MemHandler()
    mem_handler.setFormatter(mem_fmt)

    # Attach handlers ONLY to root — child loggers propagate up
    root = logging.getLogger()
    root.addHandler(mem_handler)
    root.setLevel(logging.DEBUG)

    # Rotating file handler — reads path/size/backups from config.yaml
    try:
        import yaml
        cfg_path = Path(__file__).parent / 'pageindex' / 'config.yaml'
        with open(cfg_path) as f:
            mon_cfg = yaml.safe_load(f).get('monitoring', {})
        log_file_rel = mon_cfg.get('log_file', 'logs/server.log').strip()
        if log_file_rel:
            log_file = (Path(__file__).parent / log_file_rel).resolve()
            log_file.parent.mkdir(parents=True, exist_ok=True)
            file_handler = logging.handlers.RotatingFileHandler(
                log_file,
                maxBytes=int(mon_cfg.get('log_file_max_bytes', 10 * 1024 * 1024)),
                backupCount=int(mon_cfg.get('log_file_backup_count', 5)),
                encoding='utf-8',
            )
            file_handler.setFormatter(fmt)
            root.addHandler(file_handler)
    except Exception as exc:
        # Log file setup failures are non-fatal
        mem_handler.emit(logging.LogRecord(
            'server', logging.WARNING, '', 0,
            f'File log setup failed: {exc}', (), None,
        ))

    # Ensure named loggers propagate (default) and have no level filter
    for name in ('werkzeug', 'pageindex', 'db'):
        lg = logging.getLogger(name)
        lg.setLevel(logging.DEBUG)
        lg.propagate = True   # let root handler catch it (no duplicate handler)

    sys.stdout = _StdoutCapture(sys.stdout, 'info')
    sys.stderr = _StdoutCapture(sys.stderr, 'error')


# ---------------------------------------------------------------------------
# Request / response logging hooks
# ---------------------------------------------------------------------------

# Paths too noisy to log on every hit
_SKIP_LOG_PATHS = {'/api/logs/stream', '/api/logs', '/health', '/api/metrics'}
_SKIP_LOG_PREFIXES = ('/api/documents/', )   # /status polling & file serve


def _should_log_request(path: str, method: str) -> bool:
    if path in _SKIP_LOG_PATHS:
        return False
    # Skip status polls and file serves but keep mutations
    if method == 'GET' and any(
        path.startswith(p) and (path.endswith('/status') or path.endswith('/file'))
        for p in _SKIP_LOG_PREFIXES
    ):
        return False
    return True


@app.before_request
def _before():
    g._t0 = time.perf_counter()
    path = request.path
    if not _should_log_request(path, request.method):
        return
    qs = f'?{request.query_string.decode()}' if request.query_string else ''
    content_type = request.content_type or ''
    if 'multipart' in content_type:
        body_hint = f'multipart [{request.content_length or 0} bytes]'
    elif 'json' in content_type:
        try:
            body_hint = json.dumps(request.get_json(silent=True, force=True))
        except Exception:
            body_hint = '<unparseable>'
    else:
        body_hint = ''
    detail = f' · {body_hint}' if body_hint else ''
    _push_log('debug', f'→ {request.method} {path}{qs}{detail}')


@app.after_request
def _after(response):
    if not hasattr(g, '_t0'):
        return response
    path = request.path
    if not _should_log_request(path, request.method):
        return response
    ms = (time.perf_counter() - g._t0) * 1000
    lvl = 'error' if response.status_code >= 500 else \
          'warn'  if response.status_code >= 400 else 'info'
    _push_log(lvl, f'← {response.status_code} {request.method} {path} ({ms:.0f}ms)')
    return response


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _utcnow():
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3] + 'Z'


def _get_max_page(nodes):
    mx = 0
    for n in nodes:
        if (n.get('end_index') or 0) > mx:
            mx = n['end_index']
        if n.get('nodes'):
            mx = max(mx, _get_max_page(n['nodes']))
    return mx


def _count_and_depth(nodes, depth=0):
    count = len(nodes)
    max_d = depth
    for n in nodes:
        children = n.get('nodes', [])
        if children:
            c, d = _count_and_depth(children, depth + 1)
            count += c
            max_d = max(max_d, d)
    return count, max_d


# ---------------------------------------------------------------------------
# Background PDF processing + cancellation
# ---------------------------------------------------------------------------

_cancel_flags: dict = {}   # doc_id -> threading.Event (set = cancel requested)
_cancel_lock = threading.Lock()


def _is_cancelled(doc_id: int) -> bool:
    with _cancel_lock:
        flag = _cancel_flags.get(doc_id)
    return flag is not None and flag.is_set()


def _register_cancel_flag(doc_id: int) -> threading.Event:
    flag = threading.Event()
    with _cancel_lock:
        _cancel_flags[doc_id] = flag
    return flag


def _clear_cancel_flag(doc_id: int):
    with _cancel_lock:
        _cancel_flags.pop(doc_id, None)


def process_document(doc_id: int, file_path: str):
    t_start = time.perf_counter()
    filename = Path(file_path).name
    flag = _register_cancel_flag(doc_id)
    try:
        _push_log('info', f'[doc:{doc_id}] ▶ Processing started — "{filename}"')
        db.update_document_status(doc_id, 'processing')

        _push_log('debug', f'[doc:{doc_id}] Loading PageIndex config...')
        opt = ConfigLoader().load({})
        _push_log('debug', f'[doc:{doc_id}] Config: model={opt.model}  '
                            f'toc_check={opt.toc_check_page_num}  '
                            f'max_pages_per_node={opt.max_page_num_each_node}')

        # Detect image-based PDFs before starting — gives the user an early warning
        try:
            from pageindex.docling_ocr import is_image_based
            if is_image_based(file_path):
                _push_log('info', f'[doc:{doc_id}] 🔍 Image-based PDF detected (no selectable text) — OCR (ocrmypdf/Tesseract) will run automatically')
            else:
                _push_log('debug', f'[doc:{doc_id}] Selectable text found — standard extraction will be used')
        except Exception:
            pass  # detection is best-effort, don't block processing

        _push_log('info', f'[doc:{doc_id}] Running PageIndex on "{filename}"...')
        t_pi = time.perf_counter()
        result = page_index_main(file_path, opt)
        pi_ms = (time.perf_counter() - t_pi) * 1000
        _push_log('info', f'[doc:{doc_id}] PageIndex finished in {pi_ms:.0f}ms')

        # Check if cancelled while LLM was running — discard result
        if flag.is_set():
            _push_log('info', f'[doc:{doc_id}] ⏹ Cancelled — discarding result, reverting to pending')
            db.update_document_status(doc_id, 'pending')
            return

        structure = result.get('structure', [])
        doc_name = result.get('doc_name', '')
        description = result.get('doc_description', '')
        if not description and structure:
            description = structure[0].get('summary', '')

        node_count, depth = _count_and_depth(structure)
        page_count = _get_max_page(structure)
        _push_log('debug', f'[doc:{doc_id}] Tree stats: {node_count} nodes, '
                            f'depth={depth}, pages=1–{page_count}')

        _push_log('debug', f'[doc:{doc_id}] Serialising tree JSON...')
        tree_json_str = json.dumps(result)
        json_kb = len(tree_json_str) // 1024
        _push_log('debug', f'[doc:{doc_id}] Tree JSON size: {json_kb} KB')

        _push_log('debug', f'[doc:{doc_id}] Saving tree to database...')
        tree_id = db.save_tree(doc_id, tree_json_str, node_count, depth, doc_name, description)
        _push_log('debug', f'[doc:{doc_id}] Tree saved — tree_id={tree_id}')

        _push_log('debug', f'[doc:{doc_id}] Flattening {node_count} nodes into tree_nodes + FTS5...')
        db.flatten_and_insert_nodes(doc_id, tree_id, structure)
        _push_log('debug', f'[doc:{doc_id}] FTS5 index updated')

        db.re_anchor_annotations(doc_id)
        _push_log('debug', f'[doc:{doc_id}] Annotations re-anchored')

        sidecar = str(file_path).replace('.pdf', '_structure.json')
        with open(sidecar, 'w', encoding='utf-8') as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
        _push_log('debug', f'[doc:{doc_id}] Sidecar JSON written → {Path(sidecar).name}')

        db.update_document_status(doc_id, 'done', page_count=page_count, processed_at=_utcnow())
        db.cache_put(doc_id, result)

        # Store extracted page texts so the Text tab can serve them from DB
        _push_log('debug', f'[doc:{doc_id}] Storing page texts ({page_count} pages)...')
        page_list = get_page_tokens(file_path)
        db.save_page_texts(doc_id, [text for text, _ in page_list])
        _push_log('debug', f'[doc:{doc_id}] Page texts stored')

        total_ms = (time.perf_counter() - t_start) * 1000
        _push_log('info', f'[doc:{doc_id}] ✓ Done — {node_count} nodes · '
                           f'{page_count} pages · depth {depth} · {total_ms:.0f}ms total')

    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        if not flag.is_set():
            db.update_document_status(doc_id, 'failed', error_message=str(e))
            _push_log('error', f'[doc:{doc_id}] ✗ Failed — {e}')
            _push_log('error', f'[doc:{doc_id}] Traceback:\n{tb}')
        else:
            _push_log('info', f'[doc:{doc_id}] ⏹ Cancelled (exception during cancel) — reverting to pending')
            db.update_document_status(doc_id, 'pending')
    finally:
        _clear_cancel_flag(doc_id)


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@app.route('/health', methods=['HEAD', 'GET'])
def health():
    return jsonify({'status': 'ok'}), 200


# ---------------------------------------------------------------------------
# Folders
# ---------------------------------------------------------------------------

@app.route('/api/folders', methods=['GET'])
def list_folders():
    folders = db.get_folders()
    _push_log('debug', f'[folders] Listed {len(folders)} folder(s)')
    return jsonify({'folders': folders})


@app.route('/api/folders', methods=['POST'])
def create_folder():
    data = request.get_json(force=True)
    name = (data.get('name') or '').strip()
    parent_id = data.get('parent_id')
    if not name:
        _push_log('warn', '[folders] Create failed — name is required')
        return jsonify({'error': 'name is required'}), 400
    folder = db.create_folder(name, parent_id)
    parent_hint = f' (parent={parent_id})' if parent_id else ''
    _push_log('info', f'[folders] Created folder "{name}" → id={folder["id"]}{parent_hint}')
    return jsonify(folder), 201


@app.route('/api/folders/<int:folder_id>', methods=['DELETE'])
def delete_folder(folder_id):
    _push_log('info', f'[folders] Deleting folder id={folder_id} (cascades to documents)')
    db.delete_folder(folder_id)
    _push_log('info', f'[folders] Folder id={folder_id} deleted')
    return '', 204


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------

@app.route('/api/documents', methods=['GET'])
def list_documents():
    folder_id = request.args.get('folder_id', type=int)
    docs = db.get_documents(folder_id)
    scope = f'folder={folder_id}' if folder_id else 'all folders'
    _push_log('debug', f'[documents] Listed {len(docs)} document(s) [{scope}]')
    return jsonify({'documents': docs})


@app.route('/api/documents/<int:doc_id>', methods=['GET'])
def get_document(doc_id):
    doc = db.get_document(doc_id)
    if not doc:
        _push_log('warn', f'[documents] GET doc:{doc_id} — not found')
        return jsonify({'error': 'Not found'}), 404

    _push_log('debug', f'[documents] Fetching doc:{doc_id} '
                        f'"{doc["original_filename"]}" [status={doc["status"]}]')

    if doc['status'] == 'done':
        tree = db.cache_get(doc_id)
        if tree is not None:
            _push_log('debug', f'[documents] doc:{doc_id} tree served from LRU cache')
        else:
            _push_log('debug', f'[documents] doc:{doc_id} cache miss — loading tree from DB')
            row = db.get_tree(doc_id)
            if row:
                tree = json.loads(row['tree_json'])
                db.cache_put(doc_id, tree)
                _push_log('debug', f'[documents] doc:{doc_id} tree loaded from DB '
                                    f'({row["node_count"]} nodes) and cached')
            else:
                _push_log('warn', f'[documents] doc:{doc_id} status=done but no tree found in DB')
        doc['tree'] = tree
    else:
        doc['tree'] = None
        _push_log('debug', f'[documents] doc:{doc_id} tree skipped [status={doc["status"]}]')

    return jsonify(doc)


@app.route('/api/documents/<int:doc_id>/status', methods=['GET'])
def get_document_status(doc_id):
    doc = db.get_document(doc_id)
    if not doc:
        return jsonify({'error': 'Not found'}), 404
    return jsonify({
        'id': doc_id,
        'status': doc['status'],
        'page_count': doc['page_count'],
        'error_message': doc['error_message'],
        'processed_at': doc['processed_at'],
        'notes_status': doc.get('notes_status', 'pending'),
    })


@app.route('/api/documents/upload', methods=['POST'])
def upload_document():
    if 'pdf' not in request.files:
        _push_log('warn', '[upload] Request missing PDF file field')
        return jsonify({'error': 'No PDF file provided'}), 400

    pdf_file = request.files['pdf']
    if not pdf_file.filename.lower().endswith('.pdf'):
        _push_log('warn', f'[upload] Rejected "{pdf_file.filename}" — not a PDF')
        return jsonify({'error': 'File must be a PDF'}), 400

    folder_id = request.form.get('folder_id', type=int)
    _push_log('info', f'[upload] Receiving "{pdf_file.filename}" '
                       f'[folder={folder_id or "root"}]')

    dest_dir = Path(db.UPLOADS_ROOT) / str(folder_id if folder_id is not None else 'root')
    dest_dir.mkdir(parents=True, exist_ok=True)

    stored_name = f"{uuid.uuid4()}.pdf"
    file_path = dest_dir / stored_name

    _push_log('debug', f'[upload] Writing to disk: {file_path}')
    pdf_file.save(str(file_path))
    file_size = file_path.stat().st_size

    _push_log('debug', f'[upload] File saved — {file_size // 1024} KB '
                        f'at {file_path.name}')

    page_count = None
    try:
        import fitz
        with fitz.open(str(file_path)) as _pdf:
            page_count = len(_pdf)
        _push_log('debug', f'[upload] PDF has {page_count} pages')
    except Exception:
        pass

    doc = db.create_document(
        folder_id=folder_id,
        original_filename=pdf_file.filename,
        stored_filename=stored_name,
        file_path=str(file_path),
        file_size=file_size,
        page_count=page_count,
    )
    _push_log('info', f'[upload] ✓ Registered "{pdf_file.filename}" as doc:{doc["id"]} '
                       f'({file_size // 1024} KB) — auto-triggering PageIndex')
    threading.Thread(
        target=process_document,
        args=(doc['id'], str(file_path)),
        daemon=True,
    ).start()
    return jsonify({'id': doc['id'], 'status': 'processing',
                    'original_filename': pdf_file.filename,
                    'folder_id': folder_id}), 202


@app.route('/api/documents/<int:doc_id>/process', methods=['POST'])
def trigger_processing(doc_id):
    doc = db.get_document(doc_id)
    if not doc:
        _push_log('warn', f'[process] Trigger for unknown doc:{doc_id}')
        return jsonify({'error': 'Not found'}), 404
    if doc['status'] == 'processing':
        _push_log('warn', f'[process] doc:{doc_id} already processing — ignoring duplicate trigger')
        return jsonify({'error': 'Already processing'}), 409
    _push_log('info', f'[process] Manual trigger received for doc:{doc_id} '
                       f'"{doc["original_filename"]}" [was: {doc["status"]}]')
    threading.Thread(
        target=process_document,
        args=(doc_id, doc['file_path']),
        daemon=True,
    ).start()
    _push_log('debug', f'[process] Background thread spawned for doc:{doc_id}')
    return jsonify({'id': doc_id, 'status': 'processing'}), 202


@app.route('/api/documents/<int:doc_id>/process', methods=['DELETE'])
def cancel_processing(doc_id):
    doc = db.get_document(doc_id)
    if not doc:
        return jsonify({'error': 'Not found'}), 404
    if doc['status'] != 'processing':
        _push_log('warn', f'[cancel] doc:{doc_id} is not processing (status={doc["status"]}) — ignoring')
        return jsonify({'error': 'Not processing'}), 409
    _push_log('info', f'[cancel] Cancel requested for doc:{doc_id} "{doc["original_filename"]}"')
    with _cancel_lock:
        flag = _cancel_flags.get(doc_id)
    if flag:
        flag.set()
        _push_log('info', f'[cancel] Cancel flag set for doc:{doc_id} — will revert to pending after current LLM call')
    else:
        # Thread hasn't registered yet or already finished — just revert status directly
        db.update_document_status(doc_id, 'pending')
        _push_log('info', f'[cancel] doc:{doc_id} reverted to pending directly (no active flag)')
    return jsonify({'id': doc_id, 'status': 'pending'}), 200


@app.route('/api/documents/<int:doc_id>/file', methods=['GET'])
def serve_document_file(doc_id):
    doc = db.get_document(doc_id)
    if not doc:
        return jsonify({'error': 'Not found'}), 404
    fp = Path(doc['file_path'])
    if not fp.exists():
        _push_log('error', f'[file] doc:{doc_id} — file missing on disk: {fp}')
        return jsonify({'error': 'File not found on disk'}), 404
    _push_log('debug', f'[file] Serving doc:{doc_id} "{doc["original_filename"]}" '
                        f'({fp.stat().st_size // 1024} KB)')
    return send_file(str(fp), mimetype='application/pdf',
                     as_attachment=False,
                     download_name=doc['original_filename'])


def _raw_text_to_blocks(raw: str) -> list:
    """Split raw page text into display blocks with lightweight header detection.

    PyPDF2 uses \\n[ \\t]*\\n (blank-ish lines) as paragraph separators, not
    bare \\n\\n, so we normalise first.
    """
    import re
    # Normalise any line that is only whitespace into a blank separator
    normalised = re.sub(r'\n[ \t]+\n', '\n\n', raw)
    normalised = re.sub(r'\n{3,}', '\n\n', normalised)
    paragraphs = normalised.split('\n\n')
    blocks = []
    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        words = para.split()
        capitalised = sum(1 for w in words if w and w[0].isupper())
        is_header = (
            len(para) <= 120
            and para[-1] not in '.,:;?!'
            and len(words) <= 15
            and capitalised / max(len(words), 1) >= 0.6
        )
        blocks.append({'text': para, 'is_header': is_header})
    return blocks


@app.route('/api/documents/<int:doc_id>/text', methods=['GET'])
def get_document_text(doc_id):
    doc = db.get_document(doc_id)
    if not doc:
        return jsonify({'error': 'Not found'}), 404

    page_param = request.args.get('page', '1')

    # ── All-pages mode: used by the continuous-scroll text panel ────────────
    if page_param == 'all':
        with db.get_db() as conn:
            rows = conn.execute(
                "SELECT page_num, text FROM document_pages WHERE doc_id = ? ORDER BY page_num",
                (doc_id,),
            ).fetchall()
        if rows:
            pages = []
            total_words = 0
            for row in rows:
                blocks = _raw_text_to_blocks(row['text'])
                full = '\n\n'.join(b['text'] for b in blocks)
                wc = len(full.split())
                total_words += wc
                pages.append({
                    'page': row['page_num'],
                    'blocks': blocks,
                    'word_count': wc,
                    'char_count': len(full),
                })
            return jsonify({
                'pages': pages,
                'total_pages': len(pages),
                'total_words': total_words,
                'source': 'db',
            })
        # Fallback to live extraction
        fp = Path(doc['file_path'])
        if not fp.exists():
            return jsonify({'error': 'File not found on disk'}), 404
        try:
            page_list = get_page_tokens(str(fp))
            pages = []
            total_words = 0
            for i, entry in enumerate(page_list):
                raw = entry[0] if isinstance(entry, (list, tuple)) else entry
                blocks = _raw_text_to_blocks(raw)
                full = '\n\n'.join(b['text'] for b in blocks)
                wc = len(full.split())
                total_words += wc
                pages.append({
                    'page': i + 1,
                    'blocks': blocks,
                    'word_count': wc,
                    'char_count': len(full),
                })
            return jsonify({
                'pages': pages,
                'total_pages': len(pages),
                'total_words': total_words,
                'source': 'live',
            })
        except Exception as e:
            _push_log('error', f'[text] doc:{doc_id} all-pages — {e}')
            return jsonify({'error': str(e)}), 500

    # ── Single-page mode (legacy, still used internally) ─────────────────────
    page_num = int(page_param) if page_param.isdigit() else 1

    # ── Primary path: serve from DB (stored during processing) ──────────────
    stored = db.get_page_text(doc_id, page_num)
    if stored is not None:
        blocks = _raw_text_to_blocks(stored['text'])
        full = '\n\n'.join(b['text'] for b in blocks)
        return jsonify({
            'page': page_num,
            'total_pages': stored['total_pages'],
            'blocks': blocks,
            'word_count': len(full.split()),
            'char_count': len(full),
            'source': 'db',
        })

    # ── Fallback: live extraction for docs processed before this change ──────
    fp = Path(doc['file_path'])
    if not fp.exists():
        return jsonify({'error': 'File not found on disk'}), 404

    try:
        page_list = get_page_tokens(str(fp))
        total = len(page_list)
        page_num = max(1, min(page_num, total))
        entry = page_list[page_num - 1]
        raw = entry[0] if isinstance(entry, (list, tuple)) else entry
        blocks = _raw_text_to_blocks(raw)
        full = '\n\n'.join(b['text'] for b in blocks)
        return jsonify({
            'page': page_num,
            'total_pages': total,
            'blocks': blocks,
            'word_count': len(full.split()),
            'char_count': len(full),
            'source': 'live',
        })
    except Exception as e:
        _push_log('error', f'[text] doc:{doc_id} page:{page_num} — {e}')
        return jsonify({'error': str(e)}), 500


# ---------------------------------------------------------------------------
# Annotations
# ---------------------------------------------------------------------------

@app.route('/api/documents/<int:doc_id>/annotations', methods=['GET'])
def list_annotations(doc_id):
    if not db.get_document(doc_id):
        return jsonify({'error': 'Not found'}), 404
    anns = db.get_annotations(doc_id)
    return jsonify({'annotations': anns})


@app.route('/api/documents/<int:doc_id>/annotations', methods=['POST'])
def create_annotation(doc_id):
    if not db.get_document(doc_id):
        return jsonify({'error': 'Not found'}), 404
    data = request.get_json(force=True)
    node_id = (data.get('node_id') or '').strip()
    anchor_page = data.get('anchor_page')
    if not node_id:
        return jsonify({'error': 'node_id required'}), 400
    if not anchor_page:
        return jsonify({'error': 'anchor_page required'}), 400
    ann = db.save_annotation(
        doc_id,
        node_id,
        int(anchor_page),
        data.get('anchor_title', ''),
        data.get('anchor_path', ''),
        (data.get('body') or '').strip(),
    )
    return jsonify(ann), 201


@app.route('/api/annotations/<int:ann_id>', methods=['DELETE'])
def delete_annotation(ann_id):
    db.delete_annotation(ann_id)
    return '', 204


# ---------------------------------------------------------------------------
# Note Builder Agent
# ---------------------------------------------------------------------------

def _run_note_builder_thread(doc_id: int, model: str):
    """Background thread: run Note Builder Agent and persist results."""
    try:
        _push_log('info', f'[note_builder] doc:{doc_id} ▶ Starting Note Builder Agent')
        db.update_notes_status(doc_id, 'generating')

        # Fetch full page text with [PAGE N] markers
        document_text = db.get_all_page_texts(doc_id)
        if not document_text:
            _push_log('warn', f'[note_builder] doc:{doc_id} — no page text found, cannot generate notes')
            db.update_notes_status(doc_id, 'failed')
            return

        result = run_note_builder(model=model, document_text=document_text)

        notes = [
            {
                'node_id':      n.node_id or f'page-{n.page_index}',
                'anchor_page':  n.page_index,
                'anchor_title': n.anchor_title or '',
                'anchor_path':  '',
                'body':         n.body,
                'note_type':    n.note_type,
                'severity':     n.severity,
            }
            for n in result.notes
        ]
        db.save_agent_notes(doc_id, notes)
        db.update_notes_status(doc_id, 'done')
        _push_log('info', f'[note_builder] doc:{doc_id} ✓ Done — {len(notes)} notes generated')

    except Exception as e:
        _push_log('error', f'[note_builder] doc:{doc_id} — Error: {e}')
        db.update_notes_status(doc_id, 'failed')


@app.route('/api/documents/<int:doc_id>/generate_notes', methods=['POST'])
def generate_notes(doc_id):
    doc = db.get_document(doc_id)
    if not doc:
        return jsonify({'error': 'Not found'}), 404
    if doc['status'] != 'done':
        return jsonify({'error': 'Document not yet indexed'}), 409
    if doc.get('notes_status') == 'generating':
        return jsonify({'error': 'Already generating'}), 409

    data = request.get_json(force=True) or {}
    model = data.get('model') or 'gpt-4o-2024-11-20'

    _push_log('info', f'[note_builder] doc:{doc_id} — Note generation requested (model={model})')
    threading.Thread(
        target=_run_note_builder_thread,
        args=(doc_id, model),
        daemon=True,
    ).start()
    return jsonify({'status': 'generating'}), 202


@app.route('/api/documents/<int:doc_id>', methods=['DELETE'])
def delete_document(doc_id):
    doc = db.get_document(doc_id)
    if doc:
        _push_log('info', f'[documents] Deleting doc:{doc_id} "{doc["original_filename"]}"')
        fp = Path(doc['file_path'])
        if fp.exists():
            fp.unlink(missing_ok=True)
            _push_log('debug', f'[documents] Removed PDF from disk: {fp.name}')
        sidecar = fp.with_name(fp.stem + '_structure.json')
        if sidecar.exists():
            sidecar.unlink(missing_ok=True)
            _push_log('debug', f'[documents] Removed sidecar JSON: {sidecar.name}')
    else:
        _push_log('warn', f'[documents] DELETE doc:{doc_id} — not found in DB')

    db.cache_evict(doc_id)
    db.delete_document(doc_id)
    _push_log('info', f'[documents] doc:{doc_id} fully removed (DB + disk + cache)')
    return '', 204


# ---------------------------------------------------------------------------
# Logs
# ---------------------------------------------------------------------------

@app.route('/api/logs', methods=['GET'])
def get_logs():
    limit = request.args.get('limit', 300, type=int)
    since = request.args.get('since', 0, type=int)   # only return entries with id > since
    with _log_lock:
        all_entries = list(_log_buffer)
    if since:
        entries = [e for e in all_entries if e['id'] > since]
    else:
        entries = all_entries[-limit:]
    return jsonify({'logs': entries})


@app.route('/api/logs/stream')
def stream_logs():
    def generate():
        q = Queue(maxsize=300)
        with _log_lock:
            history = list(_log_buffer)
            _log_subscribers.append(q)
        for entry in history:
            yield f"data: {json.dumps(entry)}\n\n"
        try:
            while True:
                try:
                    entry = q.get(timeout=25)
                    yield f"data: {json.dumps(entry)}\n\n"
                except Empty:
                    yield ": ping\n\n"
        finally:
            with _log_lock:
                if q in _log_subscribers:
                    _log_subscribers.remove(q)

    return Response(
        generate(),
        mimetype='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'},
    )


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

@app.route('/api/metrics', methods=['GET'])
def get_metrics():
    """Return accumulated LLM call metrics (tokens, cost, latency) since last reset."""
    from pageindex.shared.llm import get_metrics as _get_metrics
    data = _get_metrics()
    # Add derived averages if there have been any calls
    calls = data.get('total_calls', 0)
    if calls:
        data['avg_latency_ms']   = round(data['total_latency_ms'] / calls, 1)
        data['avg_cost_usd']     = round(data['total_cost_usd']   / calls, 6)
    for model, bucket in data.get('by_model', {}).items():
        if bucket['calls']:
            bucket['avg_latency_ms'] = round(bucket['latency_ms'] / bucket['calls'], 1)
            bucket['avg_cost_usd']   = round(bucket['cost_usd']   / bucket['calls'], 6)
    return jsonify(data)


@app.route('/api/metrics/reset', methods=['POST'])
def reset_metrics():
    """Zero all accumulated LLM metrics counters."""
    from pageindex.shared.llm import reset_metrics as _reset_metrics
    _reset_metrics()
    _push_log('info', '[metrics] Metrics counters reset')
    return '', 204


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

@app.route('/api/search', methods=['GET'])
def search():
    query = (request.args.get('q') or '').strip()
    doc_id = request.args.get('doc_id', type=int)
    if not query:
        return jsonify({'results': [], 'query': ''})
    scope = f'doc:{doc_id}' if doc_id else 'all docs'
    _push_log('info', f'[search] Query: "{query}" [{scope}]')
    results = db.search_nodes(query, doc_id=doc_id)
    _push_log('info', f'[search] "{query}" → {len(results)} result(s)')
    if results:
        preview = ', '.join(f'"{r["title"]}"' for r in results[:3])
        _push_log('debug', f'[search] Top results: {preview}')
    return jsonify({'results': results, 'query': query})


# ---------------------------------------------------------------------------
# Legacy /process endpoint
# ---------------------------------------------------------------------------

@app.route('/process', methods=['POST'])
def process_pdf_legacy():
    import tempfile, shutil
    if 'pdf' not in request.files:
        return jsonify({'error': 'No PDF file provided'}), 400
    pdf_file = request.files['pdf']
    if not pdf_file.filename.endswith('.pdf'):
        return jsonify({'error': 'File is not a PDF'}), 400
    _push_log('warn', f'[legacy] /process called for "{pdf_file.filename}" — use /api instead')
    temp_dir = tempfile.mkdtemp()
    try:
        temp_path = os.path.join(temp_dir, pdf_file.filename)
        pdf_file.save(temp_path)
        opt = ConfigLoader().load({})
        result = page_index_main(temp_path, opt)
        return jsonify(result), 200
    except Exception as e:
        _push_log('error', f'[legacy] /process failed: {e}')
        return jsonify({'error': str(e)}), 500
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Cases — Adversarial Multi-Agent Pipeline
# ---------------------------------------------------------------------------

@app.route('/api/cases', methods=['GET'])
def list_cases():
    return jsonify({'cases': db.list_cases_with_summary()})


@app.route('/api/cases', methods=['POST'])
def create_case():
    data = request.get_json(force=True)
    title = (data.get('title') or '').strip()
    if not title:
        return jsonify({'error': 'title is required'}), 400
    model = data.get('model', 'gpt-4o-2024-11-20')
    case = db.create_case(title, model)
    return jsonify(case), 201


@app.route('/api/cases/<int:case_id>', methods=['GET'])
def get_case(case_id):
    case = db.get_case(case_id)
    if not case:
        return jsonify({'error': 'Not found'}), 404
    docs = db.get_case_documents(case_id)
    result = db.get_case_result(case_id)
    return jsonify({'case': case, 'documents': docs, 'result': result})


@app.route('/api/cases/<int:case_id>', methods=['DELETE'])
def delete_case(case_id):
    db.delete_case(case_id)
    return jsonify({'ok': True})


@app.route('/api/cases/<int:case_id>/restore', methods=['POST'])
def restore_case(case_id):
    db.restore_case(case_id)
    return jsonify({'ok': True})


@app.route('/api/cases/<int:case_id>/purge', methods=['DELETE'])
def purge_case(case_id):
    db.purge_case(case_id)
    return jsonify({'ok': True})


@app.route('/api/cases/archived', methods=['GET'])
def list_archived_cases():
    return jsonify({'cases': db.list_archived_cases()})


@app.route('/api/cases/<int:case_id>/documents', methods=['POST'])
def add_case_document(case_id):
    """Attach an existing document (by doc_id) to a case as Petitioner or Respondent."""
    case = db.get_case(case_id)
    if not case:
        return jsonify({'error': 'Case not found'}), 404
    data = request.get_json(force=True)
    doc_id = data.get('doc_id')
    party_role = data.get('party_role', '').strip()
    document_type = data.get('document_type', 'Petition').strip()
    if party_role not in ('Petitioner', 'Respondent'):
        return jsonify({'error': "party_role must be 'Petitioner' or 'Respondent'"}), 400
    cd = db.add_case_document(case_id, doc_id, party_role, document_type)
    return jsonify(cd), 201


@app.route('/api/cases/<int:case_id>/documents/<int:case_doc_id>', methods=['DELETE'])
def remove_case_document(case_id, case_doc_id):
    """Detach a document from a case (removes case_document row only)."""
    case = db.get_case(case_id)
    if not case:
        return jsonify({'error': 'Case not found'}), 404
    db.detach_case_document(case_id, case_doc_id)
    return jsonify({'ok': True})


@app.route('/api/cases/<int:case_id>/party-names', methods=['PATCH'])
def update_party_names(case_id):
    case = db.get_case(case_id)
    if not case:
        return jsonify({'error': 'Case not found'}), 404
    data = request.get_json(force=True)
    role = data.get('role', '').strip()
    name = (data.get('name') or '').strip()
    if role not in ('Petitioner', 'Respondent') or not name:
        return jsonify({'error': 'role and name are required'}), 400
    db.update_case_party_names(case_id, role, name)
    return jsonify({'ok': True})


def _run_clerk_bg(case_id: int):
    from pageindex.pipeline import run_pipeline_clerk
    try:
        run_pipeline_clerk(case_id)
    except Exception as exc:
        log.error("Background clerk failed | case_id=%s | %s", case_id, exc)


def _run_registrar_bg(case_id: int):
    from pageindex.pipeline import run_pipeline_registrar
    try:
        run_pipeline_registrar(case_id)
    except Exception as exc:
        log.error("Background registrar failed | case_id=%s | %s", case_id, exc)


def _run_procedural_bg(case_id: int):
    from pageindex.pipeline import run_pipeline_procedural
    try:
        run_pipeline_procedural(case_id)
    except Exception as exc:
        log.error("Background procedural failed | case_id=%s | %s", case_id, exc)


def _run_devils_advocate_bg(case_id: int):
    from pageindex.pipeline import run_pipeline_devils_advocate
    try:
        run_pipeline_devils_advocate(case_id)
    except Exception as exc:
        log.error("Background devil's advocate failed | case_id=%s | %s", case_id, exc)


def _run_judge_bg(case_id: int):
    from pageindex.pipeline import run_pipeline_judge
    try:
        run_pipeline_judge(case_id)
    except Exception as exc:
        log.error("Background judge failed | case_id=%s | %s", case_id, exc)


def _run_drafter_bg(case_id: int, forum: str, jurisdiction_style: str):
    from pageindex.pipeline import run_pipeline_drafter
    try:
        run_pipeline_drafter(case_id, forum=forum, jurisdiction_style=jurisdiction_style)
    except Exception as exc:
        log.error("Background drafter failed | case_id=%s | %s", case_id, exc)


@app.route('/api/cases/<int:case_id>/run/clerk', methods=['POST'])
def run_clerk_stage(case_id):
    """Trigger Clerk Agent stage in background."""
    case = db.get_case(case_id)
    if not case:
        return jsonify({'error': 'Case not found'}), 404
    if case['status'].endswith('_running'):
        return jsonify({'error': f"Stage already running: '{case['status']}'"}), 409
    t = threading.Thread(target=_run_clerk_bg, args=(case_id,), daemon=True)
    t.start()
    return jsonify({'status': 'clerk_running', 'case_id': case_id})


@app.route('/api/cases/<int:case_id>/run/registrar', methods=['POST'])
def run_registrar_stage(case_id):
    """Trigger Registrar Agent stage in background (requires verifier_done or clerk_done)."""
    case = db.get_case(case_id)
    if not case:
        return jsonify({'error': 'Case not found'}), 404
    if case['status'] not in ('verifier_done', 'clerk_done'):
        return jsonify({'error': f"Registrar requires 'verifier_done' or 'clerk_done', current status: '{case['status']}'"}), 409
    t = threading.Thread(target=_run_registrar_bg, args=(case_id,), daemon=True)
    t.start()
    return jsonify({'status': 'registrar_running', 'case_id': case_id})


@app.route('/api/cases/<int:case_id>/run/procedural', methods=['POST'])
def run_procedural_stage(case_id):
    """Trigger Procedural Agent stage in background (requires registrar_done)."""
    case = db.get_case(case_id)
    if not case:
        return jsonify({'error': 'Case not found'}), 404
    if case['status'] not in ('registrar_done',):
        return jsonify({'error': f"Procedural Agent requires 'registrar_done', current status: '{case['status']}'"}), 409
    t = threading.Thread(target=_run_procedural_bg, args=(case_id,), daemon=True)
    t.start()
    return jsonify({'status': 'procedural_running', 'case_id': case_id})


@app.route('/api/cases/<int:case_id>/run/devils_advocate', methods=['POST'])
def run_devils_advocate_stage(case_id):
    """Trigger Devil's Advocate Agent stage in background (requires procedural_done)."""
    case = db.get_case(case_id)
    if not case:
        return jsonify({'error': 'Case not found'}), 404
    if case['status'] not in ('procedural_done',):
        return jsonify({'error': f"Devil's Advocate requires 'procedural_done', current status: '{case['status']}'"}), 409
    t = threading.Thread(target=_run_devils_advocate_bg, args=(case_id,), daemon=True)
    t.start()
    return jsonify({'status': 'devils_advocate_running', 'case_id': case_id})


@app.route('/api/cases/<int:case_id>/review', methods=['POST'])
def review_matrix(case_id):
    """Human review gate: approve or reject the AdversarialMatrix."""
    data = request.get_json(force=True)
    action = data.get('action', '').lower()
    if action == 'approve':
        db.approve_matrix(case_id)
        return jsonify({'status': 'review_approved'})
    elif action == 'reject':
        reason = data.get('reason', '') or ''
        db.reject_matrix(case_id, reason=reason)
        return jsonify({'status': 'review_rejected'})
    return jsonify({'error': "action must be 'approve' or 'reject'"}), 400


@app.route('/api/cases/<int:case_id>/run/judge', methods=['POST'])
def run_judge_stage(case_id):
    """Trigger Judge Agent stage in background (requires review_approved)."""
    case = db.get_case(case_id)
    if not case:
        return jsonify({'error': 'Case not found'}), 404
    if case['status'] != 'review_approved':
        return jsonify({'error': f"Judge requires 'review_approved', current status: '{case['status']}'"}), 409
    t = threading.Thread(target=_run_judge_bg, args=(case_id,), daemon=True)
    t.start()
    return jsonify({'status': 'judge_running', 'case_id': case_id})


@app.route('/api/cases/<int:case_id>/run/drafter', methods=['POST'])
def run_drafter_stage(case_id):
    """Trigger Drafting Agent stage in background (requires judge_done).

    Body (optional JSON):
        forum              -- Court name/location string
        jurisdiction_style -- "indian_high_court" | "supreme_court" | "district_court" | "custom"
    """
    case = db.get_case(case_id)
    if not case:
        return jsonify({'error': 'Case not found'}), 404
    if case['status'] != 'judge_done':
        return jsonify({'error': f"Drafter requires 'judge_done', current status: '{case['status']}'"}), 409
    data = request.get_json(silent=True) or {}
    forum = data.get('forum', '')
    jurisdiction_style = data.get('jurisdiction_style', 'indian_high_court')
    t = threading.Thread(
        target=_run_drafter_bg,
        args=(case_id, forum, jurisdiction_style),
        daemon=True,
    )
    t.start()
    return jsonify({'status': 'drafter_running', 'case_id': case_id})


# ---------------------------------------------------------------------------
# Sample case seeder — Ramesh Kumar v. Municipal Corporation of Delhi
# Covers all 7 pipeline stages with realistic content.
# ---------------------------------------------------------------------------

_SAMPLE_CLERK_PETITIONER = {
    "party_role": "Petitioner",
    "document_type": "Petition",
    "extracted_facts": [
        {"statement": "The Petitioner, Ramesh Kumar, is the registered owner of Plot No. 47-B, Sector 12, Rohini, New Delhi, having purchased it under Sale Deed dated 14 March 2018.", "page_index": 2, "verified": True},
        {"statement": "The Municipal Corporation issued a demand notice dated 7 June 2024 assessing the annual rental value of the property at ₹4,80,000, resulting in a property tax demand of ₹57,600.", "page_index": 3, "verified": True},
        {"statement": "The property is a single-storey residential building of 120 sq. yards with no commercial use.", "page_index": 4, "verified": True},
        {"statement": "The Petitioner paid all previous property tax dues without default from 2018 to 2023 at an assessed rental value of ₹1,20,000 per annum.", "page_index": 5, "verified": True},
        {"statement": "No notice of re-assessment or opportunity of hearing was granted to the Petitioner before the revised demand was issued.", "page_index": 6, "verified": True}
    ],
    "issues_raised": [
        "Whether the re-assessment of Annual Rental Value was conducted in compliance with Section 116 of the DMC Act, 1957.",
        "Whether the failure to issue prior notice and afford a hearing violates the principles of natural justice.",
        "Whether the four-fold increase in ARV without any change in property use or structure is arbitrary."
    ],
    "cited_laws_and_cases": [
        {"citation": "Delhi Municipal Corporation Act, 1957, Section 116", "page_index": 7, "verified": True},
        {"citation": "Delhi Municipal Corporation Act, 1957, Section 123", "page_index": 7, "verified": True},
        {"citation": "Maneka Gandhi v. Union of India, AIR 1978 SC 597", "page_index": 8, "verified": True},
        {"citation": "Chhotu Ram v. Municipal Corporation of Delhi, (2001) 95 DLT 384", "page_index": 8, "verified": True}
    ],
    "prayers": [
        "Quash the demand notice dated 7 June 2024.",
        "Direct the Respondent to reassess the property after issuing notice and conducting a hearing.",
        "Stay the demand during pendency of the petition.",
        "Award costs of the petition to the Petitioner."
    ]
}

_SAMPLE_CLERK_RESPONDENT = {
    "party_role": "Respondent",
    "document_type": "Reply",
    "extracted_facts": [
        {"statement": "The Municipal Corporation of Delhi conducted a city-wide re-assessment survey in 2024 pursuant to the Property Tax Revision Policy 2023.", "page_index": 2, "verified": True},
        {"statement": "The revised ARV for Plot No. 47-B was determined based on the circle rate applicable to Sector 12, Rohini as notified by the Government of NCT of Delhi.", "page_index": 3, "verified": True},
        {"statement": "A general public notice of the re-assessment exercise was published in two newspapers on 15 January 2024 and displayed at ward offices.", "page_index": 4, "verified": True},
        {"statement": "The property falls in Category B under the Unit Area Method of assessment, which attracts a higher rate than previously applied.", "page_index": 5, "verified": True},
        {"statement": "The demand notice itself serves as the communication of the revised assessment and the Petitioner may file objections under Section 123 within 30 days.", "page_index": 6, "verified": True}
    ],
    "issues_raised": [
        "Whether a general public notice of re-assessment satisfies the notice requirement under Section 116 of the DMC Act.",
        "Whether the Unit Area Method validly replaces the previous ARV-based assessment.",
        "Whether the Petitioner's right to file objections under Section 123 is an adequate remedy."
    ],
    "cited_laws_and_cases": [
        {"citation": "Delhi Municipal Corporation Act, 1957, Section 116", "page_index": 7, "verified": True},
        {"citation": "Delhi Municipal Corporation Act, 1957, Section 123", "page_index": 7, "verified": True},
        {"citation": "Property Tax Revision Policy, GNCTD, 2023", "page_index": 8, "verified": True},
        {"citation": "Municipal Corporation of Delhi v. Birla Cotton Spinning, (1968) 3 SCR 251", "page_index": 9, "verified": True},
        {"citation": "Article 226, Constitution of India", "page_index": 9, "verified": True}
    ],
    "prayers": [
        "Dismiss the writ petition as not maintainable — an efficacious alternative remedy exists under Section 123.",
        "Uphold the re-assessment as lawful and in accordance with the Policy of 2023.",
        "Award costs against the Petitioner."
    ]
}

# ---------------------------------------------------------------------------
# Stage 2 — Verifier outputs
# ---------------------------------------------------------------------------

_SAMPLE_VERIFIER_PETITIONER = {
    "overall_confidence": 0.91,
    "flags": [
        {
            "flag_type": "citation_not_found",
            "severity": "warning",
            "affected_field": "cited_laws_and_cases[3]",
            "description": "Chhotu Ram v. Municipal Corporation of Delhi, (2001) 95 DLT 384 — the citation string does not appear verbatim in the document text. The case is referenced by name only without the full citation on page 8."
        }
    ],
    "citation_audit": [
        {"citation": "Delhi Municipal Corporation Act, 1957, Section 116", "found_in_page_text": True, "exact_quote": "…as required under Section 116 of the Delhi Municipal Corporation Act, 1957…"},
        {"citation": "Delhi Municipal Corporation Act, 1957, Section 123", "found_in_page_text": True, "exact_quote": "…Section 123 of the DMC Act provides for filing of objections within 30 days…"},
        {"citation": "Maneka Gandhi v. Union of India, AIR 1978 SC 597", "found_in_page_text": True, "exact_quote": "…as held by the Supreme Court in Maneka Gandhi v. Union of India, AIR 1978 SC 597…"},
        {"citation": "Chhotu Ram v. Municipal Corporation of Delhi, (2001) 95 DLT 384", "found_in_page_text": False, "exact_quote": None}
    ],
    "internal_contradictions": []
}

_SAMPLE_VERIFIER_RESPONDENT = {
    "overall_confidence": 0.87,
    "flags": [
        {
            "flag_type": "unsupported_fact",
            "severity": "warning",
            "affected_field": "extracted_facts[2]",
            "description": "The claim that the general notice was 'displayed at ward offices' does not appear in the Reply text. The Reply mentions newspaper publication only; the ward office display is an assertion without documentary support in the submission."
        }
    ],
    "citation_audit": [
        {"citation": "Delhi Municipal Corporation Act, 1957, Section 116", "found_in_page_text": True, "exact_quote": "…Section 116 of the DMC Act does not require individual notice for a survey-based mass re-assessment…"},
        {"citation": "Delhi Municipal Corporation Act, 1957, Section 123", "found_in_page_text": True, "exact_quote": "…the Petitioner has a full statutory remedy under Section 123 to object to the revised assessment…"},
        {"citation": "Property Tax Revision Policy, GNCTD, 2023", "found_in_page_text": True, "exact_quote": "…in accordance with the Property Tax Revision Policy notified by GNCTD in 2023…"},
        {"citation": "Municipal Corporation of Delhi v. Birla Cotton Spinning, (1968) 3 SCR 251", "found_in_page_text": True, "exact_quote": "…as upheld by the Supreme Court in MCD v. Birla Cotton Spinning, (1968) 3 SCR 251…"},
        {"citation": "Article 226, Constitution of India", "found_in_page_text": True, "exact_quote": "…the writ petition under Article 226 is not maintainable when an efficacious alternative remedy is available…"}
    ],
    "internal_contradictions": []
}

# ---------------------------------------------------------------------------
# Stage 3 — Adversarial Matrix
# ---------------------------------------------------------------------------

_SAMPLE_MATRIX = {
    "undisputed_background": [
        "The Petitioner is the registered owner of Plot No. 47-B, Sector 12, Rohini, New Delhi.",
        "The Municipal Corporation of Delhi is the competent authority for property tax assessment in Delhi.",
        "A demand notice dated 7 June 2024 was issued to the Petitioner revising the ARV to ₹4,80,000 per annum.",
        "The previous assessed ARV for the same property was ₹1,20,000 per annum.",
        "The Petitioner did not receive an individualised notice prior to re-assessment.",
        "Section 123 of the DMC Act, 1957 provides an objection mechanism within 30 days of a demand notice."
    ],
    "framed_issues": [
        {
            "issue_id": "I-1",
            "neutral_issue_statement": "Whether the re-assessment of ARV required individual notice to the Petitioner under Section 116 of the DMC Act, 1957, or whether a general public notice satisfied the statutory requirement.",
            "petitioner_stance": {
                "arguments": [
                    "Section 116 mandates individual notice before any revision of ARV.",
                    "General newspaper publication cannot substitute service of notice on the affected owner.",
                    "Natural justice requires a personal opportunity to contest the basis of re-assessment."
                ],
                "supporting_citations": ["DMC Act, 1957, Section 116", "Maneka Gandhi v. Union of India, AIR 1978 SC 597", "Chhotu Ram v. MCD, (2001) 95 DLT 384 (unverified)"]
            },
            "respondent_stance": {
                "arguments": [
                    "A general public notice of the city-wide survey was published in two newspapers.",
                    "Section 116 does not specify that individual notice is mandatory for a survey-based mass re-assessment.",
                    "The statutory right to object under Section 123 ensures procedural fairness after the demand."
                ],
                "supporting_citations": ["DMC Act, 1957, Section 116", "Municipal Corporation of Delhi v. Birla Cotton Spinning, (1968) 3 SCR 251"]
            }
        },
        {
            "issue_id": "I-2",
            "neutral_issue_statement": "Whether the four-fold increase in ARV — from ₹1,20,000 to ₹4,80,000 — is arbitrary or is justified by the Unit Area Method under the Property Tax Revision Policy 2023.",
            "petitioner_stance": {
                "arguments": [
                    "No physical change in the property or its use occurred between 2023 and 2024.",
                    "A 400% increase without change in use or structure is disproportionate and arbitrary.",
                    "The circle-rate basis applied to ARV conflates market value with rental value, which is impermissible."
                ],
                "supporting_citations": ["DMC Act, 1957, Section 116"]
            },
            "respondent_stance": {
                "arguments": [
                    "The Unit Area Method is a recognised and judicially approved methodology.",
                    "Sector 12, Rohini's circle rates have risen substantially reflecting actual market values.",
                    "The revised ARV is uniformly applied across all Category B properties in the area."
                ],
                "supporting_citations": ["Property Tax Revision Policy, GNCTD, 2023"]
            }
        },
        {
            "issue_id": "I-3",
            "neutral_issue_statement": "Whether the writ petition is maintainable given the existence of an alternative statutory remedy under Section 123 of the DMC Act, 1957.",
            "petitioner_stance": {
                "arguments": [
                    "Where fundamental procedural rights are violated, writ jurisdiction is not ousted by an alternative remedy.",
                    "The objection mechanism cannot cure the initial illegality of re-assessment without notice."
                ],
                "supporting_citations": ["Article 226, Constitution of India", "Maneka Gandhi v. Union of India, AIR 1978 SC 597"]
            },
            "respondent_stance": {
                "arguments": [
                    "The Section 123 remedy is efficacious, statutory, and specifically designed for this dispute.",
                    "High Courts ordinarily do not exercise writ jurisdiction where an adequate alternative remedy exists.",
                    "The Petitioner has not even exhausted the available remedy before approaching this Court."
                ],
                "supporting_citations": ["DMC Act, 1957, Section 123", "Article 226, Constitution of India"]
            }
        }
    ],
    "human_review_status": "approved"
}

# ---------------------------------------------------------------------------
# Stage 4 — Sifted Matrix (AdversarialMatrix + ProceduralAnalysis)
# ---------------------------------------------------------------------------

_SAMPLE_SIFTED_MATRIX = {
    "adversarial_matrix": _SAMPLE_MATRIX,
    "procedural_analysis": {
        "jurisdiction_finding": "maintainable",
        "jurisdiction_reasoning": "The High Court of Delhi has jurisdiction under Article 226 of the Constitution to entertain a writ petition challenging a demand notice issued by a statutory authority (MCD) exercising public law powers. Subject-matter jurisdiction is clearly established.",
        "limitation_finding": "within_time",
        "limitation_reasoning": "The demand notice is dated 7 June 2024. The writ petition appears to have been filed within weeks of the notice. No limitation bar is apparent. The cause of action is continuing so long as the demand subsists.",
        "standing_finding": "established",
        "standing_reasoning": "The Petitioner is the registered owner of the assessed property and is the direct recipient of the impugned demand notice. Locus standi is unambiguously established.",
        "issue_flags": [
            {
                "issue_id": "I-3",
                "procedural_bar": "none",
                "recommendation": "proceed",
                "severity": "advisory",
                "reasoning": "Although the Respondent raises an alternative remedy under Section 123, this does not constitute a jurisdictional bar — it is a discretionary consideration for the court. The issue is properly framed and should proceed to substantive adjudication. The advisory is to note that the court may direct exhaustion of the alternative remedy rather than adjudicating the merits."
            }
        ],
        "issues_to_proceed": ["I-1", "I-2", "I-3"],
        "issues_flagged": []
    }
}

# ---------------------------------------------------------------------------
# Stage 5 — Stress-Tested Matrix (SiftedMatrix + StressTestedMatrix)
# ---------------------------------------------------------------------------

_SAMPLE_STRESS_TESTED_MATRIX = {
    "sifted_matrix": _SAMPLE_SIFTED_MATRIX,
    "stress_tested_matrix": {
        "stress_tests": [
            {
                "issue_id": "I-1",
                "petitioner_vulnerability": {
                    "strongest_counter": "Section 116 of the DMC Act was enacted in a pre-digital era when individual notices were the only practicable means of communication. The court may read the statute purposively and hold that a publicised city-wide survey with wide newspaper coverage, followed by a statutory objection window under Section 123, satisfies the spirit of the notice requirement — particularly when the Petitioner had constructive knowledge via public notification.",
                    "weakness_type": "citation_stretch",
                    "severity": "medium",
                    "suggested_reframe": "Reframe to emphasise that 'shall be served' is mandatory language that cannot be read down regardless of administrative convenience — the word 'served' has a specific legal meaning distinct from 'published'."
                },
                "respondent_vulnerability": {
                    "strongest_counter": "The Respondent's reliance on Birla Cotton Spinning (1968) is misplaced — that case concerned rate fixation applicable to a class of properties, not the individual re-assessment of a specific property. The factual matrix is distinguishable, and using a 56-year-old precedent on class-wide rate-fixing to justify individual assessments without notice is a significant stretch.",
                    "weakness_type": "citation_stretch",
                    "severity": "high",
                    "suggested_reframe": None
                },
                "balance_assessment": "petitioner_stronger"
            },
            {
                "issue_id": "I-2",
                "petitioner_vulnerability": {
                    "strongest_counter": "The Petitioner has not filed any valuation report or independent evidence to show that ₹4,80,000 ARV is disproportionate to market realities in Sector 12, Rohini in 2024. Without expert evidence on actual rental values, the arbitrariness argument is bare assertion. Courts are reluctant to substitute their judgment for that of specialised valuation authorities without such material.",
                    "weakness_type": "factual_gap",
                    "severity": "high",
                    "suggested_reframe": "If merits are to be argued, the Petitioner should file an independent valuation report as part of the review proceedings directed by the court."
                },
                "respondent_vulnerability": {
                    "strongest_counter": "The Respondent has not placed on record the actual circle rate notification or the UAM computation sheet for Plot No. 47-B showing how the ₹4,80,000 figure was arrived at. In the absence of the calculation methodology, the court cannot verify whether the UAM was correctly applied — making the 'uniformly applied' claim unverifiable.",
                    "weakness_type": "factual_gap",
                    "severity": "medium",
                    "suggested_reframe": None
                },
                "balance_assessment": "balanced"
            },
            {
                "issue_id": "I-3",
                "petitioner_vulnerability": {
                    "strongest_counter": "The Supreme Court in Whirlpool Corporation v. Registrar of Trade Marks (1998) held that a writ petition is ordinarily not maintainable where an efficacious alternative remedy exists — and this principle is well-entrenched in tax matters. The Petitioner has not exhausted Section 123 at all, and the High Court may non-suit the petition on this ground alone before examining the merits.",
                    "weakness_type": "logical_leap",
                    "severity": "medium",
                    "suggested_reframe": "Strengthen by citing cases where courts exercised writ jurisdiction despite Section 123, specifically in natural justice violation scenarios."
                },
                "respondent_vulnerability": {
                    "strongest_counter": "The Respondent's position that the writ is not maintainable is undermined by the Petitioner's core complaint — that the very assessment process lacked the procedural step (individual notice) that would have generated the material for the Section 123 objection. It is circular to say 'go object under Section 123' when the objection right was triggered by a defective process.",
                    "weakness_type": "logical_leap",
                    "severity": "high",
                    "suggested_reframe": None
                },
                "balance_assessment": "petitioner_stronger"
            }
        ],
        "strongest_issues_for_petitioner": ["I-1", "I-3"],
        "strongest_issues_for_respondent": [],
        "most_contested_issues": ["I-2"],
        "reviewer_note": "The Petitioner has a strong case on Issues I-1 and I-3. The court is likely to find the notice issue decisive and may not reach the merits of I-2 at all. Key item to verify: confirm the exact text of Section 116 DMC Act — particularly whether the word 'served' appears — as this is the linchpin of I-1. The Chhotu Ram citation (I-1 petitioner stance) was flagged as unverified by the Verifier Agent; consider independently confirming the citation before the matter is listed."
    }
}

# ---------------------------------------------------------------------------
# Stage 6 — Draft Court Order (Judge output)
# ---------------------------------------------------------------------------

_SAMPLE_ORDER = {
    "case_title": "Ramesh Kumar v. Municipal Corporation of Delhi",
    "background_facts": "The Petitioner, a residential property owner in Rohini, Delhi, challenges a demand notice issued on 7 June 2024 revising his Annual Rental Value from ₹1,20,000 to ₹4,80,000 — a four-fold increase. The revision followed a city-wide re-assessment exercise by the Municipal Corporation under the Property Tax Revision Policy 2023, applying the Unit Area Method. No individual pre-assessment notice was served on the Petitioner. The demand notice itself stated that objections could be filed under Section 123 of the DMC Act within 30 days.",
    "reasoned_decisions": [
        {
            "issue_id": "I-1",
            "issue_statement": "Whether individual notice was required before revision of ARV under Section 116 of the DMC Act, 1957.",
            "rule": "Section 116 of the Delhi Municipal Corporation Act, 1957 requires that before any revision of the annual value of a property, the owner shall be served with a notice specifying the proposed revised value and afforded an opportunity to file objections. The Supreme Court in Maneka Gandhi v. Union of India, AIR 1978 SC 597, held that any procedure that curtails the right to be heard must be struck down as violating Article 21.",
            "analysis": "The Respondent's reliance on general newspaper publication does not satisfy the individual notice mandate under Section 116. The provision uses the language 'shall be served', indicating a mandatory personal notice. While mass re-assessment exercises may warrant practical adjustments, the statutory language does not permit substitution by general notice. The Petitioner's case is distinguishable from Birla Cotton Spinning (1968), where the Court dealt with rate fixation applicable to a class, not individual re-assessment of a specific property. The absence of individual notice before a 400% increase in assessment deprived the Petitioner of meaningful participation in the process.",
            "conclusion": "Issue I-1 decided in favour of the Petitioner. The re-assessment conducted without individual notice to the Petitioner is procedurally defective."
        },
        {
            "issue_id": "I-2",
            "issue_statement": "Whether the four-fold increase in ARV from ₹1,20,000 to ₹4,80,000 is arbitrary.",
            "rule": "The Unit Area Method of property tax assessment is a permissible methodology approved by courts when applied uniformly and based on rational classification. However, assessments must not be arbitrary or disproportionate. Article 14 of the Constitution prohibits arbitrary state action.",
            "analysis": "The Unit Area Method and the circle-rate-linked ARV revision are not per se impermissible. However, since Issue I-1 has been decided in favour of the Petitioner on grounds of procedural defect, it would be premature to adjudicate on the quantum of the revised ARV without first directing the Respondent to reassess after proper notice and hearing. The Petitioner will have a full opportunity to contest the methodology and quantum in the fresh assessment proceeding.",
            "conclusion": "Issue I-2 — not adjudicated on merits at this stage. The question of ARV quantum is remitted for fresh consideration by the Respondent after affording proper notice and hearing."
        },
        {
            "issue_id": "I-3",
            "issue_statement": "Whether the writ petition is maintainable given the alternative remedy under Section 123 of the DMC Act.",
            "rule": "It is settled law that an alternative statutory remedy does not automatically oust writ jurisdiction under Article 226. Where there is a violation of a fundamental right, breach of natural justice, or the alternative remedy is inadequate, the High Court may entertain a writ petition.",
            "analysis": "The Petitioner's grievance is not merely about the quantum of tax but about the absence of any pre-assessment notice — a fundamental procedural requirement. The Section 123 objection mechanism operates post-demand and cannot cure the initial procedural defect of having assessed the property without notice. Since the complaint goes to the root of the assessment procedure, this Court's writ jurisdiction is properly invoked and the alternative remedy is not an adequate substitute.",
            "conclusion": "Issue I-3 decided in favour of the Petitioner. The writ petition is maintainable."
        }
    ],
    "final_order": "In view of the foregoing, the demand notice dated 7 June 2024 issued by the Municipal Corporation of Delhi is quashed. The Respondent is directed to (i) serve a fresh individual notice on the Petitioner specifying the proposed revised Annual Rental Value within four weeks from the date of this order; (ii) afford the Petitioner an opportunity to file objections and be personally heard within six weeks thereafter; and (iii) pass a reasoned order on the ARV revision within four weeks of such hearing. The enhanced tax demand shall remain stayed pending completion of the fresh assessment process. The petition is allowed in the above terms. Costs of ₹10,000 are imposed on the Respondent, payable to the Petitioner within four weeks."
}

# ---------------------------------------------------------------------------
# Stage 7 — Formal Court Order (Drafting Agent output)
# ---------------------------------------------------------------------------

_SAMPLE_FORMAL_ORDER = {
    "jurisdiction_style": "indian_high_court",
    "cause_title": "IN THE HIGH COURT OF DELHI AT NEW DELHI\n\nW.P.(C) No. ___ of 2024",
    "coram": "HON'BLE MR. JUSTICE A.K. SHARMA",
    "date": "22nd March, 2026",
    "petitioner_counsel": "Mr. Vikram Anand, Advocate",
    "respondent_counsel": "Ms. Priya Nair, Standing Counsel for MCD",
    "body": (
        "Mr. Vikram Anand, Advocate for the Petitioner.\n"
        "Ms. Priya Nair, Standing Counsel for the Municipal Corporation of Delhi.\n\n"
        "This writ petition under Article 226 of the Constitution of India has been filed by the Petitioner, "
        "Shri Ramesh Kumar, challenging the demand notice dated 7th June, 2024 issued by the Municipal Corporation "
        "of Delhi ('MCD'/'Respondent') revising the Annual Rental Value ('ARV') of his property bearing Plot No. 47-B, "
        "Sector 12, Rohini, New Delhi from ₹1,20,000 per annum to ₹4,80,000 per annum — a four-fold increase — "
        "and raising a consequent property tax demand of ₹57,600.\n\n"
        "The Petitioner is the registered owner of the said property by virtue of a Sale Deed dated 14th March, 2018. "
        "The property is a single-storey residential structure admeasuring 120 sq. yards with no commercial activity. "
        "The Petitioner has been a regular taxpayer and paid all dues from 2018 to 2023 without default at the previously "
        "assessed ARV of ₹1,20,000 per annum. The Respondent, pursuant to the Property Tax Revision Policy 2023 of the "
        "Government of NCT of Delhi, conducted a city-wide re-assessment exercise applying the Unit Area Method ('UAM') "
        "and revised the ARV of the Petitioner's property without serving any individual notice upon him.\n\n"
        "Three issues fall for consideration. First, whether the re-assessment of ARV required individual notice to the "
        "Petitioner under Section 116 of the Delhi Municipal Corporation Act, 1957 ('DMC Act'). Second, whether the "
        "four-fold increase in ARV is arbitrary and disproportionate. Third, whether this writ petition is maintainable "
        "in view of the alternative remedy available under Section 123 of the DMC Act.\n\n"
        "On Issue No. I: Section 116 of the DMC Act employs the expression 'shall be served', which is mandatory language "
        "connoting individual personal service. The Respondent's contention that a general public notice published in two "
        "newspapers satisfies this statutory requirement is untenable. General publication cannot substitute service upon "
        "the affected property owner. Reliance placed by the Respondent on Municipal Corporation of Delhi v. Birla Cotton "
        "Spinning, (1968) 3 SCR 251 is misplaced, as that case dealt with city-wide rate fixation affecting a class of "
        "properties — a fundamentally different exercise from the individual re-assessment of a specific property. "
        "The principles of natural justice, as expounded by the Supreme Court in Maneka Gandhi v. Union of India, "
        "AIR 1978 SC 597, reinforce the requirement of an opportunity to be heard before adverse civil consequences "
        "are imposed. The re-assessment conducted without individual notice is accordingly procedurally defective.\n\n"
        "On Issue No. II: The Unit Area Method is not, in principle, impermissible. However, since Issue No. I is decided "
        "in favour of the Petitioner on procedural grounds, it would be premature to adjudicate the quantum of the revised "
        "ARV at this stage. The Petitioner shall have full opportunity to contest the methodology, computation, and "
        "proportionality of the revised ARV in the fresh assessment proceedings directed herein below.\n\n"
        "On Issue No. III: The availability of an alternative remedy under Section 123 of the DMC Act does not oust the "
        "writ jurisdiction of this Court under Article 226 of the Constitution where, as here, the very initiation of the "
        "assessment process suffered from a fundamental procedural defect. The objection mechanism under Section 123 "
        "operates post-assessment and cannot retroactively cure the illegality of having assessed the property without "
        "affording the owner any pre-assessment opportunity. The writ petition is maintainable."
    ),
    "operative_portion": (
        "In view of the above, this Court passes the following order:\n\n"
        "1. The demand notice dated 7th June, 2024 issued by the Municipal Corporation of Delhi revising the Annual "
        "Rental Value of Plot No. 47-B, Sector 12, Rohini, New Delhi to ₹4,80,000 per annum is hereby quashed.\n\n"
        "2. The Respondent is directed to serve a fresh individual notice upon the Petitioner specifying the proposed "
        "revised Annual Rental Value within four weeks from the date of this order.\n\n"
        "3. The Petitioner shall be afforded an opportunity to file objections and be personally heard within six weeks "
        "of receipt of the said notice.\n\n"
        "4. The Respondent shall pass a reasoned order on the ARV revision within four weeks of the hearing.\n\n"
        "5. The property tax demand raised pursuant to the quashed notice shall remain stayed pending completion of the "
        "fresh assessment process.\n\n"
        "6. The writ petition is allowed in the above terms. Costs of ₹10,000 are imposed upon the Respondent, payable "
        "to the Petitioner within four weeks from the date of this order."
    ),
    "signature_block": "Sd/-\n(A.K. SHARMA)\nJUDGE\nHIGH COURT OF DELHI"
}


import json as _json

@app.route('/api/cases/sample', methods=['POST'])
def create_sample_case():
    """Seed a complete demo case (all 7 stages done) for UI exploration."""
    case = db.create_case("Ramesh Kumar v. Municipal Corporation of Delhi", "gpt-4o-2024-11-20")
    case_id = case['id']

    # Stage 1 — Clerk
    pet_cd = db.add_case_document(case_id, None, "Petitioner", "Petition")
    res_cd = db.add_case_document(case_id, None, "Respondent", "Reply")
    db.save_clerk_output(pet_cd['id'], _json.dumps(_SAMPLE_CLERK_PETITIONER))
    db.save_clerk_output(res_cd['id'], _json.dumps(_SAMPLE_CLERK_RESPONDENT))
    db.update_case_status(case_id, "clerk_done")

    # Stage 2 — Verifier
    db.save_verifier_output(pet_cd['id'], _json.dumps(_SAMPLE_VERIFIER_PETITIONER))
    db.save_verifier_output(res_cd['id'], _json.dumps(_SAMPLE_VERIFIER_RESPONDENT))
    db.update_case_status(case_id, "verifier_done")

    # Stage 3 — Registrar (AdversarialMatrix)
    db.save_adversarial_matrix(case_id, _json.dumps(_SAMPLE_MATRIX))
    db.update_case_status(case_id, "registrar_done")

    # Stage 4 — Procedural (SiftedMatrix)
    db.save_sifted_matrix(case_id, _json.dumps(_SAMPLE_SIFTED_MATRIX))
    db.update_case_status(case_id, "procedural_done")

    # Stage 5 — Devil's Advocate (StressTestedMatrix) → triggers review_pending
    db.save_stress_tested_matrix(case_id, _json.dumps(_SAMPLE_STRESS_TESTED_MATRIX))
    db.update_case_status(case_id, "review_pending")

    # Human review gate — approve
    with db.get_db() as conn:
        conn.execute(
            "UPDATE case_results SET human_review_status = 'approved' WHERE case_id = ?",
            (case_id,)
        )
    db.update_case_status(case_id, "review_approved")

    # Stage 6 — Judge (DraftCourtOrder)
    db.save_draft_court_order(case_id, _json.dumps(_SAMPLE_ORDER))
    db.update_case_status(case_id, "judge_done")

    # Stage 7 — Drafter (FormalCourtOrder)
    db.save_formal_court_order(case_id, _json.dumps(_SAMPLE_FORMAL_ORDER))
    db.update_case_status(case_id, "complete")

    return jsonify(db.get_case(case_id)), 201


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    _setup_logging()
    db.init_db()
    _push_log('info', '─' * 48)
    _push_log('info', 'PageIndex backend ready — http://localhost:8000')
    _push_log('info', f'Database: {db.DB_PATH}')
    _push_log('info', f'Uploads:  {db.UPLOADS_ROOT}')
    openai_key = os.environ.get('OPENAI_API_KEY', '')
    if openai_key:
        _push_log('info', f'OpenAI API key loaded — {"*" * 8}{openai_key[-6:]}')
    else:
        _push_log('error', 'OPENAI_API_KEY not set — PageIndex LLM calls will fail!')

    # Report monitoring configuration
    try:
        import yaml
        _cfg_path = Path(__file__).parent / 'pageindex' / 'config.yaml'
        with open(_cfg_path) as _f:
            _mon = yaml.safe_load(_f).get('monitoring', {})
        _log_file = _mon.get('log_file', '').strip()
        if _log_file:
            _push_log('info', f'Log file:  {(Path(__file__).parent / _log_file).resolve()}')
        _wh = _webhook_url()
        if _wh:
            _push_log('info', f'Webhook:   {_wh[:40]}{"..." if len(_wh) > 40 else ""}')
        else:
            _push_log('debug', 'Webhook alerting disabled (set WEBHOOK_URL or monitoring.webhook_url)')
    except Exception:
        pass

    _push_log('info', f'Metrics:   GET /api/metrics  |  POST /api/metrics/reset')
    _push_log('info', '─' * 48)
    app.run(host='0.0.0.0', port=8000, debug=False, threaded=True)
