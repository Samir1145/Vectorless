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
  const [documents, setDocuments] = useState([]);
  const [error, setError] = useState(null);

  // Top-level view routing
  const [view, setView] = useState('dashboard'); // 'dashboard' | 'case'
  const [showNewCaseModal, setShowNewCaseModal] = useState(false);
  const [newCaseModel, setNewCaseModel] = useState('gpt-4o-2024-11-20');
  const [dashboardSearch, setDashboardSearch] = useState('');
  const [showTrash, setShowTrash] = useState(false);
  const [archivedCases, setArchivedCases] = useState([]);

  // PDF viewer
  const [pdfPage, setPdfPage] = useState(1);   // tracks visible page for tree sync
  const [numPages, setNumPages] = useState(null);
  const [pdfWidth, setPdfWidth] = useState(null);
  const pdfContainerRef = useRef(null);
  const pdfPageRefs = useRef({});              // pageNum → wrapper div element

  // Log panel
  const [logs, setLogs] = useState([]);
  const [logsOpen, setLogsOpen] = useState(false);
  const [beaconHovered, setBeaconHovered] = useState(false);
  const logEndRef = useRef(null);
  const lastLogIdRef = useRef(0);

  // Cases
  const [cases, setCases] = useState([]);
  const [activeCaseId, setActiveCaseId] = useState(null);
  const [activeCaseData, setActiveCaseData] = useState(null); // { case, documents, result }
  const [newCaseTitle, setNewCaseTitle] = useState('');
  const [activeCaseTab, setActiveCaseTab] = useState('setup');
  const [casePdfParty, setCasePdfParty] = useState('petitioner');
  const casePollRef = useRef(null);

  // D5: doc column
  const [activeDocDetails, setActiveDocDetails] = useState(null);

  // Mode switch: 'doc' = tree+notes, 'agent' = agent output
  const [sidebarFocus, setSidebarFocus] = useState({ type: 'doc' });

  // NOTES sub-tab (within Mode A)
  const [docViewMode, setDocViewMode] = useState('index'); // 'index' | 'notes'
  const [notesFilter, setNotesFilter] = useState('all');   // 'all' | 'human' | 'agent' | 'flag'
  const [notesGenerating, setNotesGenerating] = useState(false);
  const notesGenPollRef = useRef(null);
  // Add-note form
  const [notesAddOpen, setNotesAddOpen] = useState(false);
  const [notesAddPage, setNotesAddPage] = useState('');
  const [notesAddBody, setNotesAddBody] = useState('');
  // PDF highlight on scrollToPage
  const [highlightedPage, setHighlightedPage] = useState(null);
  // Annotations for active doc
  const [annotations, setAnnotations] = useState([]);
  const [editingAnnotationNodeId, setEditingAnnotationNodeId] = useState(null);
  const [annotationDraft, setAnnotationDraft] = useState('');

  // Case workspace sidebar
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [docsExpanded, setDocsExpanded] = useState(true);
  const [caseAgentsPanelExpanded, setCaseAgentsPanelExpanded] = useState(true);
  const [editingPartyRole, setEditingPartyRole] = useState(null);
  const [editingPartyNameValue, setEditingPartyNameValue] = useState('');

  // Attach-document modal (Direction 4)
  const [attachModal, setAttachModal] = useState({ open: false, role: null });
  const [attachModalTab, setAttachModalTab] = useState('upload'); // 'upload' | 'library'
  const [modalUploadState, setModalUploadState] = useState(null); // null|'uploading'|'indexing'|'attaching'|'done'|'error'
  const [modalUploadDocId, setModalUploadDocId] = useState(null);
  const [librarySearch, setLibrarySearch] = useState('');
  const pendingAutoAttachRef = useRef(null); // { docId, role }

  const pdfInputRef = useRef(null);
  const pollIntervalsRef = useRef({});   // doc_id -> intervalId

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
  const fetchDocuments = useCallback(async () => {
    try {
      const res = await fetch(`${API}/api/documents`);
      const data = await res.json();
      setDocuments(data.documents || []);
    } catch { /* server not ready yet */ }
  }, []);

  useEffect(() => {
    fetchDocuments();
  }, [fetchDocuments]);

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

  // Auto-attach + close modal when modal-uploaded doc finishes indexing
  useEffect(() => {
    const pending = pendingAutoAttachRef.current;
    if (!pending) return;
    const doc = documents.find(d => d.id === pending.docId);
    if (!doc) return;
    if (doc.status === 'done') {
      pendingAutoAttachRef.current = null;
      setModalUploadState('attaching');
      fetch(`${API}/api/cases/${activeCaseId}/documents`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ doc_id: pending.docId, party_role: pending.role, document_type: pending.role === 'Petitioner' ? 'Petition' : 'Written Statement' }),
      }).then(() => fetchCaseData(activeCaseId)).then(() => {
        setModalUploadState('done');
        setTimeout(() => {
          setAttachModal({ open: false, role: null });
          setModalUploadState(null);
          setModalUploadDocId(null);
          setAttachModalTab('upload');
        }, 900);
      }).catch(() => setModalUploadState('error'));
    } else if (doc.status === 'failed') {
      pendingAutoAttachRef.current = null;
      setModalUploadState('error');
    }
  }, [documents]); // eslint-disable-line react-hooks/exhaustive-deps

  // Measure PDF container width and keep it updated on resize.
  // Depends on `view` so the effect re-runs when the case panel mounts.
  useEffect(() => {
    const el = pdfContainerRef.current;
    if (!el) return;
    const update = () => setPdfWidth(el.clientWidth);
    update();
    const ro = new ResizeObserver(update);
    ro.observe(el);
    return () => ro.disconnect();
  }, [view]);

  // IntersectionObserver — track which PDF page is most visible, update pdfPage
  useEffect(() => {
    if (!numPages || !pdfContainerRef.current) return;
    const container = pdfContainerRef.current;
    const ratios = {};
    const observer = new IntersectionObserver(entries => {
      entries.forEach(e => {
        const p = parseInt(e.target.dataset.pageNum, 10);
        ratios[p] = e.intersectionRatio;
      });
      let best = null, bestRatio = -1;
      Object.entries(ratios).forEach(([p, r]) => {
        if (r > bestRatio) { bestRatio = r; best = parseInt(p, 10); }
      });
      if (best !== null) setPdfPage(best);
    }, { root: container, threshold: [0, 0.1, 0.25, 0.5, 0.75, 1.0] });
    Object.values(pdfPageRefs.current).forEach(el => { if (el) observer.observe(el); });
    return () => observer.disconnect();
  }, [numPages]);

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
    if (logsOpen) logEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [logs, logsOpen]);

  // ---------------------------------------------------------------------------
  // Upload (modal)
  // ---------------------------------------------------------------------------
  const handleModalUpload = async (file) => {
    if (!file.name.toLowerCase().endsWith('.pdf')) {
      setError('Please select a PDF file'); return;
    }
    setError(null);
    setModalUploadState('uploading');
    try {
      const health = await fetch(`${API}/health`, { method: 'HEAD' });
      if (!health.ok) throw new Error('Backend not reachable');
    } catch {
      setModalUploadState('error');
      setError('Backend server not running. Start it with: python3 server.py'); return;
    }
    const formData = new FormData();
    formData.append('pdf', file);
    try {
      const res = await fetch(`${API}/api/documents/upload`, { method: 'POST', body: formData });
      if (!res.ok) throw new Error('Upload failed');
      const newDoc = await res.json();
      await fetchDocuments();
      startPolling(newDoc.id);
      setModalUploadDocId(newDoc.id);
      setModalUploadState('indexing');
      pendingAutoAttachRef.current = { docId: newDoc.id, role: attachModal.role };
    } catch (e) {
      setModalUploadState('error');
      setError('Upload failed: ' + e.message);
    }
  };

  const handleFileSelect = async (event) => {
    const file = event.target.files?.[0];
    if (!file) return;
    event.target.value = '';
    await handleModalUpload(file);
  };

  // ---------------------------------------------------------------------------
  // Case helpers
  // ---------------------------------------------------------------------------
  // Ordered pipeline statuses — used for progress-bar done/running logic
  const PIPELINE_ORDER = [
    'pending',
    'clerk_running', 'clerk_done',
    'verifier_running', 'verifier_done',
    'registrar_running', 'registrar_done',
    'procedural_running', 'procedural_done',
    'devils_advocate_running',
    'review_pending', 'review_approved', 'review_rejected',
    'judge_running', 'judge_done',
    'drafter_running', 'complete',
  ];
  const statusRank = (s) => { const i = PIPELINE_ORDER.indexOf(s); return i === -1 ? 0 : i; };

  const inferCaseTab = (status) => {
    if (['clerk_running', 'clerk_done'].includes(status)) return 'clerk';
    if (['verifier_running', 'verifier_done'].includes(status)) return 'verifier';
    if (['registrar_running', 'registrar_done'].includes(status)) return 'registrar';
    if (['procedural_running', 'procedural_done'].includes(status)) return 'procedural';
    if (['devils_advocate_running', 'review_pending', 'review_approved', 'review_rejected'].includes(status)) return 'devils_advocate';
    if (['judge_running', 'judge_done'].includes(status)) return 'judge';
    if (['drafter_running', 'complete'].includes(status)) return 'drafter';
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
      const runningStatuses = ['clerk_running', 'verifier_running', 'registrar_running', 'procedural_running', 'devils_advocate_running', 'judge_running', 'drafter_running'];
      if (status && !runningStatuses.includes(status)) {
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
    if (cas.id === activeCaseId) { setView('case'); fetchCaseData(cas.id, true); return; }
    setActiveCaseId(cas.id);
    setCasePdfParty('petitioner');
    setSidebarFocus({ type: 'doc' });
    setView('case');
    const data = await fetchCaseData(cas.id, true);
    const status = data?.case?.status;
    if (status && ['clerk_running', 'verifier_running', 'registrar_running', 'procedural_running', 'devils_advocate_running', 'judge_running', 'drafter_running'].includes(status)) {
      startCasePoll(cas.id);
    }
  };

  const handleCreateCase = async (titleOverride, modelOverride) => {
    const title = (titleOverride || newCaseTitle).trim();
    if (!title) return;
    const model = modelOverride || newCaseModel;
    try {
      const res = await fetch(`${API}/api/cases`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title, model }),
      });
      const cas = await res.json();
      setCases(prev => [cas, ...prev]);
      setNewCaseTitle('');
      setShowNewCaseModal(false);
      handleCaseSelect(cas);
    } catch (e) { setError('Failed to create case: ' + e.message); }
  };

  const handleDeleteCase = async (e, caseId) => {
    e.stopPropagation();
    await fetch(`${API}/api/cases/${caseId}`, { method: 'DELETE' });
    setCases(prev => prev.filter(c => c.id !== caseId));
    if (activeCaseId === caseId) {
      if (casePollRef.current) { clearInterval(casePollRef.current); casePollRef.current = null; }
      setActiveCaseId(null);
      setActiveCaseData(null);
      setView('dashboard');
    }
  };

  const fetchArchivedCases = async () => {
    try {
      const res = await fetch(`${API}/api/cases/archived`);
      const data = await res.json();
      setArchivedCases(data.cases || []);
    } catch { /* ignore */ }
  };

  const handleOpenTrash = () => {
    setShowTrash(true);
    fetchArchivedCases();
  };

  const handleRestoreCase = async (e, caseId) => {
    e.stopPropagation();
    await fetch(`${API}/api/cases/${caseId}/restore`, { method: 'POST' });
    setArchivedCases(prev => prev.filter(c => c.id !== caseId));
    await fetchCases();
  };

  const handlePurgeCase = async (e, caseId) => {
    e.stopPropagation();
    await fetch(`${API}/api/cases/${caseId}/purge`, { method: 'DELETE' });
    setArchivedCases(prev => prev.filter(c => c.id !== caseId));
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

  const handleDetachCaseDoc = async (caseDocId) => {
    if (!activeCaseId) return;
    try {
      await fetch(`${API}/api/cases/${activeCaseId}/documents/${caseDocId}`, { method: 'DELETE' });
      await fetchCaseData(activeCaseId);
    } catch (e) { setError('Failed to remove document: ' + e.message); }
  };

  const handleUpdatePartyName = async (role, name) => {
    setEditingPartyRole(null);
    if (!activeCaseId || !name.trim()) return;
    try {
      await fetch(`${API}/api/cases/${activeCaseId}/party-names`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ role, name: name.trim() }),
      });
      await fetchCaseData(activeCaseId);
    } catch (e) { setError('Failed to update party name: ' + e.message); }
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
    let reason = '';
    if (action === 'reject') {
      reason = window.prompt('Reason for rejection (will be shown to the Registrar Agent on re-run):', '') || '';
    }
    try {
      await fetch(`${API}/api/cases/${activeCaseId}/review`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action, reason }),
      });
      await fetchCaseData(activeCaseId, true);
    } catch (e) { setError('Review action failed: ' + e.message); }
  };

  const handleRunVerifier = async () => {
    if (!activeCaseId) return;
    try {
      await fetch(`${API}/api/cases/${activeCaseId}/run/verifier`, { method: 'POST' });
      await fetchCaseData(activeCaseId, true);
      startCasePoll(activeCaseId);
    } catch (e) { setError('Failed to run Verifier: ' + e.message); }
  };

  const handleRunProcedural = async () => {
    if (!activeCaseId) return;
    try {
      await fetch(`${API}/api/cases/${activeCaseId}/run/procedural`, { method: 'POST' });
      await fetchCaseData(activeCaseId, true);
      startCasePoll(activeCaseId);
    } catch (e) { setError('Failed to run Procedural Agent: ' + e.message); }
  };

  const handleRunDevilsAdvocate = async () => {
    if (!activeCaseId) return;
    try {
      await fetch(`${API}/api/cases/${activeCaseId}/run/devils_advocate`, { method: 'POST' });
      await fetchCaseData(activeCaseId, true);
      startCasePoll(activeCaseId);
    } catch (e) { setError("Failed to run Devil's Advocate: " + e.message); }
  };

  const handleRunJudge = async () => {
    if (!activeCaseId) return;
    try {
      await fetch(`${API}/api/cases/${activeCaseId}/run/judge`, { method: 'POST' });
      await fetchCaseData(activeCaseId, true);
      startCasePoll(activeCaseId);
    } catch (e) { setError('Failed to run Judge: ' + e.message); }
  };

  const handleRunDrafter = async () => {
    if (!activeCaseId) return;
    try {
      await fetch(`${API}/api/cases/${activeCaseId}/run/drafter`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ jurisdiction_style: 'indian_high_court' }),
      });
      await fetchCaseData(activeCaseId, true);
      startCasePoll(activeCaseId);
    } catch (e) { setError('Failed to run Drafter: ' + e.message); }
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

  const handleDownloadFormalOrder = () => {
    if (!activeCaseData?.result?.formal_court_order) return;
    try {
      const order = JSON.parse(activeCaseData.result.formal_court_order);
      const content = order.body || order.formal_order || JSON.stringify(order, null, 2);
      const title = activeCaseData.case?.title || 'case';
      const blob = new Blob([content], { type: 'text/plain' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `${title.replace(/\s+/g, '_')}_formal_order.txt`;
      document.body.appendChild(a); a.click();
      document.body.removeChild(a); URL.revokeObjectURL(url);
    } catch (e) { setError('Download failed: ' + e.message); }
  };

  // D5: fetch full doc details (including tree) when active party doc changes
  const viewDocId = activeCaseId
    ? activeCaseData?.documents?.find(d => d.party_role.toLowerCase() === casePdfParty)?.doc_id ?? null
    : null;

  useEffect(() => {
    if (!viewDocId) { setActiveDocDetails(null); return; }
    fetch(`${API}/api/documents/${viewDocId}`)
      .then(r => r.json())
      .then(data => setActiveDocDetails(data))
      .catch(() => setActiveDocDetails(null));
    // Reset notes UI when switching docs
    setDocViewMode('index');
    setNotesFilter('all');
    setNotesAddOpen(false);
    setNotesGenerating(false);
    if (notesGenPollRef.current) { clearInterval(notesGenPollRef.current); notesGenPollRef.current = null; }
  }, [viewDocId]); // eslint-disable-line react-hooks/exhaustive-deps

  // scrollToPage: scroll PDF to page N and pulse highlight
  const scrollToPage = useCallback((pageNum) => {
    if (!pageNum) return;
    const el = pdfPageRefs.current[pageNum];
    if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' });
    setHighlightedPage(pageNum);
    setTimeout(() => setHighlightedPage(null), 1800);
  }, []);

  // Fetch annotations when active doc changes
  useEffect(() => {
    if (!viewDocId) { setAnnotations([]); return; }
    fetch(`${API}/api/documents/${viewDocId}/annotations`)
      .then(r => r.ok ? r.json() : { annotations: [] })
      .then(data => setAnnotations(data.annotations || []))
      .catch(() => setAnnotations([]));
  }, [viewDocId]); // eslint-disable-line react-hooks/exhaustive-deps

  const handleAnnotationSave = useCallback(async (nodeId, pageStart, nodeTitle, body) => {
    if (!viewDocId) return;
    try {
      await fetch(`${API}/api/documents/${viewDocId}/annotations`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ node_id: nodeId, page_start: pageStart, node_title: nodeTitle, body }),
      });
      const data = await fetch(`${API}/api/documents/${viewDocId}/annotations`).then(r => r.json());
      setAnnotations(data.annotations || []);
    } catch { /* annotation endpoint may not exist */ }
    setEditingAnnotationNodeId(null);
  }, [viewDocId]); // eslint-disable-line react-hooks/exhaustive-deps

  const handleGenerateNotes = useCallback(async () => {
    if (!viewDocId || notesGenerating) return;
    setNotesGenerating(true);
    try {
      await fetch(`${API}/api/documents/${viewDocId}/generate_notes`, { method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ model: activeCaseData?.case?.model || 'gpt-4o-2024-11-20' }),
      });
    } catch { setNotesGenerating(false); return; }
    // Poll status until done or failed
    notesGenPollRef.current = setInterval(async () => {
      try {
        const res = await fetch(`${API}/api/documents/${viewDocId}/status`);
        const data = await res.json();
        if (data.notes_status === 'done' || data.notes_status === 'failed') {
          clearInterval(notesGenPollRef.current); notesGenPollRef.current = null;
          setNotesGenerating(false);
          // Refresh annotations
          const annRes = await fetch(`${API}/api/documents/${viewDocId}/annotations`);
          const annData = await annRes.json();
          setAnnotations(annData.annotations || []);
        }
      } catch { /* retry */ }
    }, 2000);
  }, [viewDocId, notesGenerating, activeCaseData]); // eslint-disable-line react-hooks/exhaustive-deps

  const handleAddNote = useCallback(async () => {
    if (!viewDocId || !notesAddBody.trim()) return;
    const page = parseInt(notesAddPage) || pdfPage || 1;
    const nodeId = `page-${page}-human-${Date.now()}`;
    await fetch(`${API}/api/documents/${viewDocId}/annotations`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ node_id: nodeId, anchor_page: page, anchor_title: `Page ${page}`, anchor_path: '', body: notesAddBody.trim() }),
    }).catch(() => {});
    const data = await fetch(`${API}/api/documents/${viewDocId}/annotations`).then(r => r.json()).catch(() => ({ annotations: [] }));
    setAnnotations(data.annotations || []);
    setNotesAddOpen(false);
    setNotesAddPage('');
    setNotesAddBody('');
  }, [viewDocId, notesAddPage, notesAddBody, pdfPage]); // eslint-disable-line react-hooks/exhaustive-deps

  const caseStatusLabel = {
    pending: 'Setup',
    clerk_running: 'Clerk ⟳', clerk_done: 'Clerk ✓',
    verifier_running: 'Verifier ⟳', verifier_done: 'Verifier ✓',
    registrar_running: 'Registrar ⟳', registrar_done: 'Registrar ✓',
    procedural_running: 'Procedural ⟳', procedural_done: 'Procedural ✓',
    devils_advocate_running: "Devil's Advocate ⟳",
    review_pending: 'Review ⏳', review_approved: 'Review ✓', review_rejected: 'Review ✗',
    judge_running: 'Judge ⟳', judge_done: 'Judge ✓',
    drafter_running: 'Drafter ⟳', complete: 'Complete ✓',
    error: 'Error ✗',
  };

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------
  // ---------------------------------------------------------------------------
  // Pipeline stage helper (0-7) used by dashboard cards
  // ---------------------------------------------------------------------------
  const stageNum = (status) => {
    const m = {
      pending: 0,
      clerk_running: 1, verifier_running: 1, verifier_done: 2,
      registrar_running: 2, registrar_done: 3,
      procedural_running: 3, procedural_done: 4,
      devils_advocate_running: 4,
      review_pending: 5, review_approved: 5, review_rejected: 5,
      judge_running: 5, judge_done: 6,
      drafter_running: 6, complete: 7,
      error: 0,
    };
    return m[status] ?? 0;
  };

  const filteredCases = cases.filter(c =>
    !dashboardSearch.trim() || c.title.toLowerCase().includes(dashboardSearch.toLowerCase())
  );
  const totalCases   = cases.length;
  const inProgress   = cases.filter(c => c.status !== 'pending' && c.status !== 'complete' && c.status !== 'error').length;
  const completeCases = cases.filter(c => c.status === 'complete').length;
  const errorCases   = cases.filter(c => c.status === 'error').length;

  return (
    <div className="app" data-theme={theme}>

      {/* Hoisted file input — usable from any view */}
      <input
        type="file"
        ref={pdfInputRef}
        onChange={handleFileSelect}
        accept=".pdf"
        style={{ display: 'none' }}
      />

      {/* Error toast */}
      {error && (
        <div className="error-toast" onClick={() => setError(null)}>
          <span>⚠ {error}</span>
          <button className="error-toast-close">✕</button>
        </div>
      )}

      {/* ================================================================
          DASHBOARD VIEW
          ================================================================ */}
      {view === 'dashboard' && (
        <div className="dashboard">
          {/* Nav bar */}
          <div className="db-nav">
            <div className="db-nav-left">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/>
                <rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>
              </svg>
              <span className="db-nav-brand">PageIndex</span>
            </div>
            <div className="db-nav-right">
              <button className={`db-nav-icon-btn ${showTrash ? 'active' : ''}`} title="Trash" onClick={handleOpenTrash}>
                <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <polyline points="3 6 5 6 21 6"/>
                  <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/>
                  <path d="M10 11v6"/><path d="M14 11v6"/>
                  <path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/>
                </svg>
              </button>
              <button className="db-nav-icon-btn" title="Settings">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <circle cx="12" cy="12" r="3"/>
                  <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/>
                </svg>
              </button>
              <button className="theme-toggle-btn" onClick={toggleTheme} title={theme === 'dark' ? 'Light mode' : 'Dark mode'}>
                {theme === 'dark' ? (
                  <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <circle cx="12" cy="12" r="5"/>
                    <line x1="12" y1="1" x2="12" y2="3"/><line x1="12" y1="21" x2="12" y2="23"/>
                    <line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/>
                    <line x1="1" y1="12" x2="3" y2="12"/><line x1="21" y1="12" x2="23" y2="12"/>
                    <line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/>
                  </svg>
                ) : (
                  <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/>
                  </svg>
                )}
              </button>
            </div>
          </div>

          {/* Page header */}
          <div className="db-content">
            <div className="db-page-header">
              <h1 className="db-page-title">Cases</h1>
              <div className="db-page-actions">
                <div className="db-search-box">
                  <svg className="search-icon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>
                  </svg>
                  <input
                    className="search-input"
                    placeholder="Search cases..."
                    value={dashboardSearch}
                    onChange={e => setDashboardSearch(e.target.value)}
                  />
                  {dashboardSearch && <button className="cws-search-clear" onClick={() => setDashboardSearch('')}>✕</button>}
                </div>
                <button className="db-new-case-btn" onClick={() => setShowNewCaseModal(true)}>
                  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                    <line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>
                  </svg>
                  New Case
                </button>
              </div>
            </div>

            {/* Stats strip */}
            {totalCases > 0 && (
              <div className="db-stats">
                <span className="db-stat">{totalCases} total</span>
                {inProgress > 0   && <><span className="db-stat-sep">·</span><span className="db-stat in-progress">{inProgress} in progress</span></>}
                {completeCases > 0 && <><span className="db-stat-sep">·</span><span className="db-stat complete">{completeCases} complete</span></>}
                {errorCases > 0   && <><span className="db-stat-sep">·</span><span className="db-stat error">{errorCases} error{errorCases > 1 ? 's' : ''}</span></>}
              </div>
            )}

            {/* Cases grid */}
            {filteredCases.length === 0 ? (
              <div className="db-empty">
                <div className="db-empty-icon">⚖️</div>
                <h2 className="db-empty-title">{cases.length === 0 ? 'No cases yet' : 'No results'}</h2>
                <p className="db-empty-sub">
                  {cases.length === 0
                    ? 'Create a case, attach Petitioner and Respondent documents, then run the pipeline.'
                    : `No cases matching "${dashboardSearch}"`}
                </p>
                {cases.length === 0 && (
                  <button className="db-new-case-btn" onClick={() => setShowNewCaseModal(true)}>+ Create your first case</button>
                )}
              </div>
            ) : (
              <div className="db-cases-grid">
                {filteredCases.map(cas => {
                  const stage = stageNum(cas.status);
                  const isRunning = cas.status.includes('running');
                  const isError = cas.status === 'error';
                  return (
                    <div
                      key={cas.id}
                      className={`db-case-card ${isError ? 'error' : ''}`}
                      onClick={() => handleCaseSelect(cas)}
                    >
                      {/* Card top */}
                      <div className="db-card-top">
                        <span className={`db-card-chip status-${cas.status}`}>
                          {isRunning && <span className="db-card-chip-spinner" />}
                          {caseStatusLabel[cas.status] || cas.status}
                        </span>
                        <button
                          className="db-card-delete"
                          title="Delete case"
                          onClick={e => { e.stopPropagation(); handleDeleteCase(e, cas.id); }}
                        >
                          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                            <polyline points="3 6 5 6 21 6"/>
                            <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/>
                          </svg>
                        </button>
                      </div>

                      {/* Title */}
                      <div className="db-card-title">{cas.title}</div>

                      {/* Documents */}
                      <div className="db-card-docs">
                        <div className="db-card-doc">
                          <span className="db-doc-role pet">P</span>
                          <span className="db-doc-name">{cas.petitioner_doc || <em>Not attached</em>}</span>
                        </div>
                        <div className="db-card-doc">
                          <span className="db-doc-role res">R</span>
                          <span className="db-doc-name">{cas.respondent_doc || <em>Not attached</em>}</span>
                        </div>
                      </div>

                      {/* Progress bar */}
                      <div className="db-card-progress">
                        <div className="db-progress-track">
                          {Array.from({ length: 7 }, (_, i) => (
                            <div
                              key={i}
                              className={`db-progress-seg ${i < stage ? 'done' : ''} ${isRunning && i === stage - 1 ? 'running' : ''} ${isError ? 'error' : ''}`}
                            />
                          ))}
                        </div>
                        <span className="db-progress-label">{isError ? 'Error' : `${stage}/7`}</span>
                      </div>

                      {/* Footer */}
                      <div className="db-card-footer">
                        <span className="db-card-date">{formatDocDate(cas.created_at)}</span>
                        <button
                          className="db-card-open"
                          onClick={e => { e.stopPropagation(); handleCaseSelect(cas); }}
                        >
                          Open →
                        </button>
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        </div>
      )}

      {/* ================================================================
          ATTACH DOCUMENT MODAL
          ================================================================ */}
      {attachModal.open && (
        <div className="attach-modal-backdrop" onClick={() => { setAttachModal({ open: false, role: null }); pendingAutoAttachRef.current = null; setModalUploadState(null); setModalUploadDocId(null); }}>
          <div className="attach-modal" onClick={e => e.stopPropagation()}>

            {/* Header */}
            <div className="attach-modal-header">
              <span className="attach-modal-title">Attach Document — {attachModal.role}</span>
              <button className="attach-modal-close" onClick={() => { setAttachModal({ open: false, role: null }); pendingAutoAttachRef.current = null; setModalUploadState(null); setModalUploadDocId(null); }}>
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                  <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
                </svg>
              </button>
            </div>

            {/* Tabs */}
            <div className="attach-modal-tabs">
              <button className={`attach-tab ${attachModalTab === 'upload' ? 'active' : ''}`} onClick={() => setAttachModalTab('upload')}>Upload</button>
              <button className={`attach-tab ${attachModalTab === 'library' ? 'active' : ''}`} onClick={() => { fetchDocuments(); setAttachModalTab('library'); }}>Library</button>
            </div>

            {/* Upload tab */}
            {attachModalTab === 'upload' && (
              <div className="attach-modal-body">
                {!modalUploadState && (
                  <div
                    className="attach-drop-zone"
                    onDragOver={e => e.preventDefault()}
                    onDrop={e => { e.preventDefault(); const f = e.dataTransfer.files?.[0]; if (f) handleModalUpload(f); }}
                    onClick={() => pdfInputRef.current?.click()}
                  >
                    <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" opacity="0.45">
                      <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
                      <polyline points="17 8 12 3 7 8"/>
                      <line x1="12" y1="3" x2="12" y2="15"/>
                    </svg>
                    <span className="attach-drop-primary">Drop PDF here</span>
                    <span className="attach-drop-secondary">or click to browse</span>
                  </div>
                )}
                {(modalUploadState === 'uploading' || modalUploadState === 'indexing' || modalUploadState === 'attaching') && (
                  <div className="attach-progress">
                    <div className="attach-progress-spinner" />
                    <div className="attach-progress-text">
                      {modalUploadState === 'uploading'  && 'Uploading…'}
                      {modalUploadState === 'indexing'   && 'Building index — this may take a minute…'}
                      {modalUploadState === 'attaching'  && 'Attaching to case…'}
                    </div>
                    {modalUploadState === 'indexing' && (
                      <>
                        <div className="attach-progress-sub">
                          {documents.find(d => d.id === modalUploadDocId)?.original_filename}
                        </div>
                        <button
                          className="attach-retry-btn"
                          onClick={() => {
                            pendingAutoAttachRef.current = null;
                            if (modalUploadDocId) {
                              clearInterval(pollIntervalsRef.current[modalUploadDocId]);
                              delete pollIntervalsRef.current[modalUploadDocId];
                            }
                            setModalUploadState(null);
                            setModalUploadDocId(null);
                          }}
                        >Stop indexing</button>
                      </>
                    )}
                  </div>
                )}
                {modalUploadState === 'done' && (
                  <div className="attach-progress attach-done">
                    <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="#4caf50" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <polyline points="20 6 9 17 4 12"/>
                    </svg>
                    <div className="attach-progress-text">Attached successfully</div>
                  </div>
                )}
                {modalUploadState === 'error' && (
                  <div className="attach-progress attach-error">
                    <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="#f44336" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/>
                    </svg>
                    <div className="attach-progress-text">Upload failed</div>
                    <button className="attach-retry-btn" onClick={() => { setModalUploadState(null); setModalUploadDocId(null); }}>Try again</button>
                  </div>
                )}
              </div>
            )}

            {/* Library tab */}
            {attachModalTab === 'library' && (
              <div className="attach-modal-body attach-library">
                <div className="attach-library-search-wrap">
                  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" opacity="0.5">
                    <circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>
                  </svg>
                  <input
                    className="attach-library-search"
                    placeholder="Search documents…"
                    value={librarySearch}
                    onChange={e => setLibrarySearch(e.target.value)}
                    autoFocus
                  />
                </div>
                <div className="attach-library-list">
                  {(() => {
                    const q = librarySearch.toLowerCase();
                    // Docs attached to the OTHER party cannot be reused
                    const otherPartyDocIds = (activeCaseData?.documents || [])
                      .filter(cd => cd.party_role !== attachModal.role)
                      .map(cd => cd.doc_id);
                    const filtered = documents.filter(d =>
                      d.status === 'done' &&
                      !otherPartyDocIds.includes(d.id) &&
                      (!q || (d.original_filename || '').toLowerCase().includes(q))
                    );
                    if (filtered.length === 0) return (
                      <div className="attach-library-empty">
                        {q ? 'No matching documents' : 'No indexed documents yet'}
                      </div>
                    );
                    return filtered.map(doc => {
                      const attachedAs = (activeCaseData?.documents || []).find(cd => cd.doc_id === doc.id)?.party_role;
                      const isAlreadyThisRole = attachedAs === attachModal.role;
                      const dateStr = doc.uploaded_at ? new Date(doc.uploaded_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric' }) : '';
                      return (
                        <div
                          key={doc.id}
                          className={`attach-library-row ${attachedAs ? 'in-use' : ''} ${isAlreadyThisRole ? 'already-attached' : ''}`}
                          onClick={() => {
                            if (isAlreadyThisRole) return;
                            handleAddCaseDocument(attachModal.role, doc.id, attachModal.role === 'Petitioner' ? 'Petition' : 'Written Statement');
                            setAttachModal({ open: false, role: null });
                          }}
                          title={isAlreadyThisRole ? 'Already attached to this party' : `Attach to ${attachModal.role}`}
                        >
                          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" style={{flexShrink:0}} opacity="0.6">
                            <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
                            <polyline points="14 2 14 8 20 8"/>
                          </svg>
                          <div className="attach-library-info">
                            <span className="attach-library-name">{doc.original_filename}</span>
                            {dateStr && <span className="attach-library-meta">{dateStr}</span>}
                          </div>
                          {attachedAs && (
                            <span className={`attach-library-badge ${isAlreadyThisRole ? 'same' : ''}`}>
                              {isAlreadyThisRole ? attachedAs : attachedAs}
                            </span>
                          )}
                        </div>
                      );
                    });
                  })()}
                </div>
              </div>
            )}
          </div>
        </div>
      )}

      {/* ================================================================
          TRASH PANEL (slide-over)
          ================================================================ */}
      {showTrash && (
        <>
          <div className="trash-backdrop" onClick={() => setShowTrash(false)} />
          <div className="trash-panel">
            <div className="trash-header">
              <div className="trash-header-left">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <polyline points="3 6 5 6 21 6"/>
                  <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/>
                  <path d="M10 11v6"/><path d="M14 11v6"/>
                  <path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/>
                </svg>
                <span className="trash-title">Trash</span>
                {archivedCases.length > 0 && (
                  <span className="trash-count">{archivedCases.length}</span>
                )}
              </div>
              <button className="trash-close" onClick={() => setShowTrash(false)}>✕</button>
            </div>

            {archivedCases.length === 0 ? (
              <div className="trash-empty">
                <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" strokeLinejoin="round" style={{ opacity: 0.25 }}>
                  <polyline points="3 6 5 6 21 6"/>
                  <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/>
                  <path d="M10 11v6"/><path d="M14 11v6"/>
                  <path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/>
                </svg>
                <p>Trash is empty</p>
              </div>
            ) : (
              <div className="trash-list">
                {archivedCases.map(cas => (
                  <div key={cas.id} className="trash-item">
                    <div className="trash-item-info">
                      <span className="trash-item-title">{cas.title}</span>
                      <span className="trash-item-meta">
                        {cas.petitioner_doc || '—'} · {cas.respondent_doc || '—'}
                      </span>
                      <span className="trash-item-date">
                        Deleted {formatDocDate(cas.archived_at || cas.updated_at)}
                      </span>
                    </div>
                    <div className="trash-item-actions">
                      <button
                        className="trash-btn restore"
                        title="Restore"
                        onClick={e => handleRestoreCase(e, cas.id)}
                      >
                        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
                          <polyline points="1 4 1 10 7 10"/>
                          <path d="M3.51 15a9 9 0 1 0 .49-4.95"/>
                        </svg>
                        Restore
                      </button>
                      <button
                        className="trash-btn delete"
                        title="Delete permanently"
                        onClick={e => handlePurgeCase(e, cas.id)}
                      >
                        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
                          <polyline points="3 6 5 6 21 6"/>
                          <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/>
                        </svg>
                        Delete
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </>
      )}

      {/* ================================================================
          CASE DETAIL VIEW (sidebar + panels)
          ================================================================ */}
      {view === 'case' && <>

      {/* ================================================================
          SIDEBAR
          ================================================================ */}
      <div className={`sidebar${sidebarCollapsed ? ' sidebar-collapsed' : ''}`}>

        {activeCaseId && activeCaseData ? (
        /* ============================================================
           CASE WORKSPACE SIDEBAR
           ============================================================ */
        (() => {
          const cStatus = activeCaseData.case.status;
          const cRank   = statusRank(cStatus);
          const caseDocs = activeCaseData.documents || [];

          // ── Agent row renderer ──────────────────────────────────────
          const AgentRow = ({ num, label, isRunning, isDone, canPlay, onPlay, tab, subRow, showDownload }) => {
            const state = isRunning ? 'running' : isDone ? 'done' : canPlay ? 'ready' : 'locked';
            const canRefresh = isDone && onPlay;
            return (
              <>
                <div
                  className={`cws-agent-row ${state} ${activeCaseTab === tab ? 'cws-agent-active' : ''}`}
                  onClick={() => { if (isDone || isRunning) { setActiveCaseTab(tab); setSidebarFocus({ type: 'agent' }); } }}
                >
                  <div className="cws-agent-left">
                    <span className="cws-agent-num">{num}.</span>
                    <span className="cws-agent-label">{label}</span>
                    {isRunning && <span className="cws-agent-spinner" />}
                  </div>
                  <div className="cws-agent-controls">
                    {showDownload && isDone && (
                      <button
                        className="cws-agent-btn download"
                        title="Download order"
                        onClick={e => { e.stopPropagation(); handleDownloadFormalOrder(); }}
                      >
                        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                          <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
                          <polyline points="7 10 12 15 17 10"/>
                          <line x1="12" y1="15" x2="12" y2="3"/>
                        </svg>
                      </button>
                    )}
                    {/* Refresh */}
                    <button
                      className={`cws-agent-btn refresh ${canRefresh ? 'active' : ''}`}
                      disabled={!canRefresh}
                      title="Re-run"
                      onClick={e => { e.stopPropagation(); if (canRefresh) onPlay(); }}
                    >
                      <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                        <path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/>
                        <path d="M3 3v5h5"/>
                      </svg>
                    </button>
                    {/* Stop */}
                    <button
                      className={`cws-agent-btn stop ${isRunning ? 'active' : ''}`}
                      disabled={!isRunning}
                      title="Stop"
                      onClick={e => e.stopPropagation()}
                    >
                      <svg width="8" height="8" viewBox="0 0 24 24" fill="currentColor">
                        <rect x="4" y="4" width="16" height="16" rx="2"/>
                      </svg>
                    </button>
                    {/* Play — prominent green square */}
                    <button
                      className={`cws-agent-btn play-sq ${canPlay ? 'active' : ''}`}
                      disabled={!canPlay && !isRunning}
                      title="Run"
                      onClick={e => { e.stopPropagation(); if (canPlay && onPlay) onPlay(); }}
                    >
                      <svg width="10" height="10" viewBox="0 0 24 24" fill="currentColor">
                        <polygon points="5 3 19 12 5 21 5 3"/>
                      </svg>
                    </button>
                  </div>
                </div>
                {subRow && (
                  <div className="cws-agent-subrow">
                    <span className="cws-agent-subrow-label">↳ {subRow.label}</span>
                    <span className={`cws-agent-subrow-status ${subRow.status}`}>{subRow.text}</span>
                  </div>
                )}
              </>
            );
          };

          // ── Citation Audit sub-row data ─────────────────────────────
          const auditSubRow = (() => {
            const raw = activeCaseData.result?.citation_audit;
            if (!raw) return cRank >= statusRank('verifier_done')
              ? { label: 'Citation Audit', status: 'na', text: 'n/a' }
              : null;
            try {
              const a = JSON.parse(raw);
              const issues = (a.total_not_found || 0) + (a.total_misrepresented || 0);
              return {
                label: 'Citation Audit',
                status: issues > 0 ? 'warn' : 'ok',
                text: issues > 0 ? `${issues} issue${issues > 1 ? 's' : ''}` : `${a.total_found} verified`,
              };
            } catch { return null; }
          })();

          // ── Human review gate ───────────────────────────────────────
          const ReviewGate = () => {
            const gateStatus = cStatus === 'review_approved' ? 'approved'
              : cStatus === 'review_rejected' ? 'rejected'
              : cStatus === 'review_pending' ? 'pending'
              : null;
            if (!gateStatus && cRank < statusRank('review_pending')) return null;
            return (
              <div className={`cws-review-gate ${gateStatus || ''}`}>
                <span className="cws-review-label">Human Review</span>
                {gateStatus === 'pending' && (
                  <div className="cws-review-btns">
                    <button className="cws-review-btn approve" onClick={() => handleReviewMatrix('approve')}>Approve</button>
                    <button className="cws-review-btn reject"  onClick={() => handleReviewMatrix('reject')}>Reject</button>
                  </div>
                )}
                {gateStatus === 'approved' && <span className="cws-review-chip approved">Approved ✓</span>}
                {gateStatus === 'rejected' && <span className="cws-review-chip rejected">Rejected ✗</span>}
              </div>
            );
          };

          return (
            <>
              {/* ── Case header ────────────────────────────────────── */}
              <div className="cws-header">
                <button
                  className="cws-back-btn"
                  title="Back to dashboard"
                  onClick={() => { if (casePollRef.current) { clearInterval(casePollRef.current); casePollRef.current = null; } setView('dashboard'); setActiveCaseId(null); setActiveCaseData(null); }}
                >
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/>
                    <rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>
                  </svg>
                </button>
                <div className="cws-header-mid">
                  <span className="cws-case-title">{activeCaseData.case.title}</span>
                </div>
                <button
                  className="cws-collapse-btn"
                  title={sidebarCollapsed ? 'Expand sidebar' : 'Collapse sidebar'}
                  onClick={() => setSidebarCollapsed(v => !v)}
                >
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                    {sidebarCollapsed
                      ? <polyline points="9 18 15 12 9 6"/>
                      : <polyline points="15 18 9 12 15 6"/>}
                  </svg>
                </button>
              </div>

              {/* ── DOCUMENTS section ────────────────────────────── */}
              <div className="cws-docs-area">
              <div className="cws-section-header" onClick={() => setDocsExpanded(v => !v)}>
                <span className="cws-section-title">DOCUMENTS</span>
                <span className={`cws-section-chevron ${docsExpanded ? 'expanded' : ''}`}>
                  <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                    <polyline points="18 15 12 9 6 15"/>
                  </svg>
                </span>
              </div>

              {docsExpanded && (
                <div className="cws-docs">
                  {['Petitioner', 'Respondent'].map(role => {
                    const partyName = role === 'Petitioner'
                      ? (activeCaseData.case.petitioner_name || 'Petitioner')
                      : (activeCaseData.case.respondent_name || 'Respondent');
                    const roleDocs = caseDocs.filter(d => d.party_role === role);
                    const isEditingName = editingPartyRole === role;

                    const openAttach = () => {
                      fetchDocuments();
                      setAttachModalTab('upload');
                      setModalUploadState(null);
                      setModalUploadDocId(null);
                      setAttachModal({ open: true, role });
                    };

                    return (
                      <div key={role} className="cws-party-group">

                        {/* Party header — fused "ROLE – NAME" label */}
                        <div className="cws-party-header">
                          {isEditingName ? (
                            <input
                              className="cws-party-name-input"
                              value={editingPartyNameValue}
                              onChange={e => setEditingPartyNameValue(e.target.value)}
                              onBlur={() => handleUpdatePartyName(role, editingPartyNameValue)}
                              onKeyDown={e => {
                                if (e.key === 'Enter') handleUpdatePartyName(role, editingPartyNameValue);
                                if (e.key === 'Escape') setEditingPartyRole(null);
                              }}
                              autoFocus
                            />
                          ) : (
                            <span className="cws-party-label">
                              {role.toUpperCase()} – {partyName.toUpperCase()}
                            </span>
                          )}
                          <div className="cws-party-actions">
                            <button className="cws-party-icon-btn" title="Rename party"
                              onClick={() => { setEditingPartyRole(role); setEditingPartyNameValue(partyName); }}>
                              <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
                                <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/>
                                <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/>
                              </svg>
                            </button>
                            <button className="cws-party-icon-btn" title="Add document" onClick={openAttach}>
                              <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                                <line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>
                              </svg>
                            </button>
                          </div>
                        </div>

                        {/* Document cards */}
                        {roleDocs.map(caseDoc => {
                          const pageCount    = caseDoc.page_count;
                          const uploadedAt   = caseDoc.uploaded_at;
                          const indexStatus  = caseDoc.doc_status || 'pending';
                          const clerkStatus  = caseDoc.clerk_status || 'pending';
                          const verifStatus  = caseDoc.verifier_status || 'pending';
                          const dateStr = uploadedAt
                            ? new Date(uploadedAt).toLocaleDateString('en-GB', { day:'2-digit', month:'2-digit', year:'numeric' })
                            : '';
                          const timeStr = uploadedAt
                            ? new Date(uploadedAt).toLocaleTimeString('en-US', { hour:'numeric', minute:'2-digit', hour12:true }).toLowerCase()
                            : '';
                          return (
                            <div
                              key={caseDoc.id}
                              className={`cws-doc-card ${casePdfParty === role.toLowerCase() ? 'active' : ''}`}
                              onClick={() => { setCasePdfParty(role.toLowerCase()); setSidebarFocus({ type: 'doc' }); }}
                            >
                              <div className="cws-doc-card-body">
                                <div className="cws-doc-card-title">{caseDoc.original_filename || 'Document'}</div>
                                <div className="cws-doc-card-meta">
                                  {[pageCount ? `${pageCount} pages` : null, dateStr, timeStr].filter(Boolean).join(' · ')}
                                </div>
                              </div>
                              <div className="cws-doc-card-right">
                                <div className="cws-doc-dots">
                                  <span className={`cws-dot ${indexStatus}`} title={`Index: ${indexStatus}`} />
                                  <span className={`cws-dot ${clerkStatus}`} title={`Clerk: ${clerkStatus}`} />
                                  <span className={`cws-dot ${verifStatus}`} title={`Verify: ${verifStatus}`} />
                                </div>
                                <button className="cws-doc-detach" title="Remove"
                                  onClick={e => { e.stopPropagation(); handleDetachCaseDoc(caseDoc.id); }}>✕</button>
                              </div>
                            </div>
                          );
                        })}

                      </div>
                    );
                  })}
                </div>
              )}
              </div>{/* end cws-docs-area */}

              {/* ── AGENTS section — pinned to bottom, opens upward ── */}
              <div className="cws-agents-bottom">
                {caseAgentsPanelExpanded && (
                  <div className="cws-agents">
                    <AgentRow num={1} label="CLERK AGENT"
                      isRunning={cStatus === 'clerk_running'}
                      isDone={cRank >= statusRank('verifier_done')}
                      canPlay={cStatus === 'pending'}
                      onPlay={handleRunClerk}
                      tab="clerk"
                    />
                    <AgentRow num={2} label="VERIFY"
                      isRunning={cStatus === 'verifier_running'}
                      isDone={cRank >= statusRank('verifier_done')}
                      canPlay={false}
                      tab="verifier"
                      subRow={auditSubRow}
                    />
                    <AgentRow num={3} label="REGISTRAR AGENT"
                      isRunning={cStatus === 'registrar_running'}
                      isDone={cRank >= statusRank('registrar_done')}
                      canPlay={cStatus === 'verifier_done'}
                      onPlay={handleRunRegistrar}
                      tab="registrar"
                    />
                    <AgentRow num={4} label="PROCEDURE"
                      isRunning={cStatus === 'procedural_running'}
                      isDone={cRank >= statusRank('procedural_done')}
                      canPlay={cStatus === 'registrar_done'}
                      onPlay={handleRunProcedural}
                      tab="procedural"
                    />
                    <AgentRow num={5} label="STRESS AGENT"
                      isRunning={cStatus === 'devils_advocate_running'}
                      isDone={cRank >= statusRank('review_pending')}
                      canPlay={cStatus === 'procedural_done'}
                      onPlay={handleRunDevilsAdvocate}
                      tab="devils_advocate"
                    />
                    <ReviewGate />
                    <AgentRow num={6} label="JUDGE AGENT"
                      isRunning={cStatus === 'judge_running'}
                      isDone={cRank >= statusRank('judge_done')}
                      canPlay={cStatus === 'review_approved'}
                      onPlay={handleRunJudge}
                      tab="judge"
                    />
                    <AgentRow num={7} label="DRAFTER"
                      isRunning={cStatus === 'drafter_running'}
                      isDone={cStatus === 'complete'}
                      canPlay={cStatus === 'judge_done'}
                      onPlay={handleRunDrafter}
                      tab="drafter"
                      showDownload={true}
                    />
                  </div>
                )}
                <div className="cws-section-header cws-agents-header" onClick={() => setCaseAgentsPanelExpanded(v => !v)}>
                  <span className="cws-section-title">AGENTS</span>
                  <span className={`cws-section-chevron ${caseAgentsPanelExpanded ? 'expanded' : ''}`}>
                    <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                      <polyline points="18 15 12 9 6 15"/>
                    </svg>
                  </span>
                </div>
              </div>
            </>
          );
        })()
        ) : null}

      </div>

      {/* ================================================================
          MIDDLE PANEL — PDF Viewer
          ================================================================ */}
      <div className="pdf-panel">
        <div className="panel-header">
          {activeCaseId && (
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
                onLoadSuccess={({ numPages: n }) => {
                  pdfPageRefs.current = {};
                  setNumPages(n);
                }}
                loading={<div className="pdf-loading"><div className="spinner" /><p>Loading PDF…</p></div>}
                error={<div className="pdf-error">Failed to load PDF</div>}
              >
                {numPages && Array.from({ length: numPages }, (_, i) => (
                  <div
                    key={i + 1}
                    className={`pdf-page-wrapper${highlightedPage === i + 1 ? ' pdf-page-highlighted' : ''}`}
                    data-page-num={i + 1}
                    ref={el => { pdfPageRefs.current[i + 1] = el; }}
                  >
                    <Page
                      pageNumber={i + 1}
                      width={pdfWidth || undefined}
                      renderTextLayer={true}
                      renderAnnotationLayer={true}
                    />
                  </div>
                ))}
              </Document>

              {numPages && (
                <div className="pdf-page-counter">
                  {pdfPage} / {numPages}
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
          CONTENT PANEL — Mode A: tree+notes  /  Mode B: agent output
          ================================================================ */}
      <div className="content-panel">
        {activeCaseId && activeCaseData && (() => {
          const cStatus = activeCaseData.case.status;
          const petName = activeCaseData.case.petitioner_name || 'Petitioner';
          const resName = activeCaseData.case.respondent_name || 'Respondent';
          const isDocMode = sidebarFocus.type === 'doc';

          return (
            <>
              {/* ── Content panel header ── */}
              <div className="cp-header">
                {isDocMode ? (
                  <div className="cp-doc-tabs">
                    <button className={`cp-doc-tab ${docViewMode === 'index' ? 'active' : ''}`} onClick={() => setDocViewMode('index')}>INDEX</button>
                    <button className={`cp-doc-tab ${docViewMode === 'notes' ? 'active' : ''}`} onClick={() => setDocViewMode('notes')}>NOTES</button>
                  </div>
                ) : (
                  <div className="cp-mode-badge">{activeCaseTab.replace(/_/g, ' ').toUpperCase()}</div>
                )}
                <div className="cp-party-tabs">
                  {['petitioner', 'respondent'].map(party => {
                    const name = party === 'petitioner' ? petName : resName;
                    const hasDoc = activeCaseData.documents?.some(d => d.party_role.toLowerCase() === party);
                    return (
                      <button key={party}
                        className={`cp-party-tab ${casePdfParty === party ? 'active' : ''} ${!hasDoc ? 'no-doc' : ''}`}
                        onClick={() => { if (hasDoc) setCasePdfParty(party); }}
                        title={`View ${name}'s document`}
                      >{name}</button>
                    );
                  })}
                </div>
              </div>

              {/* ── Content panel body ── */}
              <div className="cp-body">
                {isDocMode ? (
                  /* ── Mode A: INDEX or NOTES ── */
                  !viewDocId ? (
                    <div className="doc-col-empty">
                      <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" opacity="0.3">
                        <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
                        <polyline points="14 2 14 8 20 8"/>
                      </svg>
                      <span>No document attached</span>
                    </div>
                  ) : !activeDocDetails ? (
                    <div className="doc-col-loading"><span className="spinner-sm" /> Loading…</div>
                  ) : docViewMode === 'index' ? (
                    /* ── INDEX tab: tree ── */
                    !activeDocDetails.tree ? (
                      <div className="doc-col-empty"><span>Document not yet indexed</span></div>
                    ) : (
                      <div className="cp-tree">
                        {(activeDocDetails.tree.structure || []).map((node, i) => (
                          <TreeNode
                            key={i} node={node} level={0}
                            onNodeClick={scrollToPage}
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
                    )
                  ) : (
                    /* ── NOTES tab ── */
                    (() => {
                      const humanNotes = annotations.filter(a => (a.source || 'human') === 'human');
                      const agentNotes = annotations.filter(a => a.source === 'agent');
                      const filtered = annotations.filter(a => {
                        if (notesFilter === 'human') return (a.source || 'human') === 'human';
                        if (notesFilter === 'agent') return a.source === 'agent';
                        if (notesFilter === 'flag')  return a.note_type === 'flag';
                        return true;
                      }).sort((a, b) => (a.anchor_page || 0) - (b.anchor_page || 0));

                      const noteTypeBadge = (type, severity) => {
                        const labels = { summary: 'Summary', flag: 'Flag', quote: 'Quote', cross_ref: 'Cross-ref' };
                        const cls = `notes-type-badge ${type || 'human'}${severity ? ` sev-${severity}` : ''}`;
                        return <span className={cls}>{labels[type] || 'Note'}{severity ? ` ·${severity[0].toUpperCase()}` : ''}</span>;
                      };

                      return (
                        <div className="notes-view">
                          {/* Filter + actions strip */}
                          <div className="notes-strip">
                            <div className="notes-filters">
                              {[['all','All'],['human','Human'],['agent','Agent'],['flag','Flags']].map(([v,l]) => (
                                <button key={v} className={`notes-filter-btn ${notesFilter===v?'active':''}`} onClick={() => setNotesFilter(v)}>
                                  {l}{v==='agent'&&agentNotes.length>0?` ${agentNotes.length}`:''}{v==='human'&&humanNotes.length>0?` ${humanNotes.length}`:''}
                                </button>
                              ))}
                            </div>
                            <button className="notes-add-btn" onClick={() => { setNotesAddOpen(v => !v); setNotesAddPage(String(pdfPage||1)); setNotesAddBody(''); }} title="Add note">
                              <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                                <line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>
                              </svg>
                            </button>
                          </div>

                          {/* Inline add-note form */}
                          {notesAddOpen && (
                            <div className="notes-add-form">
                              <div className="notes-add-row">
                                <span className="notes-add-label">Page</span>
                                <input className="notes-add-page-input" type="number" min="1" value={notesAddPage} onChange={e => setNotesAddPage(e.target.value)} />
                              </div>
                              <textarea className="notes-add-textarea" rows={3} placeholder="Write a note…" value={notesAddBody} onChange={e => setNotesAddBody(e.target.value)} autoFocus />
                              <div className="notes-add-actions">
                                <button className="notes-add-save" onClick={handleAddNote} disabled={!notesAddBody.trim()}>Save</button>
                                <button className="notes-add-cancel" onClick={() => setNotesAddOpen(false)}>Cancel</button>
                              </div>
                            </div>
                          )}

                          {/* Empty state */}
                          {annotations.length === 0 && !notesGenerating && (
                            <div className="notes-empty">
                              <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round" strokeLinejoin="round" opacity="0.3">
                                <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
                                <polyline points="14 2 14 8 20 8"/>
                                <line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/>
                              </svg>
                              <p>No notes yet</p>
                              {activeDocDetails?.tree && (
                                <button className="notes-generate-btn" onClick={handleGenerateNotes}>
                                  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
                                    <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/>
                                  </svg>
                                  Generate Notes with AI
                                </button>
                              )}
                            </div>
                          )}

                          {/* Generating spinner */}
                          {notesGenerating && (
                            <div className="notes-generating">
                              <span className="spinner-sm" />
                              <span>Note Builder Agent running…</span>
                            </div>
                          )}

                          {/* Note cards */}
                          {!notesGenerating && filtered.length > 0 && (
                            <div className="notes-list">
                              {/* Re-generate button (when agent notes exist) */}
                              {agentNotes.length > 0 && (
                                <button className="notes-regen-btn" onClick={handleGenerateNotes} title="Re-generate AI notes">
                                  <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                                    <path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/>
                                    <path d="M3 3v5h5"/>
                                  </svg>
                                  Re-generate
                                </button>
                              )}
                              {filtered.map(note => (
                                <div key={note.id} className={`notes-card ${note.note_type || 'human'} ${note.severity ? `sev-${note.severity}` : ''}`}>
                                  <div className="notes-card-header">
                                    <button className="notes-card-page" onClick={() => scrollToPage(note.anchor_page)} title={`Go to page ${note.anchor_page}`}>
                                      p.{note.anchor_page}
                                    </button>
                                    {note.anchor_title && <span className="notes-card-section">{note.anchor_title}</span>}
                                    <div className="notes-card-badges">
                                      {noteTypeBadge(note.note_type, note.severity)}
                                      <span className={`notes-source-badge ${note.source || 'human'}`}>{note.source === 'agent' ? 'AI' : '✏'}</span>
                                    </div>
                                  </div>
                                  <div className="notes-card-body">{note.body}</div>
                                  <div className="notes-card-actions">
                                    <button className="notes-card-delete" title="Delete note"
                                      onClick={async () => {
                                        await fetch(`${API}/api/annotations/${note.id}`, { method: 'DELETE' }).catch(() => {});
                                        setAnnotations(prev => prev.filter(a => a.id !== note.id));
                                      }}>
                                      <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
                                        <polyline points="3 6 5 6 21 6"/>
                                        <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/>
                                      </svg>
                                    </button>
                                  </div>
                                </div>
                              ))}
                            </div>
                          )}

                          {/* Has notes but filter is empty */}
                          {!notesGenerating && annotations.length > 0 && filtered.length === 0 && (
                            <div className="notes-empty"><p>No {notesFilter} notes</p></div>
                          )}

                          {/* Generate button when only human notes exist */}
                          {!notesGenerating && agentNotes.length === 0 && humanNotes.length > 0 && activeDocDetails?.tree && (
                            <div className="notes-generate-row">
                              <button className="notes-generate-btn" onClick={handleGenerateNotes}>
                                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
                                  <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/>
                                </svg>
                                Generate AI Notes
                              </button>
                            </div>
                          )}
                        </div>
                      );
                    })()
                  )
                ) : (
                  /* ── Mode B: agent output ── */
                  <div className="cp-agent-output">

                    {/* ─── Setup Tab ─── */}
                    {activeCaseTab === 'setup' && (() => {
                      const petDoc = activeCaseData.documents?.find(d => d.party_role === 'Petitioner');
                      const resDoc = activeCaseData.documents?.find(d => d.party_role === 'Respondent');
                      const canRunClerk = petDoc && resDoc && statusRank(cStatus) < statusRank('clerk_running');
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
                                  <span
                                    className="clerk-page-tag clickable"
                                    onClick={() => scrollToPage(f.page_index)}
                                  >p.{f.page_index}</span>
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
                                  {c.page_index && <span className="clerk-page-tag clickable" onClick={() => scrollToPage(c.page_index)}>p.{c.page_index}</span>}
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
                      const canRunVerifier = cStatus === 'clerk_done';
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
                          {canRunVerifier && (
                            <div className="case-setup-actions">
                              <button className="case-run-btn" onClick={handleRunVerifier}>
                                ▶ Run Verifier Agent
                              </button>
                            </div>
                          )}
                          {cStatus === 'verifier_running' && (
                            <div className="case-running-banner"><span className="spinner-sm" /> Verifier is auditing submissions…</div>
                          )}
                        </div>
                      );
                    })()}

                    {/* ─── Verifier Tab ─── */}
                    {activeCaseTab === 'verifier' && (() => {
                      const docs = activeCaseData.documents || [];
                      const petDoc = docs.find(d => d.party_role === 'Petitioner');
                      const resDoc = docs.find(d => d.party_role === 'Respondent');
                      const canRunRegistrar = cStatus === 'verifier_done';
                      const renderAudit = (cd) => {
                        if (!cd) return <div className="case-clerk-empty">No document attached.</div>;
                        if (!cd.verifier_output) return <div className="case-clerk-empty">{cd.verifier_status === 'running' ? <><span className="spinner-sm" /> Auditing…</> : 'Not yet verified.'}</div>;
                        let audit;
                        try { audit = JSON.parse(cd.verifier_output); } catch { return <div className="case-clerk-empty">Invalid output.</div>; }
                        const confColor = audit.overall_confidence >= 0.85 ? '#4caf50' : audit.overall_confidence >= 0.65 ? '#ff9800' : '#f44336';
                        return (
                          <div className="clerk-output">
                            <div className="verifier-confidence" style={{ color: confColor }}>
                              Confidence: {Math.round(audit.overall_confidence * 100)}%
                            </div>
                            {audit.flags?.length > 0 && (
                              <div className="clerk-section">
                                <div className="clerk-section-label">Flags ({audit.flags.length})</div>
                                {audit.flags.map((f, i) => (
                                  <div key={i} className={`verifier-flag ${f.severity}`}>
                                    <span className="verifier-flag-type">{f.flag_type}</span>
                                    <span className="verifier-flag-field">{f.affected_field}</span>
                                    <div className="verifier-flag-desc">{f.description}</div>
                                  </div>
                                ))}
                              </div>
                            )}
                            <div className="clerk-section">
                              <div className="clerk-section-label">Citation Audit ({audit.citation_audit?.length || 0})</div>
                              {(audit.citation_audit || []).map((c, i) => (
                                <div key={i} className={`verifier-citation ${c.found_in_page_text ? 'found' : 'missing'}`}>
                                  <span className="verifier-citation-status">{c.found_in_page_text ? '✓' : '✗'}</span>
                                  {c.citation}
                                </div>
                              ))}
                            </div>
                            {audit.internal_contradictions?.length > 0 && (
                              <div className="clerk-section">
                                <div className="clerk-section-label">Contradictions ({audit.internal_contradictions.length})</div>
                                {audit.internal_contradictions.map((c, i) => (
                                  <div key={i} className="verifier-contradiction">{c}</div>
                                ))}
                              </div>
                            )}
                          </div>
                        );
                      };
                      const citationAuditData = (() => {
                        const raw = activeCaseData.result?.citation_audit;
                        if (!raw) return null;
                        try { return JSON.parse(raw); } catch { return null; }
                      })();
                      return (
                        <div className="clerk-panel">
                          <div className="clerk-columns">
                            <div className="clerk-column">
                              <div className="clerk-column-header petitioner">Petitioner</div>
                              {renderAudit(petDoc)}
                            </div>
                            <div className="clerk-column">
                              <div className="clerk-column-header respondent">Respondent</div>
                              {renderAudit(resDoc)}
                            </div>
                          </div>

                          {/* ─── Citation Audit (Stage 2.5) ─── */}
                          {citationAuditData && (
                            <div className="citation-audit-panel">
                              <div className="citation-audit-header">
                                <span className="citation-audit-title">Citation Audit</span>
                                <span className="citation-audit-meta">
                                  {citationAuditData.total_case_citations} citations checked
                                  {citationAuditData.total_not_found > 0 && <span className="ca-badge not-found">{citationAuditData.total_not_found} not found</span>}
                                  {citationAuditData.total_misrepresented > 0 && <span className="ca-badge misrepresented">{citationAuditData.total_misrepresented} misrepresented</span>}
                                  {citationAuditData.total_unverified > 0 && <span className="ca-badge unverified">{citationAuditData.total_unverified} unverified</span>}
                                  {!citationAuditData.indian_kanoon_available && <span className="ca-badge unverified">Indian Kanoon unavailable</span>}
                                </span>
                              </div>
                              <div className="citation-audit-results">
                                {(citationAuditData.results || []).filter(r => r.is_case_citation).map((r, i) => {
                                  let status = 'verified';
                                  if (!r.found) status = 'not-found';
                                  else if (r.claimed_holding_matches === false) status = 'misrepresented';
                                  else if (r.verification_method === 'unverified') status = 'unverified';
                                  const statusLabel = { 'verified': 'VERIFIED', 'not-found': 'NOT FOUND', 'misrepresented': 'MISREPRESENTED', 'unverified': 'UNVERIFIED' }[status];
                                  return (
                                    <div key={i} className={`ca-result ${status}`}>
                                      <div className="ca-result-top">
                                        <span className={`ca-status-badge ${status}`}>{statusLabel}</span>
                                        <span className="ca-party-badge">{r.party_role}</span>
                                        <span className="ca-citation">{r.citation}</span>
                                      </div>
                                      {r.case_title && <div className="ca-case-title">{r.case_title}{r.court ? ` — ${r.court}` : ''}{r.decision_date ? ` (${r.decision_date})` : ''}</div>}
                                      {r.actual_holding && <div className="ca-holding"><span className="ca-holding-label">Actual holding:</span> {r.actual_holding}</div>}
                                      {r.discrepancy_note && <div className="ca-discrepancy"><span className="ca-discrepancy-label">⚠ Discrepancy:</span> {r.discrepancy_note}</div>}
                                      {r.note && <div className="ca-note">{r.note}</div>}
                                      {r.source_url && <a className="ca-source-link" href={r.source_url} target="_blank" rel="noreferrer">View on Indian Kanoon ↗</a>}
                                    </div>
                                  );
                                })}
                              </div>
                            </div>
                          )}

                          {canRunRegistrar && (
                            <div className="case-setup-actions">
                              <button className="case-run-btn" onClick={handleRunRegistrar}>▶ Run Registrar Agent</button>
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
                      const result = activeCaseData.result;
                      if (!result?.adversarial_matrix) {
                        return (
                          <div className="empty-state">
                            <div className="empty-icon">⚖️</div>
                            <h2>Registrar not run yet</h2>
                            <p>Complete the Verifier stage first, then run the Registrar Agent.</p>
                          </div>
                        );
                      }
                      let matrix;
                      try { matrix = JSON.parse(result.adversarial_matrix); } catch {
                        return <div className="case-error-msg">Failed to parse matrix.</div>;
                      }
                      const canRunProcedural = cStatus === 'registrar_done';
                      return (
                        <div className="registrar-panel">
                          <div className="registrar-undisputed">
                            <div className="registrar-section-label">Undisputed Background ({matrix.undisputed_background?.length || 0} facts)</div>
                            {(matrix.undisputed_background || []).map((f, i) => (
                              <div key={i} className="registrar-fact">{f}</div>
                            ))}
                          </div>
                          <div className="registrar-issues-label">Framed Issues ({matrix.framed_issues?.length || 0})</div>
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
                          {canRunProcedural && (
                            <div className="case-setup-actions">
                              <button className="case-run-btn" onClick={handleRunProcedural}>▶ Run Procedural Agent</button>
                            </div>
                          )}
                          {cStatus === 'procedural_running' && (
                            <div className="case-running-banner"><span className="spinner-sm" /> Procedural Agent is sifting issues…</div>
                          )}
                        </div>
                      );
                    })()}

                    {/* ─── Procedural Tab ─── */}
                    {activeCaseTab === 'procedural' && (() => {
                      const result = activeCaseData.result;
                      if (!result?.sifted_matrix) {
                        return (
                          <div className="empty-state">
                            <div className="empty-icon">🔍</div>
                            <h2>Procedural Agent not run yet</h2>
                            <p>Complete the Registrar stage first.</p>
                          </div>
                        );
                      }
                      let sifted;
                      try { sifted = JSON.parse(result.sifted_matrix); } catch {
                        return <div className="case-error-msg">Failed to parse sifted matrix.</div>;
                      }
                      const pa = sifted.procedural_analysis;
                      const canRunDA = cStatus === 'procedural_done';
                      const findingColor = (f) => f === 'maintainable' || f === 'within_time' || f === 'established' ? '#4caf50' : f === 'unclear' ? '#ff9800' : '#f44336';
                      return (
                        <div className="registrar-panel">
                          <div className="procedural-overview">
                            {[
                              { label: 'Jurisdiction', value: pa.jurisdiction_finding, note: pa.jurisdiction_reasoning },
                              { label: 'Limitation', value: pa.limitation_finding, note: pa.limitation_reasoning },
                              { label: 'Standing', value: pa.standing_finding, note: pa.standing_reasoning },
                            ].map(({ label, value, note }) => (
                              <div key={label} className="procedural-finding">
                                <div className="procedural-finding-header">
                                  <span className="procedural-finding-label">{label}</span>
                                  <span className="procedural-finding-value" style={{ color: findingColor(value) }}>{value.replace(/_/g, ' ')}</span>
                                </div>
                                <div className="procedural-finding-note">{note}</div>
                              </div>
                            ))}
                          </div>
                          <div className="registrar-issues-label">
                            Issues to Proceed ({pa.issues_to_proceed?.length || 0}) · Flagged ({pa.issues_flagged?.length || 0})
                          </div>
                          {(pa.issue_flags || []).map((flag) => (
                            <div key={flag.issue_id} className={`procedural-issue-flag ${flag.severity}`}>
                              <div className="procedural-flag-header">
                                <span className="issue-id-badge">{flag.issue_id}</span>
                                <span className={`procedural-bar-tag ${flag.procedural_bar}`}>{flag.procedural_bar}</span>
                                <span className={`procedural-rec-tag ${flag.recommendation}`}>{flag.recommendation}</span>
                                <span className={`procedural-sev-tag ${flag.severity}`}>{flag.severity}</span>
                              </div>
                              <div className="procedural-flag-reasoning">{flag.reasoning}</div>
                            </div>
                          ))}
                          {pa.issue_flags?.length === 0 && (
                            <div className="registrar-fact" style={{ color: '#4caf50' }}>✓ All issues cleared — no procedural bars found.</div>
                          )}
                          {canRunDA && (
                            <div className="case-setup-actions">
                              <button className="case-run-btn" onClick={handleRunDevilsAdvocate}>▶ Run Devil's Advocate</button>
                            </div>
                          )}
                          {cStatus === 'devils_advocate_running' && (
                            <div className="case-running-banner"><span className="spinner-sm" /> Devil's Advocate is stress-testing…</div>
                          )}
                        </div>
                      );
                    })()}

                    {/* ─── Devil's Advocate Tab ─── */}
                    {activeCaseTab === 'devils_advocate' && (() => {
                      const result = activeCaseData.result;
                      if (!result?.stress_tested_matrix) {
                        return (
                          <div className="empty-state">
                            <div className="empty-icon">😈</div>
                            <h2>Devil's Advocate not run yet</h2>
                            <p>Complete the Procedural stage first.</p>
                          </div>
                        );
                      }
                      let stData;
                      try { stData = JSON.parse(result.stress_tested_matrix); } catch {
                        return <div className="case-error-msg">Failed to parse stress test matrix.</div>;
                      }
                      const st = stData.stress_tested_matrix;
                      const reviewStatus = result.human_review_status;
                      const balanceColor = (b) => b === 'petitioner_stronger' ? '#5b9cf6' : b === 'respondent_stronger' ? '#f67b5b' : b === 'balanced' ? '#ff9800' : '#888';
                      return (
                        <div className="registrar-panel">
                          {st.reviewer_note && (
                            <div className="devils-reviewer-note">
                              <div className="registrar-section-label">Reviewer Note</div>
                              <div className="devils-note-text">{st.reviewer_note}</div>
                            </div>
                          )}
                          <div className="registrar-issues-label">
                            Stress Tests ({st.stress_tests?.length || 0})
                          </div>
                          {(st.stress_tests || []).map((test) => (
                            <details key={test.issue_id} className="registrar-issue">
                              <summary className="registrar-issue-summary">
                                <span className="issue-id-badge">{test.issue_id}</span>
                                <span className="balance-badge" style={{ color: balanceColor(test.balance_assessment) }}>
                                  {test.balance_assessment.replace(/_/g, ' ')}
                                </span>
                              </summary>
                              <div className="registrar-issue-body">
                                {[
                                  { party: 'Petitioner', vuln: test.petitioner_vulnerability },
                                  { party: 'Respondent', vuln: test.respondent_vulnerability },
                                ].map(({ party, vuln }) => (
                                  <div key={party} className={`stance-block ${party.toLowerCase()}`}>
                                    <div className="stance-label">{party} — Vulnerability</div>
                                    <div className={`verifier-flag-type severity-${vuln.severity}`}>{vuln.weakness_type} · {vuln.severity}</div>
                                    <div className="stance-arg">{vuln.strongest_counter}</div>
                                    {vuln.suggested_reframe && (
                                      <div className="stance-citation">↻ Reframe: {vuln.suggested_reframe}</div>
                                    )}
                                  </div>
                                ))}
                              </div>
                            </details>
                          ))}
                          <div className="review-gate">
                            {reviewStatus === 'pending' && (
                              <>
                                <div className="review-gate-label">Human Review Required — approve matrix before Judge runs</div>
                                <div className="review-gate-actions">
                                  <button className="review-approve-btn" onClick={() => handleReviewMatrix('approve')}>✓ Approve Matrix</button>
                                  <button className="review-reject-btn" onClick={() => handleReviewMatrix('reject')}>✗ Reject</button>
                                </div>
                              </>
                            )}
                            {reviewStatus === 'approved' && statusRank(cStatus) < statusRank('judge_running') && (
                              <div className="case-setup-actions">
                                <button className="case-run-btn approved" onClick={handleRunJudge}>▶ Run Judge Agent</button>
                              </div>
                            )}
                            {reviewStatus === 'approved' && cStatus === 'judge_running' && (
                              <div className="case-running-banner"><span className="spinner-sm" /> Judge is deliberating…</div>
                            )}
                            {reviewStatus === 'rejected' && (
                              <div className="case-error-msg">Matrix rejected — re-run Registrar to rebuild.</div>
                            )}
                            {reviewStatus === 'approved' && statusRank(cStatus) >= statusRank('judge_done') && (
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
                            <p>Approve the matrix in the Stress Test tab, then run the Judge Agent.</p>
                          </div>
                        );
                      }
                      let order;
                      try { order = JSON.parse(result.draft_court_order); } catch {
                        return <div className="case-error-msg">Failed to parse court order.</div>;
                      }
                      const canRunDrafter = cStatus === 'judge_done';
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
                          {canRunDrafter && (
                            <div className="case-setup-actions">
                              <button className="case-run-btn" onClick={handleRunDrafter}>▶ Run Drafting Agent</button>
                            </div>
                          )}
                          {cStatus === 'drafter_running' && (
                            <div className="case-running-banner"><span className="spinner-sm" /> Drafting Agent is formatting the order…</div>
                          )}
                        </div>
                      );
                    })()}

                    {/* ─── Drafter Tab ─── */}
                    {activeCaseTab === 'drafter' && (() => {
                      const result = activeCaseData.result;
                      if (!result?.formal_court_order) {
                        return (
                          <div className="empty-state">
                            <div className="empty-icon">📜</div>
                            <h2>Drafting Agent not run yet</h2>
                            <p>Complete the Judge stage first, then run the Drafting Agent.</p>
                          </div>
                        );
                      }
                      let fo;
                      try { fo = JSON.parse(result.formal_court_order); } catch {
                        return <div className="case-error-msg">Failed to parse formal order.</div>;
                      }
                      const handleExportFormal = () => {
                        const text = [fo.cause_title, '', fo.coram, fo.date, '',
                          fo.petitioner_counsel ? `For Petitioner: ${fo.petitioner_counsel}` : '',
                          fo.respondent_counsel ? `For Respondent: ${fo.respondent_counsel}` : '',
                          '', fo.body, '', fo.signature_block].join('\n');
                        const blob = new Blob([text], { type: 'text/plain' });
                        const url = URL.createObjectURL(blob);
                        const a = document.createElement('a');
                        a.href = url; a.download = 'formal_court_order.txt';
                        document.body.appendChild(a); a.click();
                        document.body.removeChild(a); URL.revokeObjectURL(url);
                      };
                      return (
                        <div className="judge-panel">
                          <div className="judge-header">
                            <div className="judge-case-title">Formal Court Order</div>
                            <button className="panel-action-btn" onClick={handleExportFormal} title="Export as text">
                              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                                <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
                                <polyline points="7 10 12 15 17 10"/>
                                <line x1="12" y1="15" x2="12" y2="3"/>
                              </svg>
                            </button>
                          </div>
                          <div className="drafter-cause-block">
                            <div className="drafter-cause-title">{fo.cause_title}</div>
                            <div className="drafter-coram">{fo.coram}</div>
                            <div className="drafter-date">{fo.date}</div>
                            {(fo.petitioner_counsel || fo.respondent_counsel) && (
                              <div className="drafter-appearances">
                                {fo.petitioner_counsel && <div>For Petitioner: {fo.petitioner_counsel}</div>}
                                {fo.respondent_counsel && <div>For Respondent: {fo.respondent_counsel}</div>}
                              </div>
                            )}
                          </div>
                          <div className="judge-background">
                            <div className="judge-section-label">Order Body</div>
                            <div className="drafter-body-text">{fo.body}</div>
                          </div>
                          <div className="judge-final-order">
                            <div className="judge-section-label">Operative Portion</div>
                            <div className="drafter-operative-text">{fo.operative_portion}</div>
                          </div>
                          <div className="drafter-signature">{fo.signature_block}</div>
                        </div>
                      );
                    })()}

                  </div>
                )}
              </div>
            </>
          );
        })()}
      </div>

      </> /* end view === 'case' */}

      {/* ================================================================
          NEW CASE MODAL
          ================================================================ */}
      {showNewCaseModal && (
        <div className="modal-overlay" onClick={() => { setShowNewCaseModal(false); setNewCaseTitle(''); }}>
          <div className="modal-box" onClick={e => e.stopPropagation()}>
            <div className="modal-header">
              <span className="modal-title">New Case</span>
              <button className="modal-close" onClick={() => { setShowNewCaseModal(false); setNewCaseTitle(''); }}>✕</button>
            </div>
            <div className="modal-body">
              <label className="modal-label">Case title</label>
              <input
                className="modal-input"
                placeholder="e.g. Smith v Jones, State v Kumar"
                value={newCaseTitle}
                onChange={e => setNewCaseTitle(e.target.value)}
                onKeyDown={e => { if (e.key === 'Enter') handleCreateCase(); if (e.key === 'Escape') { setShowNewCaseModal(false); setNewCaseTitle(''); } }}
                autoFocus
              />
              <label className="modal-label" style={{ marginTop: 14 }}>Model</label>
              <select
                className="modal-select"
                value={newCaseModel}
                onChange={e => setNewCaseModel(e.target.value)}
              >
                <option value="gpt-4o-2024-11-20">gpt-4o-2024-11-20 (recommended)</option>
                <option value="gpt-4o-mini">gpt-4o-mini (fast, lower cost)</option>
                <option value="gpt-4o">gpt-4o</option>
                <option value="o1-preview">o1-preview</option>
              </select>
            </div>
            <div className="modal-footer">
              <button className="modal-btn secondary" onClick={() => { setShowNewCaseModal(false); setNewCaseTitle(''); }}>Cancel</button>
              <button
                className="modal-btn primary"
                disabled={!newCaseTitle.trim()}
                onClick={() => handleCreateCase()}
              >
                Create &amp; Open →
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ================================================================
          ACTIVITY LOG — floating beacon + terminal
          ================================================================ */}
      {(() => {
        const lastEntry = logs[logs.length - 1];
        const hasError = lastEntry?.level === 'error';
        const isActive = logs.length > 0;
        const preview = logs.slice(-3);
        return (
          <>
            {/* Backdrop — closes terminal when clicking outside */}
            {logsOpen && (
              <div className="log-backdrop" onClick={() => setLogsOpen(false)} />
            )}

            {/* Floating terminal panel */}
            {logsOpen && (
              <div className="log-terminal">
                <div className="log-terminal-header">
                  <div className="log-terminal-title">
                    <span className={`log-beacon-dot ${hasError ? 'error' : 'ok'} ${isActive ? 'pulse' : ''}`} />
                    Activity Log
                  </div>
                  <div className="log-terminal-actions">
                    <button className="log-terminal-btn" onClick={() => { setLogs([]); lastLogIdRef.current = 0; }} title="Clear">
                      <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                        <polyline points="3 6 5 6 21 6"/>
                        <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/>
                      </svg>
                    </button>
                    <button className="log-terminal-btn" onClick={() => setLogsOpen(false)} title="Close">
                      <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                        <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
                      </svg>
                    </button>
                  </div>
                </div>
                <div className="log-terminal-body">
                  {logs.length === 0 ? (
                    <div className="log-terminal-empty">Waiting for activity…</div>
                  ) : (
                    logs.map(entry => (
                      <div key={entry.id} className={`log-terminal-entry log-${entry.level}`}>
                        <span className="log-ts">{entry.ts}</span>
                        <span className="log-msg">{entry.msg}</span>
                      </div>
                    ))
                  )}
                  <div ref={logEndRef} />
                </div>
              </div>
            )}

            {/* Beacon */}
            {!logsOpen && (
              <div
                className={`log-beacon ${hasError ? 'error' : 'ok'} ${beaconHovered ? 'hovered' : ''}`}
                onClick={() => setLogsOpen(true)}
                onMouseEnter={() => setBeaconHovered(true)}
                onMouseLeave={() => setBeaconHovered(false)}
              >
                <span className={`log-beacon-dot ${hasError ? 'error' : 'ok'} ${isActive ? 'pulse' : ''}`} />

                {/* Hover preview — last 3 lines */}
                {beaconHovered && preview.length > 0 && (
                  <div className="log-beacon-preview">
                    <div className="log-beacon-preview-title">Activity Log</div>
                    {preview.map(entry => (
                      <div key={entry.id} className={`log-terminal-entry log-${entry.level}`}>
                        <span className="log-ts">{entry.ts}</span>
                        <span className="log-msg">{entry.msg}</span>
                      </div>
                    ))}
                    <div className="log-beacon-preview-hint">Click to open</div>
                  </div>
                )}
              </div>
            )}
          </>
        );
      })()}

    </div>
  );
}
