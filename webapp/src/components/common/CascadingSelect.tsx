import { useState, useEffect, useMemo } from 'react';
import { getChildren, getBreadcrumb } from '../../store/hierarchyStore';

interface TreeItem {
  id: number;
  parent_id: number | null;
  name: string;
  level_label?: string | null;
}

interface CascadingSelectProps {
  items: TreeItem[];
  value: number | null;
  onChange: (id: number | null) => void;
  placeholder?: string;
  levelLabels?: string[];
  allowNonLeaf?: boolean;
}

export function CascadingSelect({
  items,
  value,
  onChange,
  placeholder = 'Select...',
  levelLabels,
  allowNonLeaf = true,
}: CascadingSelectProps) {
  // Build selection path from current value back to root
  const [selections, setSelections] = useState<(number | null)[]>([]);

  useEffect(() => {
    if (value == null) {
      setSelections([]);
      return;
    }
    // Walk up from value to root to build selection path
    const path: number[] = [];
    let currentId: number | null = value;
    const seen = new Set<number>();
    while (currentId != null) {
      if (seen.has(currentId)) break;
      seen.add(currentId);
      path.unshift(currentId);
      const node = items.find((n) => n.id === currentId);
      if (!node) break;
      currentId = node.parent_id;
    }
    setSelections(path);
  }, [value, items]);

  // Build levels: each level shows children of the previous selection
  const levels = useMemo(() => {
    const result: { parentId: number | null; options: TreeItem[]; selected: number | null; label: string }[] = [];

    // First level: root items
    const rootItems = getChildren(items, null);
    if (rootItems.length === 0) return result;

    const firstLabel = levelLabels?.[0] || rootItems[0]?.level_label || 'Select';
    result.push({
      parentId: null,
      options: rootItems,
      selected: selections[0] ?? null,
      label: firstLabel,
    });

    // Subsequent levels based on selections
    for (let i = 0; i < selections.length; i++) {
      const selectedId = selections[i];
      if (selectedId == null) break;

      const children = getChildren(items, selectedId);
      if (children.length === 0) break;

      const levelLabel = levelLabels?.[i + 1] || children[0]?.level_label || 'Select';
      result.push({
        parentId: selectedId,
        options: children,
        selected: selections[i + 1] ?? null,
        label: levelLabel,
      });
    }

    return result;
  }, [items, selections, levelLabels]);

  const handleChange = (levelIndex: number, selectedId: number | null) => {
    const newSelections = selections.slice(0, levelIndex);
    if (selectedId != null) {
      newSelections.push(selectedId);
    }
    setSelections(newSelections);

    // Determine the effective selected value
    const effectiveId = selectedId;
    if (effectiveId != null) {
      const hasChildren = getChildren(items, effectiveId).length > 0;
      if (allowNonLeaf || !hasChildren) {
        onChange(effectiveId);
      } else {
        // Don't fire onChange yet — wait for leaf selection
        onChange(null);
      }
    } else {
      onChange(newSelections.length > 0 ? newSelections[newSelections.length - 1] : null);
    }
  };

  // Breadcrumb display
  const breadcrumb = value != null ? getBreadcrumb(items, value) : [];

  if (items.length === 0) {
    return (
      <div className="cascading-select">
        <select className="form-input form-select" disabled>
          <option>No options configured</option>
        </select>
      </div>
    );
  }

  return (
    <div className="cascading-select">
      {levels.map((level, i) => (
        <div key={`${level.parentId}-${i}`} className="cascading-select-level">
          <select
            className="form-input form-select"
            value={level.selected ?? ''}
            onChange={(e) => handleChange(i, e.target.value ? Number(e.target.value) : null)}
          >
            <option value="">{placeholder}</option>
            {level.options.map((opt) => (
              <option key={opt.id} value={opt.id}>{opt.name}</option>
            ))}
          </select>
        </div>
      ))}
      {breadcrumb.length > 1 && (
        <div className="cascading-breadcrumb">
          {breadcrumb.join(' > ')}
        </div>
      )}
    </div>
  );
}
