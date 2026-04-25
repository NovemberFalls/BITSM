import { useState } from 'react';
import { buildTree } from '../../store/hierarchyStore';

interface TreeItem {
  id: number;
  parent_id: number | null;
  name: string;
  level_label?: string | null;
  sort_order?: number;
}

interface TreeManagerProps {
  items: TreeItem[];
  title: string;
  onCreate: (data: { parent_id: number | null; name: string; level_label?: string }) => Promise<any>;
  onUpdate: (id: number, data: { name: string }) => Promise<void>;
  onDelete: (id: number) => Promise<void>;
  showLevelLabel?: boolean;
}

export function TreeManager({ items, title, onCreate, onUpdate, onDelete, showLevelLabel = false }: TreeManagerProps) {
  const [addingTo, setAddingTo] = useState<number | null | 'root'>(null);
  const [newName, setNewName] = useState('');
  const [newLevelLabel, setNewLevelLabel] = useState('');
  const [submitting, setSubmitting] = useState(false);

  const tree = buildTree(items, null);

  const handleAdd = async (parentId: number | null) => {
    if (!newName.trim() || submitting) return;
    setSubmitting(true);
    try {
      await onCreate({
        parent_id: parentId,
        name: newName.trim(),
        ...(showLevelLabel && newLevelLabel.trim() ? { level_label: newLevelLabel.trim() } : {}),
      });
      setNewName('');
      setNewLevelLabel('');
      setAddingTo(null);
    } catch {}
    setSubmitting(false);
  };

  const handleKeyDown = (e: React.KeyboardEvent, parentId: number | null) => {
    if (e.key === 'Enter') handleAdd(parentId);
    if (e.key === 'Escape') { setAddingTo(null); setNewName(''); setNewLevelLabel(''); }
  };

  return (
    <div className="tree-manager">
      <div className="tree-manager-header">
        <h3 className="tree-manager-title">{title}</h3>
        <button
          className="btn btn-ghost btn-sm"
          onClick={() => { setAddingTo('root'); setNewName(''); setNewLevelLabel(''); }}
        >
          + Add Root
        </button>
      </div>

      {tree.length === 0 && addingTo !== 'root' && (
        <div className="empty-state">
          <div className="empty-state-text">No items configured yet. Click "+ Add Root" to start.</div>
        </div>
      )}

      {addingTo === 'root' && (
        <div className="tree-add-form">
          <input
            className="form-input"
            placeholder="Name..."
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            onKeyDown={(e) => handleKeyDown(e, null)}
            autoFocus
          />
          {showLevelLabel && (
            <input
              className="form-input"
              placeholder="Level label (e.g. Company)..."
              value={newLevelLabel}
              onChange={(e) => setNewLevelLabel(e.target.value)}
              onKeyDown={(e) => handleKeyDown(e, null)}
            />
          )}
          <div className="tree-add-actions">
            <button className="btn btn-primary btn-sm" onClick={() => handleAdd(null)} disabled={submitting}>Add</button>
            <button className="btn btn-ghost btn-sm" onClick={() => setAddingTo(null)}>Cancel</button>
          </div>
        </div>
      )}

      <div className="tree-nodes">
        {tree.map((node) => (
          <TreeNode
            key={node.id}
            node={node}
            depth={0}
            addingTo={addingTo}
            setAddingTo={setAddingTo}
            newName={newName}
            setNewName={setNewName}
            newLevelLabel={newLevelLabel}
            setNewLevelLabel={setNewLevelLabel}
            showLevelLabel={showLevelLabel}
            submitting={submitting}
            onAdd={handleAdd}
            onKeyDown={handleKeyDown}
            onUpdate={onUpdate}
            onDelete={onDelete}
          />
        ))}
      </div>
    </div>
  );
}

function TreeNode({
  node, depth, addingTo, setAddingTo, newName, setNewName,
  newLevelLabel, setNewLevelLabel, showLevelLabel, submitting,
  onAdd, onKeyDown, onUpdate, onDelete,
}: {
  node: any;
  depth: number;
  addingTo: number | null | 'root';
  setAddingTo: (v: number | null | 'root') => void;
  newName: string;
  setNewName: (v: string) => void;
  newLevelLabel: string;
  setNewLevelLabel: (v: string) => void;
  showLevelLabel: boolean;
  submitting: boolean;
  onAdd: (parentId: number | null) => void;
  onKeyDown: (e: React.KeyboardEvent, parentId: number | null) => void;
  onUpdate: (id: number, data: { name: string }) => Promise<void>;
  onDelete: (id: number) => Promise<void>;
}) {
  const [editing, setEditing] = useState(false);
  const [editName, setEditName] = useState(node.name);

  const handleSave = async () => {
    if (editName.trim() && editName.trim() !== node.name) {
      await onUpdate(node.id, { name: editName.trim() });
    }
    setEditing(false);
  };

  return (
    <div className="tree-node" style={{ paddingLeft: depth * 20 }}>
      <div className="tree-node-row">
        {editing ? (
          <input
            className="form-input tree-edit-input"
            value={editName}
            onChange={(e) => setEditName(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') handleSave(); if (e.key === 'Escape') setEditing(false); }}
            onBlur={handleSave}
            autoFocus
          />
        ) : (
          <span className="tree-node-name" onDoubleClick={() => { setEditing(true); setEditName(node.name); }}>
            {node.children?.length > 0 && <span className="tree-expand">&#x25B8; </span>}
            {node.name}
            {showLevelLabel && node.level_label && (
              <span className="tree-level-label">{node.level_label}</span>
            )}
          </span>
        )}
        <div className="tree-node-actions">
          <button
            className="btn btn-ghost btn-sm"
            onClick={() => { setAddingTo(node.id); setNewName(''); setNewLevelLabel(''); }}
            title="Add child"
          >
            +
          </button>
          <button className="btn btn-ghost btn-sm" onClick={() => onDelete(node.id)} title="Delete">
            -
          </button>
        </div>
      </div>

      {addingTo === node.id && (
        <div className="tree-add-form" style={{ marginLeft: 20 }}>
          <input
            className="form-input"
            placeholder="Name..."
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            onKeyDown={(e) => onKeyDown(e, node.id)}
            autoFocus
          />
          {showLevelLabel && (
            <input
              className="form-input"
              placeholder="Level label..."
              value={newLevelLabel}
              onChange={(e) => setNewLevelLabel(e.target.value)}
              onKeyDown={(e) => onKeyDown(e, node.id)}
            />
          )}
          <div className="tree-add-actions">
            <button className="btn btn-primary btn-sm" onClick={() => onAdd(node.id)} disabled={submitting}>Add</button>
            <button className="btn btn-ghost btn-sm" onClick={() => setAddingTo(null)}>Cancel</button>
          </div>
        </div>
      )}

      {node.children?.map((child: any) => (
        <TreeNode
          key={child.id}
          node={child}
          depth={depth + 1}
          addingTo={addingTo}
          setAddingTo={setAddingTo}
          newName={newName}
          setNewName={setNewName}
          newLevelLabel={newLevelLabel}
          setNewLevelLabel={setNewLevelLabel}
          showLevelLabel={showLevelLabel}
          submitting={submitting}
          onAdd={onAdd}
          onKeyDown={onKeyDown}
          onUpdate={onUpdate}
          onDelete={onDelete}
        />
      ))}
    </div>
  );
}
