import os
import sys
import json
import uuid
import time
import logging
import threading
from collections import deque
from queue import Queue, Empty
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, request, jsonify, send_file, Response, g
from flask_cors import CORS

from pageindex import page_index_main
from pageindex.utils import ConfigLoader, get_page_tokens
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
            _push_log(self._MAP.get(record.levelno, 'info'), self.format(record))
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
    handler = _MemHandler()
    handler.setFormatter(logging.Formatter('%(name)s: %(message)s'))

    # Attach handler ONLY to root — child loggers propagate up, so attaching
    # to both root AND child would double-fire every message.
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)

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
_SKIP_LOG_PATHS = {'/api/logs/stream', '/api/logs', '/health'}
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

    doc = db.create_document(
        folder_id=folder_id,
        original_filename=pdf_file.filename,
        stored_filename=stored_name,
        file_path=str(file_path),
        file_size=file_size,
    )
    _push_log('info', f'[upload] ✓ Registered "{pdf_file.filename}" as doc:{doc["id"]} '
                       f'({file_size // 1024} KB) — awaiting manual PageIndex trigger')
    return jsonify({'id': doc['id'], 'status': 'pending',
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

    page_num = request.args.get('page', 1, type=int)

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
        raw = page_list[page_num - 1][0]
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
    return jsonify({'cases': db.list_cases()})


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


def _run_judge_bg(case_id: int):
    from pageindex.pipeline import run_pipeline_judge
    try:
        run_pipeline_judge(case_id)
    except Exception as exc:
        log.error("Background judge failed | case_id=%s | %s", case_id, exc)


@app.route('/api/cases/<int:case_id>/run/clerk', methods=['POST'])
def run_clerk_stage(case_id):
    """Trigger Clerk Agent stage in background."""
    case = db.get_case(case_id)
    if not case:
        return jsonify({'error': 'Case not found'}), 404
    t = threading.Thread(target=_run_clerk_bg, args=(case_id,), daemon=True)
    t.start()
    return jsonify({'status': 'clerk_running', 'case_id': case_id})


@app.route('/api/cases/<int:case_id>/run/registrar', methods=['POST'])
def run_registrar_stage(case_id):
    """Trigger Registrar Agent stage in background."""
    case = db.get_case(case_id)
    if not case:
        return jsonify({'error': 'Case not found'}), 404
    if case['status'] not in ('clerk_done',):
        return jsonify({'error': f"Cannot run registrar from status '{case['status']}'"}), 409
    t = threading.Thread(target=_run_registrar_bg, args=(case_id,), daemon=True)
    t.start()
    return jsonify({'status': 'registrar_running', 'case_id': case_id})


@app.route('/api/cases/<int:case_id>/review', methods=['POST'])
def review_matrix(case_id):
    """Human review gate: approve or reject the AdversarialMatrix."""
    data = request.get_json(force=True)
    action = data.get('action', '').lower()
    if action == 'approve':
        db.approve_matrix(case_id)
        return jsonify({'status': 'review_approved'})
    elif action == 'reject':
        db.reject_matrix(case_id)
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


# ---------------------------------------------------------------------------
# Sample case seeder
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
                "supporting_citations": ["DMC Act, 1957, Section 116", "Maneka Gandhi v. Union of India, AIR 1978 SC 597", "Chhotu Ram v. MCD, (2001) 95 DLT 384"]
            },
            "respondent_stance": {
                "arguments": [
                    "A general public notice of the city-wide survey was published in two newspapers and displayed at ward offices.",
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

_SAMPLE_ORDER = {
    "case_title": "Ramesh Kumar v. Municipal Corporation of Delhi",
    "background_facts": "The Petitioner, a residential property owner in Rohini, Delhi, challenges a demand notice issued on 7 June 2024 revising his Annual Rental Value from ₹1,20,000 to ₹4,80,000 — a four-fold increase. The revision followed a city-wide re-assessment exercise by the Municipal Corporation under the Property Tax Revision Policy 2023, applying the Unit Area Method. No individual pre-assessment notice was served on the Petitioner. The demand notice itself stated that objections could be filed under Section 123 of the DMC Act within 30 days.",
    "reasoned_decisions": [
        {
            "issue_id": "I-1",
            "issue_statement": "Whether individual notice was required before revision of ARV under Section 116 of the DMC Act, 1957.",
            "rule": "Section 116 of the Delhi Municipal Corporation Act, 1957 requires that before any revision of the annual value of a property, the owner shall be served with a notice specifying the proposed revised value and afforded an opportunity to file objections. The Supreme Court in Maneka Gandhi v. Union of India (1978) held that any procedure that curtails the right to be heard must be struck down as violating Article 21.",
            "analysis": "The Respondent's reliance on general newspaper publication does not satisfy the individual notice mandate under Section 116. The provision uses the language 'shall be served', indicating a mandatory personal notice. While mass re-assessment exercises may warrant practical adjustments, the statutory language does not permit substitution by general notice. The Petitioner's case is distinguishable from Birla Cotton Spinning (where the Court dealt with rate fixation, not individual re-assessment) as the present dispute concerns a specific property revision. The absence of individual notice before a 400% increase in assessment deprived the Petitioner of meaningful participation in the process.",
            "conclusion": "Issue I-1 decided in favour of the Petitioner. The re-assessment conducted without individual notice to the Petitioner is procedurally defective."
        },
        {
            "issue_id": "I-2",
            "issue_statement": "Whether the four-fold increase in ARV from ₹1,20,000 to ₹4,80,000 is arbitrary.",
            "rule": "The Unit Area Method of property tax assessment is a permissible methodology approved by courts when applied uniformly and based on rational classification. However, assessments must not be arbitrary or disproportionate. Article 14 of the Constitution prohibits arbitrary state action.",
            "analysis": "The Unit Area Method and the circle-rate-linked ARV revision are not per se impermissible. However, since Issue I-1 has been decided in favour of the Petitioner on grounds of procedural defect, it would be premature to adjudicate on the quantum of the revised ARV without first directing the Respondent to reassess after proper notice and hearing. The Petitioner will have full opportunity to contest the methodology and quantum in the fresh assessment proceeding.",
            "conclusion": "Issue I-2 — not adjudicated on merits at this stage. The question of ARV quantum is remitted for fresh consideration by the Respondent after affording proper notice."
        },
        {
            "issue_id": "I-3",
            "issue_statement": "Whether the writ petition is maintainable given the alternative remedy under Section 123 of the DMC Act.",
            "rule": "It is settled law that an alternative statutory remedy does not automatically oust writ jurisdiction under Article 226. Where there is a violation of a fundamental right, breach of natural justice, or the alternative remedy is inadequate, the High Court may entertain a writ petition.",
            "analysis": "The Petitioner's grievance is not merely about the quantum of tax but about the absence of any pre-assessment notice — a fundamental procedural requirement. The Section 123 objection mechanism operates post-demand and cannot cure the initial procedural defect. Since the complaint goes to the root of jurisdiction and procedure, this Court's writ jurisdiction is properly invoked.",
            "conclusion": "Issue I-3 decided in favour of the Petitioner. The writ petition is maintainable."
        }
    ],
    "final_order": "In view of the foregoing, the demand notice dated 7 June 2024 issued by the Municipal Corporation of Delhi is quashed. The Respondent is directed to (i) serve a fresh individual notice on the Petitioner specifying the proposed revised Annual Rental Value within four weeks; (ii) afford the Petitioner an opportunity to file objections and be heard within six weeks thereafter; and (iii) pass a reasoned order on the ARV revision within four weeks of the hearing. The enhanced tax demand shall remain stayed pending completion of the fresh assessment process. The petition is allowed in the above terms. Costs of ₹10,000 are imposed on the Respondent, payable to the Petitioner within four weeks."
}


import json as _json

@app.route('/api/cases/sample', methods=['POST'])
def create_sample_case():
    """Seed a complete demo case (all stages done) for UI exploration."""
    case = db.create_case("Ramesh Kumar v. Municipal Corporation of Delhi", "gpt-4o-2024-11-20")
    case_id = case['id']

    # Add two case documents (no real PDFs)
    pet_cd = db.add_case_document(case_id, None, "Petitioner", "Petition")
    res_cd = db.add_case_document(case_id, None, "Respondent", "Reply")

    # Save clerk outputs
    db.save_clerk_output(pet_cd['id'], _json.dumps(_SAMPLE_CLERK_PETITIONER))
    db.save_clerk_output(res_cd['id'], _json.dumps(_SAMPLE_CLERK_RESPONDENT))
    db.update_case_status(case_id, "clerk_done")

    # Save adversarial matrix (already approved)
    db.save_adversarial_matrix(case_id, _json.dumps(_SAMPLE_MATRIX))
    db.update_case_status(case_id, "review_approved")

    # Approve matrix directly in case_results
    with db.get_db() as conn:
        conn.execute(
            "UPDATE case_results SET human_review_status = 'approved' WHERE case_id = ?",
            (case_id,)
        )

    # Save draft court order
    db.save_draft_court_order(case_id, _json.dumps(_SAMPLE_ORDER))
    db.update_case_status(case_id, "judge_done")

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
    _push_log('info', '─' * 48)
    app.run(host='0.0.0.0', port=8000, debug=False, threaded=True)
