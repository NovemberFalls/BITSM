import { useEffect, useState } from 'react';

interface Location {
  id: number;
  name: string;
  parent_id: number | null;
  depth?: number;
}

interface LocationFilterProps {
  value: string;
  onChange: (locationId: string) => void;
  tenantId?: number;
}

export function LocationFilter({ value, onChange }: LocationFilterProps) {
  const [locations, setLocations] = useState<Location[]>([]);

  useEffect(() => {
    fetch('/api/hierarchies/locations', { credentials: 'include' })
      .then(r => r.ok ? r.json() : { locations: [] })
      .then(data => {
        const locs: Location[] = data.locations || [];
        // Build depth map for indentation
        const idToDepth: Record<number, number> = {};
        const getDepth = (id: number): number => {
          if (idToDepth[id] !== undefined) return idToDepth[id];
          const loc = locs.find(l => l.id === id);
          if (!loc || !loc.parent_id) { idToDepth[id] = 0; return 0; }
          idToDepth[id] = 1 + getDepth(loc.parent_id);
          return idToDepth[id];
        };
        locs.forEach(l => { l.depth = getDepth(l.id); });
        // Sort: top-level first, then children grouped under parents
        const sorted = sortTree(locs);
        setLocations(sorted);
      })
      .catch(() => {});
  }, []);

  if (locations.length === 0) return null;

  return (
    <select
      className="report-filter-select"
      value={value}
      onChange={e => onChange(e.target.value)}
      title="Filter by location"
    >
      <option value="">All Locations</option>
      {locations.map(l => (
        <option key={l.id} value={String(l.id)}>
          {'\u00a0'.repeat((l.depth ?? 0) * 3)}{l.name}
        </option>
      ))}
    </select>
  );
}

function sortTree(locs: Location[]): Location[] {
  const result: Location[] = [];
  const byParent: Record<string, Location[]> = {};
  for (const l of locs) {
    const key = l.parent_id == null ? 'root' : String(l.parent_id);
    (byParent[key] = byParent[key] || []).push(l);
  }
  function walk(parentKey: string) {
    for (const l of byParent[parentKey] || []) {
      result.push(l);
      walk(String(l.id));
    }
  }
  walk('root');
  return result;
}
