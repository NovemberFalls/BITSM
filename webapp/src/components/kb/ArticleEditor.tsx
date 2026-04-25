import { useState } from 'react';

interface Collection {
  id: number;
  name: string;
}

interface ArticleEditorProps {
  article?: { id: number; title: string; content: string; is_published: boolean; tenant_collection_id?: number | null } | null;
  collections?: Collection[];
  onSave: (data: { title: string; content: string; is_published: boolean; tenant_collection_id?: number | null }) => Promise<void>;
  onCancel: () => void;
}

export function ArticleEditor({ article, collections, onSave, onCancel }: ArticleEditorProps) {
  const [title, setTitle] = useState(article?.title || '');
  const [content, setContent] = useState(article?.content || '');
  const [published, setPublished] = useState(article?.is_published ?? true);
  const [collectionId, setCollectionId] = useState<number | null>(article?.tenant_collection_id ?? null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  const handleSave = async () => {
    if (!title.trim()) {
      setError('Title is required');
      return;
    }
    setSaving(true);
    setError('');
    try {
      await onSave({ title: title.trim(), content, is_published: published, tenant_collection_id: collectionId });
    } catch (e: any) {
      setError(e.message || 'Save failed');
      setSaving(false);
    }
  };

  return (
    <div className="modal-backdrop" onClick={(e) => { if (e.target === e.currentTarget) onCancel(); }}>
      <div className="modal-container" style={{ maxWidth: 640 }}>
        <div className="modal-header">
          <div className="modal-title">{article ? 'Edit Article' : 'New Article'}</div>
          <button className="modal-close" onClick={onCancel}>&times;</button>
        </div>
        <div className="modal-body">
          {error && <div className="form-error">{error}</div>}

          <div className="form-group">
            <label className="form-label">Title</label>
            <input
              className="form-input"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="Article title..."
              autoFocus
            />
          </div>

          <div className="form-group">
            <label className="form-label">Content</label>
            <textarea
              className="form-input form-textarea"
              value={content}
              onChange={(e) => setContent(e.target.value)}
              placeholder="Write your article content..."
              style={{ minHeight: 240, fontFamily: 'var(--mono)', fontSize: 12 }}
            />
          </div>

          {collections && collections.length > 0 && (
            <div className="form-group">
              <label className="form-label">Collection</label>
              <select
                className="form-input"
                value={collectionId ?? ''}
                onChange={(e) => setCollectionId(e.target.value ? Number(e.target.value) : null)}
              >
                <option value="">No collection</option>
                {collections.map((c) => (
                  <option key={c.id} value={c.id}>{c.name}</option>
                ))}
              </select>
            </div>
          )}

          <div className="form-group">
            <label className="comment-internal-toggle">
              <input
                type="checkbox"
                checked={published}
                onChange={(e) => setPublished(e.target.checked)}
              />
              Published (visible to all tenant users)
            </label>
          </div>

          <div className="modal-footer">
            <button className="btn btn-ghost" onClick={onCancel}>Cancel</button>
            <button className="btn btn-primary" onClick={handleSave} disabled={saving}>
              {saving ? 'Saving...' : 'Save Article'}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
