import { useEffect, useState, useCallback, useRef } from 'react';
import { api } from '../../api/client';
import { useAuthStore } from '../../store/authStore';
import { pushUrl } from '../../utils/url';
import { ArticleEditor } from './ArticleEditor';
import { SendToTicketPicker } from '../common/SendToTicketPicker';

function useDebounce(value: string, delay: number) {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setDebounced(value), delay);
    return () => clearTimeout(t);
  }, [value, delay]);
  return debounced;
}

// Module-level cache
let _cachedModules: any[] = [];
let _cachedDocs: any[] = [];
let _cachedTags: { tag: string; count: number }[] = [];
let _cachedTotal = 0;
let _kbHasLoaded = false;

type KbTab = 'modules' | 'articles' | 'uploads';

function getKbParams(): { tab: KbTab; collection: string | null } {
  const params = new URLSearchParams(window.location.search);
  const rawTab = params.get('tab');
  const tab: KbTab = rawTab === 'articles' ? 'articles' : rawTab === 'uploads' ? 'uploads' : 'modules';
  const coll = params.get('collection');
  return { tab, collection: coll || null };
}

function pushKbUrl(tab: KbTab, collectionSlug?: string | null) {
  const params = new URLSearchParams();
  if (tab === 'articles') params.set('tab', 'articles');
  if (tab === 'uploads') params.set('tab', 'uploads');
  if (collectionSlug) params.set('collection', collectionSlug);
  const qs = params.toString();
  pushUrl('/kb', qs || undefined);
}

export function KnowledgeBase() {
  const isAdmin = useAuthStore((s) => s.isAdmin);
  const initial = getKbParams();
  const [tab, setTab] = useState<KbTab>(initial.tab);
  const [initialCollection] = useState(initial.collection);

  const handleTabChange = (t: KbTab) => {
    setTab(t);
    pushKbUrl(t, null);
  };

  // Browser back/forward
  useEffect(() => {
    const onPopState = () => {
      const p = getKbParams();
      setTab(p.tab);
    };
    window.addEventListener('popstate', onPopState);
    return () => window.removeEventListener('popstate', onPopState);
  }, []);

  return (
    <div>
      <div className="kb-tab-bar">
        <button
          className={`btn btn-sm ${tab === 'modules' ? 'btn-primary' : 'btn-ghost'}`}
          onClick={() => handleTabChange('modules')}
        >
          Knowledge Base
        </button>
        <button
          className={`btn btn-sm ${tab === 'articles' ? 'btn-primary' : 'btn-ghost'}`}
          onClick={() => handleTabChange('articles')}
        >
          My Articles
        </button>
        {isAdmin() && (
          <button
            className={`btn btn-sm ${tab === 'uploads' ? 'btn-primary' : 'btn-ghost'}`}
            onClick={() => handleTabChange('uploads')}
          >
            Uploads
          </button>
        )}
      </div>

      {tab === 'modules' && <ModuleDocuments />}
      {tab === 'articles' && <TenantArticles canEdit={isAdmin()} initialCollectionSlug={initialCollection} />}
      {tab === 'uploads' && <UploadHistory />}
    </div>
  );
}

const PAGE_SIZE = 50;

function ModuleDocuments() {
  const [modules, setModules] = useState<any[]>(_cachedModules);
  const [documents, setDocuments] = useState<any[]>(_cachedDocs);
  const [tags, setTags] = useState<{ tag: string; count: number }[]>(_cachedTags);
  const [total, setTotal] = useState(_cachedTotal);
  const [activeModule, setActiveModule] = useState<string | null>(null);
  const [activeTag, setActiveTag] = useState<string | null>(null);
  const [search, setSearch] = useState('');
  const [loading, setLoading] = useState(!_kbHasLoaded);
  const [loadingMore, setLoadingMore] = useState(false);
  const [expandedDoc, setExpandedDoc] = useState<number | null>(null);
  const [docDetail, setDocDetail] = useState<any>(null);
  const [docLoading, setDocLoading] = useState(false);
  const sentinelRef = useRef<HTMLDivElement>(null);

  const debouncedSearch = useDebounce(search, 300);

  // Load modules + tags once
  useEffect(() => {
    api.listKbModules().then((m) => { _cachedModules = m; setModules(m); }).catch(() => {});
    api.listDocumentTags().then((t) => { _cachedTags = t; setTags(t); }).catch(() => {});
  }, []);

  // Load documents when filters change (reset list)
  useEffect(() => {
    setLoading(true);
    const params: Record<string, string> = { limit: String(PAGE_SIZE), offset: '0' };
    if (activeModule) params.module = activeModule;
    if (activeTag) params.tag = activeTag;
    if (debouncedSearch) params.q = debouncedSearch;

    api.listDocuments(params).then((res) => {
      _cachedDocs = res.documents;
      _cachedTotal = res.total;
      _kbHasLoaded = true;
      setDocuments(res.documents);
      setTotal(res.total);
    }).catch(() => {}).finally(() => setLoading(false));
  }, [activeModule, activeTag, debouncedSearch]);

  // Infinite scroll via IntersectionObserver
  useEffect(() => {
    const sentinel = sentinelRef.current;
    if (!sentinel) return;

    const observer = new IntersectionObserver(
      (entries) => {
        if (entries[0].isIntersecting && documents.length < total && !loadingMore && !loading) {
          setLoadingMore(true);
          const params: Record<string, string> = {
            limit: String(PAGE_SIZE),
            offset: String(documents.length),
          };
          if (activeModule) params.module = activeModule;
          if (activeTag) params.tag = activeTag;
          if (debouncedSearch) params.q = debouncedSearch;

          api.listDocuments(params).then((res) => {
            setDocuments((prev) => {
              const merged = [...prev, ...res.documents];
              _cachedDocs = merged;
              return merged;
            });
          }).catch(() => {}).finally(() => setLoadingMore(false));
        }
      },
      { rootMargin: '200px' }
    );

    observer.observe(sentinel);
    return () => observer.disconnect();
  }, [documents.length, total, loadingMore, loading, activeModule, activeTag, debouncedSearch]);

  const handleDocClick = useCallback(async (docId: number) => {
    if (expandedDoc === docId) {
      setExpandedDoc(null);
      setDocDetail(null);
      return;
    }
    setExpandedDoc(docId);
    setDocLoading(true);
    try {
      const detail = await api.getDocument(docId);
      setDocDetail(detail);
    } catch {
      setDocDetail(null);
    }
    setDocLoading(false);
  }, [expandedDoc]);

  const handleTagClick = (tag: string) => {
    setActiveTag(activeTag === tag ? null : tag);
  };

  const handleModuleClick = (slug: string | null) => {
    setActiveModule(slug);
    setActiveTag(null); // reset tag filter on module change
  };

  const hasMore = documents.length < total;

  return (
    <div>
      {/* Module filters */}
      <div className="kb-filters">
        <button
          className={`btn btn-sm ${!activeModule ? 'btn-primary' : 'btn-ghost'}`}
          onClick={() => handleModuleClick(null)}
        >
          All
        </button>
        {modules.map((m) => (
          <button
            key={m.slug}
            className={`btn btn-sm ${activeModule === m.slug ? 'btn-primary' : 'btn-ghost'}`}
            onClick={() => handleModuleClick(m.slug)}
          >
            {m.name}
          </button>
        ))}
      </div>

      {/* Search */}
      <div className="kb-search">
        <input
          className="form-input"
          type="text"
          placeholder="Search articles..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        {search && (
          <button className="kb-search-clear" onClick={() => setSearch('')}>&times;</button>
        )}
      </div>

      {/* Tag cloud */}
      {tags.length > 0 && (
        <div className="kb-tag-cloud">
          {tags.slice(0, 30).map((t) => (
            <button
              key={t.tag}
              className={`kb-tag-chip${activeTag === t.tag ? ' active' : ''}`}
              onClick={() => handleTagClick(t.tag)}
            >
              {t.tag} <span className="kb-tag-count">{t.count}</span>
            </button>
          ))}
          {activeTag && (
            <button className="kb-tag-clear" onClick={() => setActiveTag(null)}>
              Clear filter
            </button>
          )}
        </div>
      )}

      {/* Document count */}
      {!loading && total > 0 && (
        <div className="kb-doc-count">{total} article{total !== 1 ? 's' : ''}{activeTag ? ` tagged "${activeTag}"` : ''}</div>
      )}

      {/* Document list */}
      <div className="kb-doc-list">
        {loading ? (
          <div className="empty-state">
            <div className="empty-state-icon">&#x27F3;</div>
            <div className="empty-state-text">Loading articles...</div>
          </div>
        ) : documents.length === 0 ? (
          <div className="empty-state">
            <div className="empty-state-icon">&#x25C9;</div>
            <div className="empty-state-title">
              {modules.length === 0 ? 'No modules enabled' : 'No documents found'}
            </div>
            <div className="empty-state-text">
              {modules.length === 0
                ? 'No knowledge modules enabled for your tenant. Contact admin to enable modules.'
                : 'Try adjusting your search or filters.'}
            </div>
          </div>
        ) : (
          <>
            {documents.map((d) => (
              <div key={d.id} className={`kb-doc-card ${expandedDoc === d.id ? 'kb-doc-expanded' : ''}`}>
                <div className="kb-doc-header" onClick={() => handleDocClick(d.id)}>
                  <div className="kb-doc-info">
                    <span className="badge badge-medium">{d.module_name}</span>
                    <span className="kb-doc-title">{d.title}</span>
                  </div>
                  <div className="kb-doc-meta">
                    {d.tags && d.tags.length > 0 && (
                      <div className="kb-doc-tags">
                        {d.tags.slice(0, 3).map((t: string) => (
                          <span key={t} className="tag-chip-doc">{t}</span>
                        ))}
                      </div>
                    )}
                    <span className="kb-doc-chevron">{expandedDoc === d.id ? '\u25BE' : '\u25B8'}</span>
                  </div>
                </div>
                {expandedDoc === d.id && (
                  <div className="kb-doc-detail">
                    {docLoading ? (
                      <div className="kb-doc-detail-loading">Loading content...</div>
                    ) : docDetail ? (
                      <>
                        {docDetail.source_url && (
                          <div className="kb-doc-detail-link">
                            Source: <a href={docDetail.source_url} target="_blank" rel="noopener noreferrer">{docDetail.source_url}</a>
                          </div>
                        )}
                        {docDetail.tags && docDetail.tags.length > 0 && (
                          <div className="kb-doc-detail-tags">
                            {docDetail.tags.map((t: string) => (
                              <span key={t} className="tag-chip-doc">{t}</span>
                            ))}
                          </div>
                        )}
                        <div className="kb-doc-detail-meta">
                          Created: {new Date(docDetail.created_at).toLocaleDateString()}
                        </div>
                        <div className="kb-doc-detail-actions">
                          <SendToTicketPicker documentId={d.id} />
                        </div>
                      </>
                    ) : (
                      <div className="kb-doc-detail-loading">Unable to load details.</div>
                    )}
                  </div>
                )}
              </div>
            ))}
            {/* Infinite scroll sentinel */}
            <div ref={sentinelRef} className="kb-load-sentinel">
              {loadingMore && <div className="kb-loading-more">Loading more...</div>}
              {!hasMore && documents.length > 0 && (
                <div className="kb-end-of-list">All {total} articles loaded</div>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
}

const ACCEPTED_EXTENSIONS = '.txt,.pdf,.docx';
const MAX_UPLOAD_SIZE = 50 * 1024 * 1024; // 50 MB
const VALID_EXTENSIONS = ['txt', 'pdf', 'docx'];

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function fileTypeLabel(a: any): string {
  if (!a.source_file_type) return '';
  if (a.source_file_type === 'application/pdf') return 'PDF';
  if (a.source_file_type?.includes('wordprocessing')) return 'DOCX';
  return 'TXT';
}

function TenantArticles({ canEdit, initialCollectionSlug }: { canEdit: boolean; initialCollectionSlug?: string | null }) {
  // Collections — tracked by slug, not numeric ID
  const [collections, setCollections] = useState<any[]>([]);
  const [activeCollSlug, _setActiveCollSlug] = useState<string | null>(initialCollectionSlug ?? null);
  const setActiveCollection = useCallback((slug: string | null) => {
    _setActiveCollSlug(slug);
    pushKbUrl('articles', slug);
  }, []);
  const [newCollName, setNewCollName] = useState('');
  const [creatingColl, setCreatingColl] = useState(false);
  const [collError, setCollError] = useState('');

  // Articles (filtered by collection)
  const [articles, setArticles] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState<any | null>(null);
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const [expandedDetail, setExpandedDetail] = useState<any>(null);

  // Upload state
  const [uploadFiles, setUploadFiles] = useState<File[]>([]);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState('');
  const [uploadResult, setUploadResult] = useState<{ uploaded: number; errors: number } | null>(null);
  const [dragOver, setDragOver] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const loadCollections = () => {
    api.listCollections().then(setCollections).catch(() => {});
  };

  const loadArticles = useCallback((slug?: string | null) => {
    setLoading(true);
    api.listArticles(slug ?? undefined)
      .then(setArticles)
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => { loadCollections(); loadArticles(initialCollectionSlug); }, []);

  // Browser back/forward: sync collection from URL
  useEffect(() => {
    const onPopState = () => {
      const p = getKbParams();
      _setActiveCollSlug(p.collection);
    };
    window.addEventListener('popstate', onPopState);
    return () => window.removeEventListener('popstate', onPopState);
  }, []);

  // Reload articles when active collection changes
  useEffect(() => {
    loadArticles(activeCollSlug);
  }, [activeCollSlug, loadArticles]);

  const handleCreateCollection = async () => {
    const name = newCollName.trim();
    if (!name) return;
    setCreatingColl(true);
    setCollError('');
    try {
      const result = await api.createCollection({ name });
      setNewCollName('');
      loadCollections();
      setActiveCollection(result.slug);
    } catch (e: any) {
      setCollError(e.message || 'Failed to create collection');
    }
    setCreatingColl(false);
  };

  const handleDeleteCollection = async (id: number, slug: string, e: React.MouseEvent) => {
    e.stopPropagation();
    const coll = collections.find(c => c.id === id);
    if (!confirm(`Delete "${coll?.name}" and all its articles?`)) return;
    await api.deleteCollection(id);
    if (activeCollSlug === slug) setActiveCollection(null);
    loadCollections();
    loadArticles(activeCollSlug === slug ? null : activeCollSlug);
  };

  const handleSave = async (data: { title: string; content: string; is_published: boolean; tenant_collection_id?: number | null }) => {
    const activeColl = collections.find(c => c.slug === activeCollSlug);
    if (editing?.id) {
      await api.updateArticle(editing.id, data);
    } else {
      const { tenant_collection_id, ...rest } = data;
      await api.createArticle({ ...rest, collection_id: tenant_collection_id ?? activeColl?.id });
    }
    setEditing(null);
    loadArticles(activeCollSlug);
    loadCollections();
  };

  const handleDelete = async (id: number) => {
    if (!confirm('Delete this article?')) return;
    try {
      await api.deleteArticle(id);
      loadArticles(activeCollSlug);
      loadCollections();
    } catch (e: any) {
      alert(e.message || 'Failed to delete article');
    }
  };

  const handleExpand = async (id: number) => {
    if (expandedId === id) { setExpandedId(null); setExpandedDetail(null); return; }
    setExpandedId(id);
    try { setExpandedDetail(await api.getArticle(id)); } catch { setExpandedDetail(null); }
  };

  // --- File handling ---
  const validateAndAddFiles = (incoming: FileList | File[]) => {
    const valid: File[] = [];
    const errs: string[] = [];
    for (const f of Array.from(incoming)) {
      const ext = f.name.split('.').pop()?.toLowerCase();
      if (!ext || !VALID_EXTENSIONS.includes(ext)) { errs.push(`${f.name}: unsupported type`); continue; }
      if (f.size > MAX_UPLOAD_SIZE) { errs.push(`${f.name}: too large (${formatFileSize(f.size)})`); continue; }
      valid.push(f);
    }
    setUploadFiles(prev => {
      const combined = [...prev, ...valid];
      if (combined.length > 30) { errs.push(`Max 30 files per upload (${combined.length} selected — first 30 kept)`); return combined.slice(0, 30); }
      return combined;
    });
    if (errs.length) setUploadError(errs.join('; '));
    else setUploadError('');
  };

  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files) validateAndAddFiles(e.target.files);
  };

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(false);
    setUploadResult(null);
    if (e.dataTransfer.files) validateAndAddFiles(e.dataTransfer.files);
  }, []);

  const removeFile = (idx: number) => {
    setUploadFiles(prev => prev.filter((_, i) => i !== idx));
    setUploadError('');
  };

  const handleUpload = async () => {
    if (!uploadFiles.length || !activeCollSlug) return;
    setUploading(true);
    setUploadError('');
    setUploadResult(null);
    try {
      const result = await api.uploadArticles(activeCollSlug, uploadFiles);
      setUploadResult({ uploaded: result.uploaded, errors: result.errors });
      setUploadFiles([]);
      if (fileInputRef.current) fileInputRef.current.value = '';
      loadArticles(activeCollSlug);
      loadCollections();
    } catch (e: any) {
      setUploadError(e.message || 'Upload failed');
    }
    setUploading(false);
  };

  const activeColl = collections.find(c => c.slug === activeCollSlug);

  return (
    <div>
      {/* Collection bar */}
      <div className="kb-coll-bar">
        <button
          className={`btn btn-sm ${!activeCollSlug ? 'btn-primary' : 'btn-ghost'}`}
          onClick={() => setActiveCollection(null)}
        >
          All Articles
        </button>
        {collections.map((c) => (
          <button
            key={c.id}
            className={`btn btn-sm ${activeCollSlug === c.slug ? 'btn-primary' : 'btn-ghost'}`}
            onClick={() => setActiveCollection(c.slug)}
            title={`${c.doc_count} article${c.doc_count !== 1 ? 's' : ''}`}
          >
            {c.name}
            <span className="kb-coll-count">{c.doc_count}</span>
            {canEdit && (
              <span
                className="kb-coll-delete"
                onClick={(e) => handleDeleteCollection(c.id, c.slug, e)}
                title="Delete collection"
              >&times;</span>
            )}
          </button>
        ))}
        {canEdit && (
          <span className="kb-coll-create">
            <input
              className="form-input kb-coll-input"
              value={newCollName}
              onChange={(e) => setNewCollName(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') handleCreateCollection(); }}
              placeholder="+ New Collection"
            />
            {newCollName.trim() && (
              <button className="btn btn-sm btn-primary" onClick={handleCreateCollection} disabled={creatingColl}>
                {creatingColl ? '...' : 'Create'}
              </button>
            )}
          </span>
        )}
      </div>
      {collError && <div className="form-error" style={{ marginBottom: 10 }}>{collError}</div>}

      {/* Upload section — only when a collection is selected */}
      {canEdit && activeCollSlug && (
        <div
          className={`kb-upload-section${dragOver ? ' kb-upload-dragover' : ''}`}
          onDrop={handleDrop}
          onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
          onDragLeave={() => setDragOver(false)}
        >
          <div className="kb-upload-header">
            Upload to "{activeColl?.name}"
            <span className="kb-upload-header-hint">Filenames become article titles</span>
          </div>
          <div className="kb-upload-body">
            <div className="kb-upload-dropzone" onClick={() => fileInputRef.current?.click()}>
              {uploadFiles.length > 0 ? (
                <div className="kb-upload-file-list">
                  {uploadFiles.map((f, i) => (
                    <div key={i} className="kb-upload-file-row">
                      <span className="kb-upload-file-icon">
                        {f.name.endsWith('.pdf') ? '\u{1F4C4}' : f.name.endsWith('.docx') ? '\u{1F4DD}' : '\u{1F4C3}'}
                      </span>
                      <span className="kb-upload-file-name">{f.name}</span>
                      <span className="kb-upload-file-size">{formatFileSize(f.size)}</span>
                      <button
                        className="kb-upload-file-remove"
                        onClick={(e) => { e.stopPropagation(); removeFile(i); }}
                        title="Remove"
                      >&times;</button>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="kb-upload-placeholder">
                  Drop files here or click to browse
                  <div className="kb-upload-hint">Up to 30 files &middot; .txt, .pdf, .docx &middot; 50 MB each</div>
                </div>
              )}
              <input
                ref={fileInputRef}
                type="file"
                accept={ACCEPTED_EXTENSIONS}
                multiple
                onChange={handleFileSelect}
                style={{ display: 'none' }}
              />
            </div>

            {uploadError && <div className="form-error" style={{ marginTop: 8 }}>{uploadError}</div>}
            {uploadResult && (
              <div className="kb-upload-success">
                {uploadResult.uploaded} file{uploadResult.uploaded !== 1 ? 's' : ''} uploaded and embedding for AI search
                {uploadResult.errors > 0 && ` (${uploadResult.errors} failed)`}
              </div>
            )}

            {uploadFiles.length > 0 && (
              <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', marginTop: 10, alignItems: 'center' }}>
                <span style={{ fontSize: 11, color: 'var(--t-text-dim)', marginRight: 'auto' }}>
                  {uploadFiles.length} file{uploadFiles.length !== 1 ? 's' : ''} selected
                </span>
                <button className="btn btn-ghost btn-sm" onClick={() => { setUploadFiles([]); setUploadError(''); if (fileInputRef.current) fileInputRef.current.value = ''; }}>
                  Clear
                </button>
                <button className="btn btn-primary btn-sm" onClick={handleUpload} disabled={uploading}>
                  {uploading ? 'Uploading...' : `Upload ${uploadFiles.length} File${uploadFiles.length !== 1 ? 's' : ''}`}
                </button>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Action bar */}
      {canEdit && (
        <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 12, marginTop: activeCollSlug ? 0 : 12 }}>
          <button className="btn btn-primary btn-sm" onClick={() => setEditing({})}>
            + Write Article
          </button>
        </div>
      )}

      {/* Article list */}
      {loading ? (
        <div className="empty-state"><div className="empty-state-text">Loading articles...</div></div>
      ) : articles.length === 0 ? (
        <div className="empty-state">
          <div className="empty-state-icon">&#x1F4DD;</div>
          <div className="empty-state-title">
            {activeCollSlug ? `No articles in "${activeColl?.name}" yet` : 'No articles yet'}
          </div>
          <div className="empty-state-text">
            {canEdit
              ? activeCollSlug
                ? 'Drop files above or write an article manually.'
                : 'Create a collection and upload documents, or write an article.'
              : 'No articles have been published yet.'}
          </div>
        </div>
      ) : (
        <div className="kb-doc-list">
          {articles.map((a) => (
            <div key={a.id} className={`kb-doc-card ${expandedId === a.id ? 'kb-doc-expanded' : ''}`}>
              <div className="kb-doc-header" onClick={() => handleExpand(a.id)}>
                <div className="kb-doc-info">
                  <span className="badge badge-medium">{a.collection_name || 'Uncollected'}</span>
                  <span className="kb-doc-title">{a.title}</span>
                  {!a.is_published && <span className="badge badge-closed_not_resolved">Draft</span>}
                </div>
                <div className="kb-doc-meta">
                  {a.source_file_name && (
                    <span className="kb-doc-file-badge" title={a.source_file_name}>
                      {fileTypeLabel(a)}{a.file_size ? ` \u00B7 ${formatFileSize(a.file_size)}` : ''}
                    </span>
                  )}
                  {a.author_name && <span>{a.author_name}</span>}
                  <span>&middot;</span>
                  <span>{new Date(a.updated_at || a.created_at).toLocaleDateString()}</span>
                  {canEdit && (
                    <>
                      <button className="btn btn-sm btn-ghost" style={{ padding: '2px 6px', fontSize: 11 }}
                        onClick={(e) => { e.stopPropagation(); api.getArticle(a.id).then(d => setEditing(d)); }}>Edit</button>
                      <button className="btn btn-sm btn-danger" style={{ padding: '2px 6px', fontSize: 11 }}
                        onClick={(e) => { e.stopPropagation(); handleDelete(a.id); }}>&times;</button>
                    </>
                  )}
                  <span className="kb-doc-chevron">{expandedId === a.id ? '\u25BE' : '\u25B8'}</span>
                </div>
              </div>
              {expandedId === a.id && expandedDetail && (
                <div className="kb-doc-detail">
                  <div className="kb-doc-detail-content" style={{ whiteSpace: 'pre-wrap', fontSize: 13, lineHeight: 1.7, marginTop: 10 }}>
                    {expandedDetail.content || 'No content.'}
                  </div>
                  <div className="kb-doc-detail-meta" style={{ marginTop: 8 }}>
                    Created: {new Date(expandedDetail.created_at).toLocaleDateString()}
                    {expandedDetail.updated_at && ` \u00B7 Updated: ${new Date(expandedDetail.updated_at).toLocaleDateString()}`}
                  </div>
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {editing !== null && (
        <ArticleEditor
          article={editing.id ? editing : null}
          collections={collections}
          onSave={handleSave}
          onCancel={() => setEditing(null)}
        />
      )}
    </div>
  );
}

// ── Upload History ────────────────────────────────────────────

interface UploadRecord {
  id: number;
  title: string;
  source_file_name: string;
  source_file_type: string | null;
  file_size: number | null;
  created_at: string;
  uploader_name: string | null;
  collection_name: string | null;
  chunk_count: number;
  has_embeddings: boolean;
}

function fileTypeBadgeClass(mimeType: string | null): string {
  if (!mimeType) return 'upload-badge-txt';
  if (mimeType === 'application/pdf') return 'upload-badge-pdf';
  if (mimeType.includes('wordprocessing')) return 'upload-badge-docx';
  return 'upload-badge-txt';
}

function fileTypeShortLabel(mimeType: string | null): string {
  if (!mimeType) return 'TXT';
  if (mimeType === 'application/pdf') return 'PDF';
  if (mimeType.includes('wordprocessing')) return 'DOCX';
  return 'TXT';
}

function relativeDate(isoString: string): string {
  const now = Date.now();
  const then = new Date(isoString).getTime();
  const diffMs = now - then;
  const diffSecs = Math.floor(diffMs / 1000);
  if (diffSecs < 60) return 'just now';
  const diffMins = Math.floor(diffSecs / 60);
  if (diffMins < 60) return `${diffMins}m ago`;
  const diffHrs = Math.floor(diffMins / 60);
  if (diffHrs < 24) return `${diffHrs}h ago`;
  const diffDays = Math.floor(diffHrs / 24);
  if (diffDays < 30) return `${diffDays}d ago`;
  return new Date(isoString).toLocaleDateString();
}

// Grid template: FileName | Type | Size | Collection | Chunks | UploadedBy | Date
const UPLOAD_GRID = '2.5fr 60px 70px 1.2fr 70px 1fr 90px';

function UploadHistory() {
  const [records, setRecords] = useState<UploadRecord[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.listUploadHistory()
      .then(setRecords)
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  if (loading) {
    return (
      <div className="empty-state">
        <div className="empty-state-text">Loading upload history...</div>
      </div>
    );
  }

  if (records.length === 0) {
    return (
      <div className="empty-state">
        <div className="empty-state-icon">&#x1F4C2;</div>
        <div className="empty-state-title">No uploads yet</div>
        <div className="empty-state-text">No files have been uploaded yet.</div>
      </div>
    );
  }

  return (
    <div className="report-table" style={{ marginTop: 12 }}>
      <div className="report-table-header" style={{ gridTemplateColumns: UPLOAD_GRID }}>
        <div className="report-table-cell">File Name</div>
        <div className="report-table-cell">Type</div>
        <div className="report-table-cell right">Size</div>
        <div className="report-table-cell">Collection</div>
        <div className="report-table-cell center">Chunks</div>
        <div className="report-table-cell">Uploaded By</div>
        <div className="report-table-cell right">Date</div>
      </div>
      {records.map((r) => (
        <div key={r.id} className="report-table-row" style={{ gridTemplateColumns: UPLOAD_GRID }}>
          <div className="report-table-cell" title={r.source_file_name} style={{ fontWeight: 500 }}>
            {r.source_file_name}
          </div>
          <div className="report-table-cell">
            <span className={`upload-type-badge ${fileTypeBadgeClass(r.source_file_type)}`}>
              {fileTypeShortLabel(r.source_file_type)}
            </span>
          </div>
          <div className="report-table-cell right" style={{ color: 'var(--t-text-muted)', fontSize: 12 }}>
            {r.file_size != null ? formatFileSize(r.file_size) : '—'}
          </div>
          <div className="report-table-cell" style={{ color: 'var(--t-text-muted)' }}>
            {r.collection_name || '—'}
          </div>
          <div className="report-table-cell center">
            <span style={{ color: r.has_embeddings ? 'var(--t-success, #4caf50)' : 'var(--t-error, #e53935)', fontWeight: 600, fontSize: 13 }}>
              {r.chunk_count}
            </span>
          </div>
          <div className="report-table-cell" style={{ color: 'var(--t-text-muted)', fontSize: 12 }}>
            {r.uploader_name || '—'}
          </div>
          <div className="report-table-cell right" title={new Date(r.created_at).toLocaleString()} style={{ color: 'var(--t-text-muted)', fontSize: 12 }}>
            {relativeDate(r.created_at)}
          </div>
        </div>
      ))}
    </div>
  );
}
