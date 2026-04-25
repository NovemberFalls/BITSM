import { useState, useRef } from 'react';
import { useHierarchyStore } from '../../store/hierarchyStore';
import { api } from '../../api/client';

interface ImportResult {
  created: number;
  skipped: number;
  linked: number;
}

export function LocationImport({ onClose }: { onClose: () => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<string[][]>([]);
  const [importing, setImporting] = useState(false);
  const [result, setResult] = useState<ImportResult | null>(null);
  const [error, setError] = useState('');
  const [dragging, setDragging] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const loadLocations = useHierarchyStore((s) => s.loadLocations);

  const handleFile = (f: File) => {
    setFile(f);
    setResult(null);
    setError('');

    // Client-side CSV preview
    if (f.name.toLowerCase().endsWith('.csv')) {
      const reader = new FileReader();
      reader.onload = (e) => {
        const text = e.target?.result as string;
        const lines = text.split('\n').filter(l => l.trim());
        const rows = lines.slice(0, 11).map(l => {
          // Simple CSV split (handles most cases)
          const result: string[] = [];
          let current = '';
          let inQuotes = false;
          for (const ch of l) {
            if (ch === '"') { inQuotes = !inQuotes; continue; }
            if (ch === ',' && !inQuotes) { result.push(current.trim()); current = ''; continue; }
            current += ch;
          }
          result.push(current.trim());
          return result;
        });
        setPreview(rows);
      };
      reader.readAsText(f);
    } else {
      setPreview([]);
    }
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setDragging(false);
    const f = e.dataTransfer.files[0];
    if (f) handleFile(f);
  };

  const handleImport = async () => {
    if (!file) return;
    setImporting(true);
    setError('');
    try {
      const res = await api.importLocations(file);
      setResult(res);
      await loadLocations();
    } catch (e: any) {
      setError(e.message || 'Import failed');
    }
    setImporting(false);
  };

  return (
    <div className="import-panel">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <h4 style={{ fontSize: 14, fontWeight: 600, color: 'var(--t-text-bright)', margin: 0 }}>
          Import Locations
        </h4>
        <button className="btn btn-sm btn-ghost" onClick={onClose}>Close</button>
      </div>

      <p style={{ fontSize: 12, color: 'var(--t-text-muted)', marginBottom: 8 }}>
        Upload a CSV, JSON, or Excel (.xlsx) file. Freshservice CSV format is supported.
        Columns: Location Name, Parent Location, Contact Name, Email, Phone, Address, City, State, Country, ZipCode.
      </p>
      <div style={{ marginBottom: 16 }}>
        <a
          href="/api/hierarchies/locations/template"
          download
          style={{ fontSize: 12, color: 'var(--t-accent)', textDecoration: 'underline', cursor: 'pointer' }}
        >
          Download Template
        </a>
      </div>

      {/* Drop zone */}
      <div
        className={`import-dropzone ${dragging ? 'dragging' : ''}`}
        onClick={() => fileRef.current?.click()}
        onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
        onDragLeave={() => setDragging(false)}
        onDrop={handleDrop}
      >
        {file ? (
          <span>{file.name} ({(file.size / 1024).toFixed(1)} KB)</span>
        ) : (
          <span>Drop file here or click to browse<br />
            <small style={{ color: 'var(--t-text-dim)' }}>.csv, .json, .xlsx</small>
          </span>
        )}
      </div>
      <input
        ref={fileRef}
        type="file"
        accept=".csv,.json,.xlsx,.xls"
        style={{ display: 'none' }}
        onChange={(e) => e.target.files?.[0] && handleFile(e.target.files[0])}
      />

      {/* CSV Preview */}
      {preview.length > 0 && (
        <div className="import-preview">
          <table>
            <thead>
              <tr>
                {preview[0].map((h, i) => (
                  <th key={i}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {preview.slice(1).map((row, ri) => (
                <tr key={ri}>
                  {row.map((cell, ci) => (
                    <td key={ci}>{cell}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
          <div style={{ fontSize: 11, color: 'var(--t-text-dim)', marginTop: 4 }}>
            Showing first {preview.length - 1} rows
          </div>
        </div>
      )}

      {/* Actions */}
      <div style={{ display: 'flex', gap: 8, marginTop: 16, alignItems: 'center' }}>
        <button
          className="btn btn-primary"
          onClick={handleImport}
          disabled={!file || importing}
        >
          {importing ? 'Importing...' : 'Import'}
        </button>
        {error && <span style={{ color: 'var(--t-error)', fontSize: 12 }}>{error}</span>}
      </div>

      {/* Result */}
      {result && (
        <div className="import-result">
          Import complete: {result.created} created, {result.skipped} skipped (duplicates), {result.linked} parent links resolved.
        </div>
      )}
    </div>
  );
}
