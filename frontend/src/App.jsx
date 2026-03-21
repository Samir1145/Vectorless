import { useState, useRef, useEffect, useCallback } from 'react';
import { Document, Page, pdfjs } from 'react-pdf';
import 'react-pdf/dist/Page/AnnotationLayer.css';
import 'react-pdf/dist/Page/TextLayer.css';
import './App.css';

const API = 'http://localhost:8000';

// Configure PDF.js worker (bundled with react-pdf)
pdfjs.GlobalWorkerOptions.workerSrc = new URL(
  'pdfjs-dist/build/pdf.worker.min.mjs',
  import.meta.url,
).toString();

// ---------------------------------------------------------------------------
// Tree Node Component
// ---------------------------------------------------------------------------
const TreeNode = ({
  node, level = 0, onNodeClick, activePage,
  annotations, editingAnnotationNodeId, setEditingAnnotationNodeId,
  annotationDraft, setAnnotationDraft, onAnnotationSave,
}) => {
  const [isExpanded, setIsExpanded] = useState(true);
  const hasChildren = node.nodes && node.nodes.length > 0;
  const startPage = node.start_index;
  const nodeId = node.node_id;
  const isActive = activePage !== null &&
    startPage <= activePage && activePage <= (node.end_index ?? startPage);

  const existingAnnotation = annotations?.find(a => a.node_id === nodeId);
  const isEditing = editingAnnotationNodeId === nodeId;

  const handleClick = () => {
    if (hasChildren) setIsExpanded(e => !e);
    if (startPage) onNodeClick?.(startPage);
  };

  const handleAnnotationEdit = (e) => {
    e.stopPropagation();
    setAnnotationDraft(existingAnnotation?.body || '');
    setEditingAnnotationNodeId(nodeId);
  };

  const childProps = {
    annotations, editingAnnotationNodeId, setEditingAnnotationNodeId,
    annotationDraft, setAnnotationDraft, onAnnotationSave,
  };

  return (
    <div className="tree-node">
      <div
        className={`node-content ${hasChildren ? 'has-children' : ''} ${isActive ? 'node-active' : ''}`}
        style={{ paddingLeft: `${level * 20 + 12}px` }}
      >
        <div className="node-header" onClick={handleClick}>
          {hasChildren && (
            <span className={`expand-icon ${isExpanded ? 'expanded' : ''}`}>▼</span>
          )}
          {!hasChildren && <span className="leaf-icon">📄</span>}
          <div className="node-title">{node.title}</div>
          <div className="node-meta">
            {node.node_id && <span className="node-id">ID: {node.node_id}</span>}
            <span className="node-pages">p.{node.start_index}–{node.end_index}</span>
          </div>
          {onAnnotationSave && (
            <button
              className={`annotation-edit-btn${existingAnnotation ? ' has-annotation' : ''}`}
              onClick={handleAnnotationEdit}
              title={existingAnnotation ? 'Edit note' : 'Add note'}
            >
              <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/>
                <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/>
              </svg>
            </button>
          )}
        </div>

        {/* Inline annotation editor */}
        {isEditing && (
          <div
            className="annotation-editor"
            onClick={e => e.stopPropagation()}
          >
            <textarea
              className="annotation-textarea"
              value={annotationDraft}
              onChange={e => setAnnotationDraft(e.target.value)}
              placeholder="Add a note for this section…"
              autoFocus
              rows={3}
            />
            <div className="annotation-editor-actions">
              <button
                className="annotation-save-btn"
                onClick={(e) => { e.stopPropagation(); onAnnotationSave(nodeId, startPage, node.title, annotationDraft); }}
              >Save</button>
              <button
                className="annotation-cancel-btn"
                onClick={(e) => { e.stopPropagation(); setEditingAnnotationNodeId(null); }}
              >Cancel</button>
              {existingAnnotation && (
                <button
                  className="annotation-delete-btn"
                  onClick={(e) => { e.stopPropagation(); onAnnotationSave(nodeId, startPage, node.title, ''); }}
                >Delete note</button>
              )}
            </div>
          </div>
        )}

        {/* Existing annotation display */}
        {!isEditing && existingAnnotation && (
          <div
            className={`annotation-body${existingAnnotation.is_orphan ? ' orphan' : ''}`}
          >
            {!!existingAnnotation.is_orphan && (
              <span className="annotation-orphan-badge">re-anchored</span>
            )}
            {existingAnnotation.body}
          </div>
        )}

        {hasChildren && isExpanded && (
          <div className="node-children">
            {node.nodes.map((child, i) => (
              <TreeNode key={i} node={child} level={level + 1}
                onNodeClick={onNodeClick} activePage={activePage}
                {...childProps}
              />
            ))}
          </div>
        )}
      </div>
      {node.summary && (
        <div className="node-summary" style={{
          paddingLeft: `${level * 20 + 24}px`,
          maxWidth: `calc(100% - ${level * 20 + 24}px)`
        }}>
          <span className="summary-label">Summary:</span>
          <p>{node.summary}</p>
        </div>
      )}
    </div>
  );
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
function countNodes(structure) {
  if (!structure) return 0;
  let count = structure.length;
  for (const node of structure) {
    if (node.nodes) count += countNodes(node.nodes);
  }
  return count;
}

function formatUploadedAt(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  const now = new Date();
  const diffMs = now - d;
  const diffMins = Math.floor(diffMs / 60000);
  if (diffMins < 1) return 'Just now';
  if (diffMins < 60) return `${diffMins}m ago`;
  const diffHrs = Math.floor(diffMins / 60);
  if (diffHrs < 24) return `${diffHrs}h ago`;
  return d.toLocaleDateString();
}

function formatDocDate(iso) {
  if (!iso) return '';
  return new Date(iso).toLocaleString('en-GB', {
    day: '2-digit', month: '2-digit', year: 'numeric',
    hour: '2-digit', minute: '2-digit', second: '2-digit',
  });
}

// ---------------------------------------------------------------------------
// Status Badge
// ---------------------------------------------------------------------------
const StatusBadge = ({ status }) => {
  if (status === 'pending') return <span className="status-badge pending">Pending</span>;
  if (status === 'processing') return (
    <span className="status-badge processing">
      <span className="spinner-sm" /> Processing
    </span>
  );
  if (status === 'failed') return <span className="status-badge failed">Failed</span>;
  return null;
};

// ---------------------------------------------------------------------------
// Main App
// ---------------------------------------------------------------------------
export default function App() {
  // API state
  const [folders, setFolders] = useState([]);
  const [documents, setDocuments] = useState([]);
  const [activeFolderId, setActiveFolderId] = useState(null); // null = All Documents

  // View state
  const [activeDocId, setActiveDocId] = useState(null);
  const [treeData, setTreeData] = useState(null);
  const [treeLoading, setTreeLoading] = useState(false);
  const [error, setError] = useState(null);

  // PDF viewer
  const [pdfPage, setPdfPage] = useState(1);
  const [numPages, setNumPages] = useState(null);
  const [pdfWidth, setPdfWidth] = useState(null);
  const pdfContainerRef = useRef(null);

  // Right panel tabs
  const [activeRightTab, setActiveRightTab] = useState('tree');
  const [pageText, setPageText] = useState(null);
  const [pageTextLoading, setPageTextLoading] = useState(false);

  // Annotations
  const [annotations, setAnnotations] = useState([]);
  const [editingAnnotationNodeId, setEditingAnnotationNodeId] = useState(null);
  const [annotationDraft, setAnnotationDraft] = useState('');

  // Log panel
  const [logs, setLogs] = useState([]);
  const [logsExpanded, setLogsExpanded] = useState(true);
  const logEndRef = useRef(null);
  const eventSourceRef = useRef(null);
  const lastLogIdRef = useRef(0);

  // Sidebar UI
  const [searchQuery, setSearchQuery] = useState('');
  const [searchResults, setSearchResults] = useState([]);
  const [showNewFolderInput, setShowNewFolderInput] = useState(false);
  const [newFolderName, setNewFolderName] = useState('');

  const [openMenuId, setOpenMenuId] = useState(null); // doc_id whose menu is open

  // Cases
  const [cases, setCases] = useState([]);
  const [activeCaseId, setActiveCaseId] = useState(null);
  const [activeCaseData, setActiveCaseData] = useState(null); // { case, documents, result }
  const [casesPanelExpanded, setCasesPanelExpanded] = useState(true);
  const [showNewCaseInput, setShowNewCaseInput] = useState(false);
  const [newCaseTitle, setNewCaseTitle] = useState('');
  const [activeCaseTab, setActiveCaseTab] = useState('setup');
  const [casePdfParty, setCasePdfParty] = useState('petitioner');
  const casePollRef = useRef(null);

  const pdfInputRef = useRef(null);
  const pollIntervalsRef = useRef({});   // doc_id -> intervalId
  const searchDebounceRef = useRef(null);
  const menuRef = useRef(null);

  // Theme
  const [theme, setTheme] = useState(() => localStorage.getItem('pi-theme') || 'light');
  const toggleTheme = useCallback(() => {
    setTheme(t => {
      const next = t === 'dark' ? 'light' : 'dark';
      localStorage.setItem('pi-theme', next);
      return next;
    });
  }, []);

  // ---------------------------------------------------------------------------
  // Data fetching
  // ---------------------------------------------------------------------------
  const fetchFolders = useCallback(async () => {
    try {
      const res = await fetch(`${API}/api/folders`);
      const data = await res.json();
      setFolders(data.folders || []);
    } catch { /* server not ready yet */ }
  }, []);

  const fetchDocuments = useCallback(async (folderId = null) => {
    try {
      const url = folderId !== null
        ? `${API}/api/documents?folder_id=${folderId}`
        : `${API}/api/documents`;
      const res = await fetch(url);
      const data = await res.json();
      setDocuments(data.documents || []);
    } catch { /* server not ready yet */ }
  }, []);

  useEffect(() => {
    fetchFolders();
  }, [fetchFolders]);

  useEffect(() => {
    fetchDocuments(activeFolderId);
  }, [activeFolderId, fetchDocuments]);

  // ---------------------------------------------------------------------------
  // Polling for pending/processing docs
  // ---------------------------------------------------------------------------
  const startPolling = useCallback((docId) => {
    if (pollIntervalsRef.current[docId]) return;
    pollIntervalsRef.current[docId] = setInterval(async () => {
      try {
        const res = await fetch(`${API}/api/documents/${docId}/status`);
        const data = await res.json();
        setDocuments(prev =>
          prev.map(d => d.id === docId ? { ...d, ...data } : d)
        );
        if (data.status === 'done' || data.status === 'failed') {
          clearInterval(pollIntervalsRef.current[docId]);
          delete pollIntervalsRef.current[docId];
        }
      } catch { /* ignore */ }
    }, 2000);
  }, []);

  // Resume polling for docs that were mid-processing when page was refreshed
  useEffect(() => {
    documents.forEach(doc => {
      if (doc.status === 'processing') {
        startPolling(doc.id);
      }
    });
  }, [documents, startPolling]);

  // Clean up intervals on unmount
  useEffect(() => {
    return () => {
      Object.values(pollIntervalsRef.current).forEach(clearInterval);
    };
  }, []);

  // Measure PDF container width and keep it updated on resize
  useEffect(() => {
    const el = pdfContainerRef.current;
    if (!el) return;
    const update = () => setPdfWidth(el.clientWidth - 2);
    update();
    const ro = new ResizeObserver(update);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // Fetch page text whenever doc or page changes — runs regardless of active tab
  // so switching to Text shows content instantly
  useEffect(() => {
    if (!activeDocId) {
      setPageText(null);
      return;
    }
    let cancelled = false;
    setPageTextLoading(true);
    fetch(`${API}/api/documents/${activeDocId}/text?page=${pdfPage}`)
      .then(r => r.json())
      .then(data => { if (!cancelled) setPageText(data); })
      .catch(() => {})
      .finally(() => { if (!cancelled) setPageTextLoading(false); });
    return () => { cancelled = true; };
  }, [activeDocId, pdfPage]);

  // Fetch annotations whenever active doc changes
  useEffect(() => {
    if (!activeDocId) { setAnnotations([]); return; }
    fetch(`${API}/api/documents/${activeDocId}/annotations`)
      .then(r => r.json())
      .then(data => setAnnotations(data.annotations || []))
      .catch(() => {});
  }, [activeDocId]);

  const handleAnnotationSave = useCallback(async (nodeId, page, title, body) => {
    if (!activeDocId || !nodeId) return;
    try {
      if (!body.trim()) {
        const existing = annotations.find(a => a.node_id === nodeId);
        if (existing) {
          await fetch(`${API}/api/annotations/${existing.id}`, { method: 'DELETE' });
          setAnnotations(prev => prev.filter(a => a.node_id !== nodeId));
        }
      } else {
        const res = await fetch(`${API}/api/documents/${activeDocId}/annotations`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ node_id: nodeId, anchor_page: page, anchor_title: title, body }),
        });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const ann = await res.json();
        setAnnotations(prev => {
          const idx = prev.findIndex(a => a.node_id === nodeId);
          if (idx >= 0) { const next = [...prev]; next[idx] = ann; return next; }
          return [...prev, ann];
        });
      }
    } catch (e) {
      console.error('Annotation save failed:', e);
    } finally {
      setEditingAnnotationNodeId(null);
    }
  }, [activeDocId, annotations]);

  // Poll /api/logs every 1.5s, appending only new entries (by id)
  useEffect(() => {
    const fetchLogs = async () => {
      try {
        const since = lastLogIdRef.current;
        const res = await fetch(`${API}/api/logs?since=${since}&limit=300`);
        if (!res.ok) return;
        const data = await res.json();
        const all = data.logs || [];
        const newEntries = all.filter(e => e.id > lastLogIdRef.current);
        if (newEntries.length > 0) {
          lastLogIdRef.current = newEntries[newEntries.length - 1].id;
          setLogs(prev => [...prev, ...newEntries].slice(-300));
        }
      } catch { /* network error — will retry */ }
    };
    fetchLogs(); // immediate first fetch
    const interval = setInterval(fetchLogs, 1500);
    return () => clearInterval(interval);
  }, []);

  // Auto-scroll log to bottom on new entries
  useEffect(() => {
    if (logsExpanded) logEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [logs, logsExpanded]);

  // ---------------------------------------------------------------------------
  // Upload
  // ---------------------------------------------------------------------------
  const handleFileSelect = async (event) => {
    const file = event.target.files?.[0];
    if (!file) return;
    event.target.value = '';

    if (!file.name.toLowerCase().endsWith('.pdf')) {
      setError('Please select a PDF file');
      return;
    }

    setError(null);

    try {
      const health = await fetch(`${API}/health`, { method: 'HEAD' });
      if (!health.ok) throw new Error('Backend not reachable');
    } catch {
      setError('Backend server not running. Start it with: python3 server.py');
      return;
    }

    const formData = new FormData();
    formData.append('pdf', file);
    if (activeFolderId !== null) formData.append('folder_id', activeFolderId);

    try {
      const res = await fetch(`${API}/api/documents/upload`, {
        method: 'POST',
        body: formData,
      });
      if (!res.ok) throw new Error('Upload failed');
      const newDoc = await res.json();
      // Refresh list and start polling
      await fetchDocuments(activeFolderId);
      startPolling(newDoc.id);
    } catch (e) {
      setError('Upload failed: ' + e.message);
    }
  };

  // ---------------------------------------------------------------------------
  // Select document — always show PDF; load tree only if done
  // ---------------------------------------------------------------------------
  const handleDocSelect = async (doc) => {
    if (doc.id === activeDocId) return;
    setPdfPage(1);
    setNumPages(null);
    setActiveDocId(doc.id);
    setTreeData(null);
    setError(null);
    setActiveCaseId(null);
    setActiveCaseData(null);

    if (doc.status === 'done') {
      setTreeLoading(true);
      try {
        const res = await fetch(`${API}/api/documents/${doc.id}`);
        const data = await res.json();
        setTreeData(data.tree);
      } catch (e) {
        setError('Failed to load tree: ' + e.message);
      } finally {
        setTreeLoading(false);
      }
    }
  };

  // ---------------------------------------------------------------------------
  // Manually trigger PageIndex processing
  // ---------------------------------------------------------------------------
  const handleRunPageIndex = async (e, doc) => {
    e.stopPropagation();
    if (doc.status === 'processing') return;
    setDocuments(prev => prev.map(d => d.id === doc.id ? { ...d, status: 'processing' } : d));
    try {
      await fetch(`${API}/api/documents/${doc.id}/process`, { method: 'POST' });
      startPolling(doc.id);
    } catch (err) {
      setError('Failed to start processing: ' + err.message);
    }
  };

  const handleStopPageIndex = async (e, doc) => {
    e.stopPropagation();
    setDocuments(prev => prev.map(d => d.id === doc.id ? { ...d, status: 'pending' } : d));
    clearInterval(pollIntervalsRef.current[doc.id]);
    try {
      await fetch(`${API}/api/documents/${doc.id}/process`, { method: 'DELETE' });
    } catch { /* best-effort */ }
  };

  // ---------------------------------------------------------------------------
  // Delete document
  // ---------------------------------------------------------------------------
  const handleDeleteDoc = async (e, docId) => {
    e.stopPropagation();
    clearInterval(pollIntervalsRef.current[docId]);
    delete pollIntervalsRef.current[docId];
    await fetch(`${API}/api/documents/${docId}`, { method: 'DELETE' });
    if (activeDocId === docId) { setTreeData(null); setActiveDocId(null); }
    setDocuments(prev => prev.filter(d => d.id !== docId));
  };

  // ---------------------------------------------------------------------------
  // Folders
  // ---------------------------------------------------------------------------
  const handleCreateFolder = async () => {
    const name = newFolderName.trim();
    if (!name) return;
    try {
      const res = await fetch(`${API}/api/folders`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name }),
      });
      const folder = await res.json();
      setFolders(prev => [...prev, folder]);
      setNewFolderName('');
      setShowNewFolderInput(false);
    } catch (e) {
      setError('Failed to create folder: ' + e.message);
    }
  };

  const handleDeleteFolder = async (e, folderId) => {
    e.stopPropagation();
    await fetch(`${API}/api/folders/${folderId}`, { method: 'DELETE' });
    setFolders(prev => prev.filter(f => f.id !== folderId));
    if (activeFolderId === folderId) setActiveFolderId(null);
  };

  // ---------------------------------------------------------------------------
  // Search
  // ---------------------------------------------------------------------------
  const handleSearchChange = (e) => {
    const q = e.target.value;
    setSearchQuery(q);
    clearTimeout(searchDebounceRef.current);
    if (!q.trim()) { setSearchResults([]); return; }
    searchDebounceRef.current = setTimeout(async () => {
      try {
        const params = new URLSearchParams({ q });
        if (activeDocId) params.set('doc_id', activeDocId);
        const res = await fetch(`${API}/api/search?${params}`);
        const data = await res.json();
        setSearchResults(data.results || []);
      } catch { /* ignore */ }
    }, 300);
  };

  // Close menu on outside click
  useEffect(() => {
    const handler = (e) => {
      if (menuRef.current && !menuRef.current.contains(e.target)) {
        setOpenMenuId(null);
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, []);

  // ---------------------------------------------------------------------------
  // Doc context menu actions
  // ---------------------------------------------------------------------------
  const handleCopyId = (e, doc) => {
    e.stopPropagation();
    navigator.clipboard.writeText(String(doc.id));
    setOpenMenuId(null);
  };

  const handleDownloadPdf = (e, doc) => {
    e.stopPropagation();
    const a = document.createElement('a');
    a.href = `${API}/api/documents/${doc.id}/file`;
    a.download = doc.original_filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    setOpenMenuId(null);
  };

  const handleShare = (e, doc) => {
    e.stopPropagation();
    const url = `${API}/api/documents/${doc.id}/file`;
    if (navigator.share) {
      navigator.share({ title: doc.original_filename, url });
    } else {
      navigator.clipboard.writeText(url);
    }
    setOpenMenuId(null);
  };

  // ---------------------------------------------------------------------------
  // Export notes as Markdown
  // ---------------------------------------------------------------------------
  const handleExportNotes = () => {
    if (!annotations.length || !treeData) return;
    const activeDoc = documents.find(d => d.id === activeDocId);
    const docName = activeDoc?.original_filename || 'document';

    // Build node_id → node map for page-range lookup
    const nodeMap = {};
    const walkNodes = (nodes) => {
      for (const n of nodes) {
        if (n.node_id) nodeMap[n.node_id] = n;
        if (n.nodes) walkNodes(n.nodes);
      }
    };
    if (treeData.structure) walkNodes(treeData.structure);

    const lines = [`# Notes — ${docName}`, ''];
    const sorted = [...annotations].sort((a, b) => a.anchor_page - b.anchor_page);
    for (const ann of sorted) {
      const node = nodeMap[ann.node_id];
      const pageRange = node
        ? `p.${node.start_index}–${node.end_index}`
        : `p.${ann.anchor_page}`;
      lines.push(`## ${ann.anchor_title || 'Untitled'} (${pageRange})`);
      lines.push('');
      lines.push(ann.body);
      lines.push('');
    }

    const blob = new Blob([lines.join('\n')], { type: 'text/markdown' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${docName.replace(/\.pdf$/i, '')}_notes.md`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  };

  // ---------------------------------------------------------------------------
  // Download tree JSON
  // ---------------------------------------------------------------------------
  const handleDownload = () => {
    if (!treeData) return;
    const blob = new Blob([JSON.stringify(treeData, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    const activeDoc = documents.find(d => d.id === activeDocId);
    a.download = `${activeDoc?.original_filename?.replace('.pdf', '') || 'document'}_structure.json`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  };

  // ---------------------------------------------------------------------------
  // Derived
  // ---------------------------------------------------------------------------
  const activeDoc = documents.find(d => d.id === activeDocId);

  // ---------------------------------------------------------------------------
  // Case helpers
  // ---------------------------------------------------------------------------
  const inferCaseTab = (status) => {
    if (['clerk_running', 'clerk_done'].includes(status)) return 'clerk';
    if (['registrar_running', 'registrar_done', 'review_pending', 'review_approved', 'review_rejected'].includes(status)) return 'registrar';
    if (['judge_running', 'judge_done'].includes(status)) return 'judge';
    return 'setup';
  };

  const fetchCases = useCallback(async () => {
    try {
      const res = await fetch(`${API}/api/cases`);
      const data = await res.json();
      setCases(data.cases || []);
    } catch { /* ignore */ }
  }, []);

  const fetchCaseData = useCallback(async (caseId, autoTab = false) => {
    try {
      const res = await fetch(`${API}/api/cases/${caseId}`);
      const data = await res.json();
      setActiveCaseData(data);
      setCases(prev => prev.map(c => c.id === caseId ? data.case : c));
      if (autoTab) setActiveCaseTab(inferCaseTab(data.case.status));
      return data;
    } catch { return null; }
  }, []);

  const startCasePoll = useCallback((caseId) => {
    if (casePollRef.current) clearInterval(casePollRef.current);
    casePollRef.current = setInterval(async () => {
      const data = await fetchCaseData(caseId, true);
      const status = data?.case?.status;
      if (status && !['clerk_running', 'registrar_running', 'judge_running'].includes(status)) {
        clearInterval(casePollRef.current);
        casePollRef.current = null;
      }
    }, 2500);
  }, [fetchCaseData]);

  useEffect(() => { fetchCases(); }, [fetchCases]);

  useEffect(() => {
    return () => { if (casePollRef.current) clearInterval(casePollRef.current); };
  }, []);

  const handleCaseSelect = async (cas) => {
    if (cas.id === activeCaseId) return;
    setActiveCaseId(cas.id);
    setActiveDocId(null);
    setTreeData(null);
    setCasePdfParty('petitioner');
    const data = await fetchCaseData(cas.id, true);
    const status = data?.case?.status;
    if (status && ['clerk_running', 'registrar_running', 'judge_running'].includes(status)) {
      startCasePoll(cas.id);
    }
  };

  const handleCreateCase = async () => {
    const title = newCaseTitle.trim();
    if (!title) return;
    try {
      const res = await fetch(`${API}/api/cases`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title }),
      });
      const cas = await res.json();
      setCases(prev => [cas, ...prev]);
      setNewCaseTitle('');
      setShowNewCaseInput(false);
      handleCaseSelect(cas);
    } catch (e) { setError('Failed to create case: ' + e.message); }
  };

  const handleLoadSample = async () => {
    try {
      const res = await fetch(`${API}/api/cases/sample`, { method: 'POST' });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const cas = await res.json();
      setCases(prev => [cas, ...prev]);
      handleCaseSelect(cas);
    } catch (e) { setError('Failed to load sample: ' + e.message); }
  };

  const handleDeleteCase = async (e, caseId) => {
    e.stopPropagation();
    await fetch(`${API}/api/cases/${caseId}`, { method: 'DELETE' });
    setCases(prev => prev.filter(c => c.id !== caseId));
    if (activeCaseId === caseId) { setActiveCaseId(null); setActiveCaseData(null); }
  };

  const handleAddCaseDocument = async (partyRole, docId, documentType) => {
    if (!activeCaseId || !docId) return;
    try {
      await fetch(`${API}/api/cases/${activeCaseId}/documents`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ doc_id: parseInt(docId), party_role: partyRole, document_type: documentType }),
      });
      await fetchCaseData(activeCaseId);
    } catch (e) { setError('Failed to attach document: ' + e.message); }
  };

  const handleRunClerk = async () => {
    if (!activeCaseId) return;
    try {
      await fetch(`${API}/api/cases/${activeCaseId}/run/clerk`, { method: 'POST' });
      await fetchCaseData(activeCaseId, true);
      startCasePoll(activeCaseId);
    } catch (e) { setError('Failed to run Clerk: ' + e.message); }
  };

  const handleRunRegistrar = async () => {
    if (!activeCaseId) return;
    try {
      await fetch(`${API}/api/cases/${activeCaseId}/run/registrar`, { method: 'POST' });
      await fetchCaseData(activeCaseId, true);
      startCasePoll(activeCaseId);
    } catch (e) { setError('Failed to run Registrar: ' + e.message); }
  };

  const handleReviewMatrix = async (action) => {
    if (!activeCaseId) return;
    try {
      await fetch(`${API}/api/cases/${activeCaseId}/review`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action }),
      });
      await fetchCaseData(activeCaseId, true);
    } catch (e) { setError('Review action failed: ' + e.message); }
  };

  const handleRunJudge = async () => {
    if (!activeCaseId) return;
    try {
      await fetch(`${API}/api/cases/${activeCaseId}/run/judge`, { method: 'POST' });
      await fetchCaseData(activeCaseId, true);
      startCasePoll(activeCaseId);
    } catch (e) { setError('Failed to run Judge: ' + e.message); }
  };

  const handleExportOrder = () => {
    if (!activeCaseData?.result?.draft_court_order) return;
    try {
      const order = JSON.parse(activeCaseData.result.draft_court_order);
      const lines = [`# ${order.case_title}`, '', '## Background Facts', '', order.background_facts, ''];
      for (const rd of order.reasoned_decisions || []) {
        lines.push(`## ${rd.issue_id}: ${rd.issue_statement}`, '');
        lines.push(`**Rule:** ${rd.rule}`, '');
        lines.push(`**Analysis:** ${rd.analysis}`, '');
        lines.push(`**Conclusion:** ${rd.conclusion}`, '');
      }
      lines.push('## Final Order', '', order.final_order);
      const blob = new Blob([lines.join('\n')], { type: 'text/markdown' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `${order.case_title.replace(/\s+/g, '_')}_order.md`;
      document.body.appendChild(a); a.click();
      document.body.removeChild(a); URL.revokeObjectURL(url);
    } catch (e) { setError('Export failed: ' + e.message); }
  };

  // PDF doc to display: case-mode shows party doc, otherwise active doc
  const casePdfDocId = activeCaseId
    ? activeCaseData?.documents?.find(d => d.party_role.toLowerCase() === casePdfParty)?.doc_id ?? null
    : null;
  const viewDocId = activeCaseId ? casePdfDocId : activeDocId;
  const caseStatusLabel = {
    pending: 'Setup', clerk_running: 'Clerk ⟳', clerk_done: 'Clerk ✓',
    registrar_running: 'Registrar ⟳', registrar_done: 'Registrar ✓',
    review_pending: 'Review ⏳', review_approved: 'Review ✓', review_rejected: 'Review ✗',
    judge_running: 'Judge ⟳', judge_done: 'Complete ✓', error: 'Error ✗',
  };

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------
  return (
    <div className="app" data-theme={theme}>

      {/* ================================================================
          SIDEBAR
          ================================================================ */}
      <div className="sidebar">

        {/* Hidden file input */}
        <input
          type="file"
          ref={pdfInputRef}
          onChange={handleFileSelect}
          accept=".pdf"
          style={{ display: 'none' }}
        />

        {/* App Header */}
        <div className="sidebar-app-header">
          <div className="sidebar-app-header-left">
            <button className="sidebar-icon-btn" title="PageIndex">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <rect x="3" y="3" width="7" height="7" rx="1"/>
                <rect x="14" y="3" width="7" height="7" rx="1"/>
                <rect x="3" y="14" width="7" height="7" rx="1"/>
                <rect x="14" y="14" width="7" height="7" rx="1"/>
              </svg>
            </button>
            <div className="sidebar-app-divider" />
            <span className="sidebar-app-name">PageIndex</span>
          </div>
          <button className="theme-toggle-btn" onClick={toggleTheme} title={theme === 'dark' ? 'Light mode' : 'Dark mode'}>
            {theme === 'dark' ? (
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <circle cx="12" cy="12" r="5"/>
                <line x1="12" y1="1" x2="12" y2="3"/>
                <line x1="12" y1="21" x2="12" y2="23"/>
                <line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/>
                <line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/>
                <line x1="1" y1="12" x2="3" y2="12"/>
                <line x1="21" y1="12" x2="23" y2="12"/>
                <line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/>
                <line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/>
              </svg>
            ) : (
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/>
              </svg>
            )}
          </button>
        </div>

        {/* Documents Section Header */}
        <div className="sidebar-docs-header">
          <h2 className="sidebar-docs-title">Documents</h2>
          <button onClick={() => pdfInputRef.current?.click()} className="sidebar-upload-btn">
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
              <polyline points="17 8 12 3 7 8"/>
              <line x1="12" y1="3" x2="12" y2="15"/>
            </svg>
            Upload
          </button>
        </div>

        {/* Search + New Folder */}
        <div className="sidebar-toolbar">
          <div className="search-box" style={{ flex: 1 }}>
            <svg className="search-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <circle cx="11" cy="11" r="8"/>
              <line x1="21" y1="21" x2="16.65" y2="16.65"/>
            </svg>
            <input
              type="text"
              placeholder="Search documents..."
              className="search-input"
              value={searchQuery}
              onChange={handleSearchChange}
            />
          </div>
          <button onClick={() => setShowNewFolderInput(v => !v)} className="toolbar-icon-btn" title="New folder">
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/>
              <line x1="12" y1="11" x2="12" y2="17"/>
              <line x1="9" y1="14" x2="15" y2="14"/>
            </svg>
          </button>
        </div>

        {/* New folder input */}
        {showNewFolderInput && (
          <div className="new-folder-input">
            <input
              type="text"
              placeholder="Folder name"
              value={newFolderName}
              onChange={e => setNewFolderName(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter') handleCreateFolder(); if (e.key === 'Escape') setShowNewFolderInput(false); }}
              autoFocus
            />
            <button className="toolbar-btn" onClick={handleCreateFolder}>Create</button>
            <button className="toolbar-btn" onClick={() => setShowNewFolderInput(false)}>✕</button>
          </div>
        )}

        {/* Folder tabs */}
        {folders.length > 0 && (
          <div className="folder-tabs">
            {folders.map(f => (
              <div key={f.id} className="folder-tab-wrap">
                <button
                  className={`folder-tab ${activeFolderId === f.id ? 'active' : ''}`}
                  onClick={() => setActiveFolderId(f.id)}
                >
                  {f.name}
                </button>
                <button
                  className="folder-tab-delete"
                  onClick={(e) => handleDeleteFolder(e, f.id)}
                  title="Delete folder"
                >✕</button>
              </div>
            ))}
          </div>
        )}

        {/* Search results */}
        {searchQuery.trim() && (
          <div className="search-results">
            <div className="search-results-header">
              {searchResults.length > 0
                ? `${searchResults.length} result${searchResults.length !== 1 ? 's' : ''}`
                : 'No results'}
            </div>
            {searchResults.map(r => (
              <div key={r.id} className="search-result-item">
                <div className="search-result-title">{r.title}</div>
                <div className="search-result-doc">{r.doc_name || r.original_filename}</div>
                {r.summary && <div className="search-result-summary">{r.summary}</div>}
                <div className="search-result-pages">Pages {r.start_page}–{r.end_page}</div>
              </div>
            ))}
          </div>
        )}

        {/* Document list */}
        <div className="doc-list">
          {documents.length === 0 ? (
            <div className="empty-docs">
              <span className="empty-docs-icon">📂</span>
              <p>No documents yet</p>
              <p style={{ fontSize: '12px', marginTop: '6px' }}>Upload a PDF to get started</p>
            </div>
          ) : (
            documents.map(doc => (
              <div
                key={doc.id}
                className={`doc-item ${doc.id === activeDocId ? 'active' : ''} ${doc.status !== 'done' ? 'not-ready' : ''}`}
                onClick={() => handleDocSelect(doc)}
              >
                <div className="doc-icon-wrap">
                  <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
                    <polyline points="14 2 14 8 20 8"/>
                    <line x1="16" y1="13" x2="8" y2="13"/>
                    <line x1="16" y1="17" x2="8" y2="17"/>
                    <polyline points="10 9 9 9 8 9"/>
                  </svg>
                </div>

                <div className="doc-info">
                  <span className="doc-name">{doc.original_filename}</span>
                  <span className="doc-timestamp">
                    {doc.status === 'done'
                      ? `Uploaded at ${formatDocDate(doc.uploaded_at)}`
                      : <StatusBadge status={doc.status} />}
                  </span>
                </div>

                {/* Play / Stop PageIndex button */}
                {(doc.status === 'pending' || doc.status === 'failed' || doc.status === 'processing') && (
                  <button
                    className={`run-pageindex-btn${doc.status === 'processing' ? ' stop' : ''}`}
                    onClick={(e) => doc.status === 'processing' ? handleStopPageIndex(e, doc) : handleRunPageIndex(e, doc)}
                    title={doc.status === 'processing' ? 'Stop processing' : 'Run PageIndex'}
                  >
                    {doc.status === 'processing' ? (
                      <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor">
                        <rect x="4" y="4" width="16" height="16" rx="2"/>
                      </svg>
                    ) : (
                      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                        <polygon points="5 3 19 12 5 21 5 3"/>
                      </svg>
                    )}
                  </button>
                )}

                <div className="doc-menu-wrap" ref={openMenuId === doc.id ? menuRef : null}>
                  <button
                    className="doc-menu-btn"
                    onClick={(e) => { e.stopPropagation(); setOpenMenuId(openMenuId === doc.id ? null : doc.id); }}
                    title="Options"
                  >
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                      <circle cx="12" cy="5" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="12" cy="19" r="1"/>
                    </svg>
                  </button>

                  {openMenuId === doc.id && (
                    <div className="doc-dropdown">
                      <button className="doc-dropdown-item" onClick={(e) => handleCopyId(e, doc)}>
                        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                          <rect x="9" y="9" width="13" height="13" rx="2" ry="2"/>
                          <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>
                        </svg>
                        Copy ID
                      </button>
                      <button className="doc-dropdown-item" onClick={(e) => handleDownloadPdf(e, doc)}>
                        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                          <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
                          <polyline points="7 10 12 15 17 10"/>
                          <line x1="12" y1="15" x2="12" y2="3"/>
                        </svg>
                        Download
                      </button>
                      <button className="doc-dropdown-item" onClick={(e) => handleShare(e, doc)}>
                        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                          <circle cx="18" cy="5" r="3"/><circle cx="6" cy="12" r="3"/><circle cx="18" cy="19" r="3"/>
                          <line x1="8.59" y1="13.51" x2="15.42" y2="17.49"/>
                          <line x1="15.41" y1="6.51" x2="8.59" y2="10.49"/>
                        </svg>
                        Share
                      </button>
                      <div className="doc-dropdown-divider" />
                      <button className="doc-dropdown-item danger" onClick={(e) => { setOpenMenuId(null); handleDeleteDoc(e, doc.id); }}>
                        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                          <polyline points="3 6 5 6 21 6"/>
                          <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/>
                          <path d="M10 11v6"/><path d="M14 11v6"/>
                          <path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/>
                        </svg>
                        Delete
                      </button>
                    </div>
                  )}
                </div>
              </div>
            ))
          )}
        </div>

        {/* ================================================================
            CASES SECTION
            ================================================================ */}
        <div className="sidebar-section-header" onClick={() => setCasesPanelExpanded(v => !v)}>
          <span className="sidebar-section-title">Cases</span>
          <div className="sidebar-section-actions">
            <button className="sidebar-icon-btn-sm" title="New case" onClick={(e) => { e.stopPropagation(); setShowNewCaseInput(v => !v); }}>
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                <line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>
              </svg>
            </button>
            <span className={`log-chevron ${casesPanelExpanded ? 'expanded' : ''}`}>
              <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                <polyline points="18 15 12 9 6 15"/>
              </svg>
            </span>
          </div>
        </div>

        {showNewCaseInput && (
          <div className="new-folder-input">
            <input
              type="text"
              placeholder="Case title"
              value={newCaseTitle}
              onChange={e => setNewCaseTitle(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter') handleCreateCase(); if (e.key === 'Escape') setShowNewCaseInput(false); }}
              autoFocus
            />
            <button className="toolbar-btn" onClick={handleCreateCase}>Create</button>
            <button className="toolbar-btn" onClick={() => setShowNewCaseInput(false)}>✕</button>
          </div>
        )}

        {casesPanelExpanded && (
          <div className="case-list">
            {cases.length === 0 ? (
              <div className="case-empty">
                No cases yet
                <button className="case-sample-btn" onClick={handleLoadSample} title="Load a pre-filled demo case">
                  Try sample case
                </button>
              </div>
            ) : (
              cases.map(cas => (
                <div
                  key={cas.id}
                  className={`case-item ${cas.id === activeCaseId ? 'active' : ''}`}
                  onClick={() => { setActiveDocId(null); setTreeData(null); handleCaseSelect(cas); }}
                >
                  <span className={`case-status-dot status-${cas.status}`} />
                  <div className="case-info">
                    <span className="case-title">{cas.title}</span>
                    <span className="case-status-label">{caseStatusLabel[cas.status] || cas.status}</span>
                  </div>
                  <button className="doc-menu-btn" title="Delete case" onClick={(e) => handleDeleteCase(e, cas.id)}>
                    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <polyline points="3 6 5 6 21 6"/>
                      <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/>
                    </svg>
                  </button>
                </div>
              ))
            )}
          </div>
        )}

        {/* ================================================================
            ACTIVITY LOG
            ================================================================ */}
        <div className="log-panel">
          <div className="log-panel-header" onClick={() => setLogsExpanded(v => !v)}>
            <div className="log-panel-title">
              <span className={`log-dot ${logs.length > 0 && logs[logs.length - 1]?.level === 'error' ? 'error' : 'ok'}`} />
              Activity Log
            </div>
            <span className={`log-chevron ${logsExpanded ? 'expanded' : ''}`}>
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                <polyline points="18 15 12 9 6 15"/>
              </svg>
            </span>
          </div>

          {logsExpanded && (
            <div className="log-entries">
              {logs.length === 0 ? (
                <div className="log-empty">Waiting for activity...</div>
              ) : (
                logs.map(entry => (
                  <div key={entry.id} className={`log-entry log-${entry.level}`}>
                    <span className="log-ts">{entry.ts}</span>
                    <span className="log-msg">{entry.msg}</span>
                  </div>
                ))
              )}
              <div ref={logEndRef} />
            </div>
          )}
        </div>
      </div>

      {/* ================================================================
          MIDDLE PANEL — PDF Viewer
          ================================================================ */}
      <div className="pdf-panel">
        <div className="panel-header">
          {activeCaseId ? (
            <div className="case-pdf-toggle">
              <button
                className={`case-pdf-toggle-btn ${casePdfParty === 'petitioner' ? 'active' : ''}`}
                onClick={() => setCasePdfParty('petitioner')}
              >Petitioner</button>
              <button
                className={`case-pdf-toggle-btn ${casePdfParty === 'respondent' ? 'active' : ''}`}
                onClick={() => setCasePdfParty('respondent')}
              >Respondent</button>
            </div>
          ) : (
            <span className="panel-title">
              {activeDoc ? activeDoc.original_filename : 'PDF Viewer'}
            </span>
          )}
          {viewDocId && (
            <a
              href={`${API}/api/documents/${viewDocId}/file`}
              target="_blank"
              rel="noreferrer"
              className="panel-action-btn"
              title="Open in new tab"
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/>
                <polyline points="15 3 21 3 21 9"/>
                <line x1="10" y1="14" x2="21" y2="3"/>
              </svg>
            </a>
          )}
        </div>

        <div className="pdf-viewport" ref={pdfContainerRef}>
          {viewDocId ? (
            <>
              <Document
                file={`${API}/api/documents/${viewDocId}/file`}
                onLoadSuccess={({ numPages }) => setNumPages(numPages)}
                loading={<div className="pdf-loading"><div className="spinner" /><p>Loading PDF…</p></div>}
                error={<div className="pdf-error">Failed to load PDF</div>}
              >
                <Page
                  pageNumber={pdfPage}
                  width={pdfWidth || undefined}
                  renderTextLayer={true}
                  renderAnnotationLayer={true}
                />
              </Document>

              {numPages && (
                <div className="pdf-controls">
                  <button
                    className="pdf-nav-btn"
                    onClick={() => setPdfPage(p => Math.max(1, p - 1))}
                    disabled={pdfPage <= 1}
                  >‹</button>
                  <span className="pdf-page-info">
                    <input
                      type="number"
                      className="pdf-page-input"
                      value={pdfPage}
                      min={1}
                      max={numPages}
                      onChange={e => {
                        const v = parseInt(e.target.value, 10);
                        if (v >= 1 && v <= numPages) setPdfPage(v);
                      }}
                    />
                    <span>/ {numPages}</span>
                  </span>
                  <button
                    className="pdf-nav-btn"
                    onClick={() => setPdfPage(p => Math.min(numPages, p + 1))}
                    disabled={pdfPage >= numPages}
                  >›</button>
                </div>
              )}
            </>
          ) : (
            <div className="pdf-empty">
              <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="#cbd5e1" strokeWidth="1.2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
                <polyline points="14 2 14 8 20 8"/>
                <line x1="16" y1="13" x2="8" y2="13"/>
                <line x1="16" y1="17" x2="8" y2="17"/>
                <polyline points="10 9 9 9 8 9"/>
              </svg>
              <p>Select a document to preview</p>
            </div>
          )}
        </div>
      </div>

      {/* ================================================================
          RIGHT PANEL — context-sensitive: Doc tree OR Case pipeline
          ================================================================ */}
      <div className="main-content">

        {activeCaseId && activeCaseData ? (
          /* ============================================================
             CASE MODE
             ============================================================ */
          <>
            {/* Progress bar */}
            <div className="case-progress-bar">
              {[
                { key: 'setup', label: 'Setup', statuses: ['pending'] },
                { key: 'clerk', label: 'Clerk', statuses: ['clerk_running', 'clerk_done'] },
                { key: 'registrar', label: 'Registrar', statuses: ['registrar_running', 'registrar_done', 'review_pending', 'review_approved', 'review_rejected'] },
                { key: 'judge', label: 'Judge', statuses: ['judge_running', 'judge_done'] },
              ].map(({ key, label, statuses }, idx) => {
                const caseStatus = activeCaseData.case.status;
                const isActive = activeCaseTab === key;
                const isDone = statuses.some(s => ['clerk_done', 'registrar_done', 'review_approved', 'review_rejected', 'judge_done'].includes(s)) &&
                  ['clerk_done', 'registrar_done', 'review_pending', 'review_approved', 'review_rejected', 'judge_running', 'judge_done'].includes(caseStatus) && key === 'clerk' ||
                  ['review_pending', 'review_approved', 'review_rejected', 'judge_running', 'judge_done'].includes(caseStatus) && key === 'registrar' ||
                  ['judge_done'].includes(caseStatus) && key === 'judge';
                const isRunning = (key === 'clerk' && caseStatus === 'clerk_running') ||
                  (key === 'registrar' && caseStatus === 'registrar_running') ||
                  (key === 'judge' && caseStatus === 'judge_running');
                return (
                  <button
                    key={key}
                    className={`case-progress-step ${isActive ? 'active' : ''} ${isDone ? 'done' : ''} ${isRunning ? 'running' : ''}`}
                    onClick={() => setActiveCaseTab(key)}
                  >
                    <span className="case-step-dot" />
                    <span className="case-step-label">{label}</span>
                    {idx < 3 && <span className="case-step-arrow">›</span>}
                  </button>
                );
              })}
              {activeCaseData.case.status === 'error' && (
                <span className="case-error-badge">Error</span>
              )}
            </div>

            {/* Case tab content */}
            <div className="case-tab-content">

              {/* ─── Setup Tab ─── */}
              {activeCaseTab === 'setup' && (() => {
                const cStatus = activeCaseData.case.status;
                const petDoc = activeCaseData.documents?.find(d => d.party_role === 'Petitioner');
                const resDoc = activeCaseData.documents?.find(d => d.party_role === 'Respondent');
                const canRunClerk = petDoc && resDoc && !['clerk_running', 'clerk_done', 'registrar_running', 'review_pending', 'review_approved', 'judge_running', 'judge_done'].includes(cStatus);
                return (
                  <div className="case-setup-panel">
                    <div className="case-setup-meta">
                      <div className="case-setup-title">{activeCaseData.case.title}</div>
                      <div className="case-setup-model">{activeCaseData.case.model}</div>
                    </div>
                    {[
                      { role: 'Petitioner', doc: petDoc },
                      { role: 'Respondent', doc: resDoc },
                    ].map(({ role, doc }) => (
                      <div key={role} className="case-party-slot">
                        <div className="case-party-role">{role}</div>
                        {doc ? (
                          <div className="case-party-doc-attached">
                            <span className={`case-clerk-status ${doc.clerk_status}`}>{doc.clerk_status}</span>
                            <span className="case-party-doc-name">
                              {documents.find(d => d.id === doc.doc_id)?.original_filename || `Doc #${doc.doc_id}`}
                            </span>
                            <span className="case-party-doc-type">{doc.document_type}</span>
                          </div>
                        ) : (
                          <div className="case-party-attach">
                            <select
                              className="case-doc-select"
                              defaultValue=""
                              onChange={e => {
                                if (e.target.value) {
                                  handleAddCaseDocument(role, e.target.value, role === 'Petitioner' ? 'Petition' : 'Reply');
                                  e.target.value = '';
                                }
                              }}
                            >
                              <option value="" disabled>Attach a document…</option>
                              {documents.filter(d => d.status === 'done').map(d => (
                                <option key={d.id} value={d.id}>{d.original_filename}</option>
                              ))}
                            </select>
                          </div>
                        )}
                      </div>
                    ))}
                    <div className="case-setup-actions">
                      <button
                        className="case-run-btn"
                        disabled={!canRunClerk}
                        onClick={handleRunClerk}
                        title={!canRunClerk ? 'Attach both documents first' : 'Run Clerk Agent on both documents'}
                      >
                        {cStatus === 'clerk_running' ? (
                          <><span className="spinner-sm" /> Running Clerk…</>
                        ) : (
                          '▶ Run Clerk Agent'
                        )}
                      </button>
                    </div>
                    {activeCaseData.case.error_message && (
                      <div className="case-error-msg">{activeCaseData.case.error_message}</div>
                    )}
                  </div>
                );
              })()}

              {/* ─── Clerk Tab ─── */}
              {activeCaseTab === 'clerk' && (() => {
                const cStatus = activeCaseData.case.status;
                const renderSubmission = (cd) => {
                  if (!cd) return <div className="case-clerk-empty">No document attached for this party.</div>;
                  if (cd.clerk_status === 'pending') return <div className="case-clerk-empty">Not yet processed.</div>;
                  if (cd.clerk_status === 'running') return <div className="case-clerk-empty"><span className="spinner-sm" /> Processing…</div>;
                  if (!cd.clerk_output) return <div className="case-clerk-empty">No output yet.</div>;
                  let sub;
                  try { sub = JSON.parse(cd.clerk_output); } catch { return <div className="case-clerk-empty">Invalid output.</div>; }
                  return (
                    <div className="clerk-output">
                      <div className="clerk-section">
                        <div className="clerk-section-label">Facts ({sub.extracted_facts?.length || 0})</div>
                        {(sub.extracted_facts || []).map((f, i) => (
                          <div key={i} className="clerk-fact">
                            <span className="clerk-page-tag">p.{f.page_index}</span>
                            {f.statement}
                          </div>
                        ))}
                      </div>
                      <div className="clerk-section">
                        <div className="clerk-section-label">Issues ({sub.issues_raised?.length || 0})</div>
                        {(sub.issues_raised || []).map((issue, i) => (
                          <div key={i} className="clerk-issue">{issue}</div>
                        ))}
                      </div>
                      <div className="clerk-section">
                        <div className="clerk-section-label">Citations ({sub.cited_laws_and_cases?.length || 0})</div>
                        {(sub.cited_laws_and_cases || []).map((c, i) => (
                          <div key={i} className="clerk-citation">
                            {c.citation}
                            {c.page_index && <span className="clerk-page-tag">p.{c.page_index}</span>}
                          </div>
                        ))}
                      </div>
                      <div className="clerk-section">
                        <div className="clerk-section-label">Prayers ({sub.prayers?.length || 0})</div>
                        {(sub.prayers || []).map((p, i) => (
                          <div key={i} className="clerk-prayer">{p}</div>
                        ))}
                      </div>
                    </div>
                  );
                };
                const petDoc = activeCaseData.documents?.find(d => d.party_role === 'Petitioner');
                const resDoc = activeCaseData.documents?.find(d => d.party_role === 'Respondent');
                const canRunRegistrar = cStatus === 'clerk_done';
                return (
                  <div className="clerk-panel">
                    <div className="clerk-columns">
                      <div className="clerk-column">
                        <div className="clerk-column-header petitioner">Petitioner</div>
                        {renderSubmission(petDoc)}
                      </div>
                      <div className="clerk-column">
                        <div className="clerk-column-header respondent">Respondent</div>
                        {renderSubmission(resDoc)}
                      </div>
                    </div>
                    {canRunRegistrar && (
                      <div className="case-setup-actions">
                        <button className="case-run-btn" onClick={handleRunRegistrar}>
                          ▶ Run Registrar Agent
                        </button>
                      </div>
                    )}
                    {cStatus === 'registrar_running' && (
                      <div className="case-running-banner"><span className="spinner-sm" /> Registrar is building the matrix…</div>
                    )}
                  </div>
                );
              })()}

              {/* ─── Registrar Tab ─── */}
              {activeCaseTab === 'registrar' && (() => {
                const cStatus = activeCaseData.case.status;
                const result = activeCaseData.result;
                if (!result?.adversarial_matrix) {
                  return (
                    <div className="empty-state">
                      <div className="empty-icon">⚖️</div>
                      <h2>Registrar not run yet</h2>
                      <p>Complete the Clerk stage first, then run the Registrar Agent.</p>
                    </div>
                  );
                }
                let matrix;
                try { matrix = JSON.parse(result.adversarial_matrix); } catch {
                  return <div className="case-error-msg">Failed to parse matrix.</div>;
                }
                const reviewStatus = result.human_review_status;
                return (
                  <div className="registrar-panel">
                    <div className="registrar-undisputed">
                      <div className="registrar-section-label">Undisputed Background ({matrix.undisputed_background?.length || 0} facts)</div>
                      {(matrix.undisputed_background || []).map((f, i) => (
                        <div key={i} className="registrar-fact">{f}</div>
                      ))}
                    </div>
                    <div className="registrar-issues-label">
                      Framed Issues ({matrix.framed_issues?.length || 0})
                    </div>
                    {(matrix.framed_issues || []).map((issue) => (
                      <details key={issue.issue_id} className="registrar-issue">
                        <summary className="registrar-issue-summary">
                          <span className="issue-id-badge">{issue.issue_id}</span>
                          {issue.neutral_issue_statement}
                        </summary>
                        <div className="registrar-issue-body">
                          <div className="stance-block petitioner">
                            <div className="stance-label">Petitioner</div>
                            {(issue.petitioner_stance?.arguments || []).map((a, i) => (
                              <div key={i} className="stance-arg">{a}</div>
                            ))}
                            {(issue.petitioner_stance?.supporting_citations || []).map((c, i) => (
                              <div key={i} className="stance-citation">{c}</div>
                            ))}
                          </div>
                          <div className="stance-block respondent">
                            <div className="stance-label">Respondent</div>
                            {(issue.respondent_stance?.arguments || []).map((a, i) => (
                              <div key={i} className="stance-arg">{a}</div>
                            ))}
                            {(issue.respondent_stance?.supporting_citations || []).map((c, i) => (
                              <div key={i} className="stance-citation">{c}</div>
                            ))}
                          </div>
                        </div>
                      </details>
                    ))}
                    <div className="review-gate">
                      {reviewStatus === 'pending' && (
                        <>
                          <div className="review-gate-label">Human Review Required</div>
                          <div className="review-gate-actions">
                            <button className="review-approve-btn" onClick={() => handleReviewMatrix('approve')}>✓ Approve</button>
                            <button className="review-reject-btn" onClick={() => handleReviewMatrix('reject')}>✗ Reject</button>
                          </div>
                        </>
                      )}
                      {reviewStatus === 'approved' && cStatus !== 'judge_running' && cStatus !== 'judge_done' && (
                        <div className="case-setup-actions">
                          <button className="case-run-btn approved" onClick={handleRunJudge}>
                            ▶ Run Judge Agent
                          </button>
                        </div>
                      )}
                      {reviewStatus === 'approved' && cStatus === 'judge_running' && (
                        <div className="case-running-banner"><span className="spinner-sm" /> Judge is deliberating…</div>
                      )}
                      {reviewStatus === 'rejected' && (
                        <div className="case-error-msg">Matrix rejected — re-run Registrar to rebuild.</div>
                      )}
                      {reviewStatus === 'approved' && cStatus === 'judge_done' && (
                        <div className="review-done-badge">✓ Approved — see Judge tab for order</div>
                      )}
                    </div>
                  </div>
                );
              })()}

              {/* ─── Judge Tab ─── */}
              {activeCaseTab === 'judge' && (() => {
                const result = activeCaseData.result;
                if (!result?.draft_court_order) {
                  return (
                    <div className="empty-state">
                      <div className="empty-icon">🔨</div>
                      <h2>Judge not run yet</h2>
                      <p>Approve the Registrar matrix, then run the Judge Agent.</p>
                    </div>
                  );
                }
                let order;
                try { order = JSON.parse(result.draft_court_order); } catch {
                  return <div className="case-error-msg">Failed to parse court order.</div>;
                }
                return (
                  <div className="judge-panel">
                    <div className="judge-header">
                      <div className="judge-case-title">{order.case_title}</div>
                      <button className="panel-action-btn" onClick={handleExportOrder} title="Export as Markdown">
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                          <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
                          <polyline points="7 10 12 15 17 10"/>
                          <line x1="12" y1="15" x2="12" y2="3"/>
                        </svg>
                      </button>
                    </div>
                    <div className="judge-background">
                      <div className="judge-section-label">Background Facts</div>
                      <p className="judge-background-text">{order.background_facts}</p>
                    </div>
                    <div className="judge-section-label">Decisions on Issues</div>
                    {(order.reasoned_decisions || []).map((rd) => (
                      <details key={rd.issue_id} className="judge-issue" open>
                        <summary className="judge-issue-summary">
                          <span className="issue-id-badge">{rd.issue_id}</span>
                          {rd.issue_statement}
                        </summary>
                        <div className="judge-irac">
                          <div className="irac-block">
                            <div className="irac-label">Rule</div>
                            <div className="irac-text">{rd.rule}</div>
                          </div>
                          <div className="irac-block">
                            <div className="irac-label">Analysis</div>
                            <div className="irac-text">{rd.analysis}</div>
                          </div>
                          <div className="irac-block conclusion">
                            <div className="irac-label">Conclusion</div>
                            <div className="irac-text">{rd.conclusion}</div>
                          </div>
                        </div>
                      </details>
                    ))}
                    <div className="judge-final-order">
                      <div className="judge-section-label">Final Order</div>
                      <div className="judge-order-text">{order.final_order}</div>
                    </div>
                  </div>
                );
              })()}

            </div>
          </>
        ) : (
          /* ============================================================
             DOCUMENT MODE (unchanged)
             ============================================================ */
          <>
            <div className="panel-header">
              <div className="right-tabs">
                <button
                  className={`right-tab ${activeRightTab === 'tree' ? 'active' : ''}`}
                  onClick={() => setActiveRightTab('tree')}
                >Tree</button>
                <button
                  className={`right-tab ${activeRightTab === 'text' ? 'active' : ''}`}
                  onClick={() => setActiveRightTab('text')}
                >Text</button>
              </div>
              <div className="panel-header-actions">
                {activeRightTab === 'tree' && treeData && (
                  <>
                    <span className="node-count">{countNodes(treeData.structure)} nodes</span>
                    {annotations.length > 0 && (
                      <button onClick={handleExportNotes} className="panel-action-btn" title={`Export ${annotations.length} note${annotations.length !== 1 ? 's' : ''} as Markdown`}>
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                          <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
                          <polyline points="14 2 14 8 20 8"/>
                          <line x1="16" y1="13" x2="8" y2="13"/>
                          <line x1="16" y1="17" x2="8" y2="17"/>
                          <polyline points="10 9 9 9 8 9"/>
                        </svg>
                      </button>
                    )}
                    <button onClick={handleDownload} className="panel-action-btn" title="Download JSON">
                      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                        <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
                        <polyline points="7 10 12 15 17 10"/>
                        <line x1="12" y1="15" x2="12" y2="3"/>
                      </svg>
                    </button>
                  </>
                )}
                {activeRightTab === 'text' && pageText && !pageText.error && (
                  <>
                    <span className="text-stats">
                      {pageText.word_count.toLocaleString()} words
                    </span>
                    <button
                      className="panel-action-btn"
                      title="Copy page text"
                      onClick={() => navigator.clipboard.writeText(
                        pageText.blocks.map(b => b.text).join('\n\n')
                      )}
                    >
                      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                        <rect x="9" y="9" width="13" height="13" rx="2" ry="2"/>
                        <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>
                      </svg>
                    </button>
                  </>
                )}
              </div>
            </div>

            {error && (
              <div className="error-message">
                <span>⚠️</span> {error}
              </div>
            )}

            {activeRightTab === 'tree' ? (
              treeLoading ? (
                <div className="loading">
                  <div className="spinner" />
                  <p>Building tree...</p>
                </div>
              ) : treeData ? (
                <div className="tree-structure-scroll">
                  {(treeData.structure || []).map((node, i) => (
                    <TreeNode key={i} node={node}
                      onNodeClick={page => setPdfPage(page)}
                      activePage={pdfPage}
                      annotations={annotations}
                      editingAnnotationNodeId={editingAnnotationNodeId}
                      setEditingAnnotationNodeId={setEditingAnnotationNodeId}
                      annotationDraft={annotationDraft}
                      setAnnotationDraft={setAnnotationDraft}
                      onAnnotationSave={handleAnnotationSave}
                    />
                  ))}
                </div>
              ) : (
                <div className="empty-state">
                  <div className="empty-icon">🌲</div>
                  <h2>No tree yet</h2>
                  <p>Select a processed document to view its PageIndex tree structure.</p>
                </div>
              )
            ) : (
              !activeDocId ? (
                <div className="empty-state">
                  <div className="empty-icon">📝</div>
                  <h2>No document selected</h2>
                  <p>Select a document to view extracted text.</p>
                </div>
              ) : pageTextLoading ? (
                <div className="loading">
                  <div className="spinner" />
                  <p>Extracting text…</p>
                </div>
              ) : pageText?.error ? (
                <div className="empty-state">
                  <div className="empty-icon">⚠️</div>
                  <h2>Extraction failed</h2>
                  <p>{pageText.error}</p>
                </div>
              ) : pageText?.blocks?.length > 0 ? (
                <div className="text-panel">
                  <div className="text-page-info">
                    <span>Page {pageText.page} of {pageText.total_pages}</span>
                    <span className="text-info-sep">·</span>
                    <span>{pageText.word_count.toLocaleString()} words</span>
                    <span className="text-info-sep">·</span>
                    <span>{pageText.char_count.toLocaleString()} chars</span>
                  </div>
                  {pageText.blocks.map((block, i) => (
                    <p key={i} className={`text-block${block.is_header ? ' is-header' : ''}`}>
                      {block.text}
                    </p>
                  ))}
                </div>
              ) : (
                <div className="empty-state">
                  <div className="empty-icon">🖼️</div>
                  <h2>No text on this page</h2>
                  <p>This page may be image-based or contain no selectable text.</p>
                </div>
              )
            )}
          </>
        )}
      </div>
    </div>
  );
}
