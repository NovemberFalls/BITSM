import { useState } from 'react';
import { api } from '../../api/client';
import type { TagSuggestion } from '../../types';

interface TagSuggestionsProps {
  ticketId: number;
  tags: string[];
  suggestions: TagSuggestion[];
  onUpdate: () => void;
  readOnly?: boolean;
}

export function TagSuggestions({ ticketId, tags, suggestions, onUpdate, readOnly = false }: TagSuggestionsProps) {
  const [newTag, setNewTag] = useState('');
  const [adding, setAdding] = useState(false);

  const handleAccept = async (suggestion: TagSuggestion, accepted: boolean) => {
    try {
      await api.acceptTag(ticketId, suggestion.id, accepted);
      onUpdate();
    } catch {}
  };

  const handleAddTag = async () => {
    const tag = newTag.trim().toLowerCase();
    if (!tag) return;
    setAdding(true);
    try {
      await api.addTag(ticketId, tag);
      setNewTag('');
      onUpdate();
    } catch {}
    setAdding(false);
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      handleAddTag();
    }
  };

  return (
    <div className="tag-suggestions">
      {/* Existing tags */}
      {tags.length > 0 && (
        <div className="tag-list">
          {tags.map((tag) => (
            <span key={tag} className="tag-chip tag-chip-accepted">{tag}</span>
          ))}
        </div>
      )}

      {/* LLM suggestions */}
      {suggestions.filter((s) => s.accepted === null).length > 0 && (
        <div className="tag-suggestions-section">
          <span className="tag-suggestions-label">AI Suggestions:</span>
          <div className="tag-list">
            {suggestions
              .filter((s) => s.accepted === null)
              .map((s) => (
                <span key={s.id} className="tag-chip tag-chip-pending">
                  {s.tag}
                  {!readOnly && (
                    <>
                      <button className="tag-action tag-accept" onClick={() => handleAccept(s, true)} title="Accept">+</button>
                      <button className="tag-action tag-reject" onClick={() => handleAccept(s, false)} title="Reject">-</button>
                    </>
                  )}
                </span>
              ))}
          </div>
        </div>
      )}

      {/* Manual tag input */}
      {!readOnly && (
        <div className="tag-input-row">
          <input
            type="text"
            className="form-input tag-input"
            placeholder="Add tag..."
            value={newTag}
            onChange={(e) => setNewTag(e.target.value)}
            onKeyDown={handleKeyDown}
            disabled={adding}
          />
          <button className="btn btn-ghost btn-sm" onClick={handleAddTag} disabled={adding || !newTag.trim()}>
            Add
          </button>
        </div>
      )}
    </div>
  );
}
