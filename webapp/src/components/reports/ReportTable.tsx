import { useState } from 'react';
import type { ReactNode } from 'react';

export interface ReportColumn {
  key: string;
  label: string;
  width: string;
  align?: 'left' | 'center' | 'right';
  sortable?: boolean;
}

interface ReportTableProps {
  columns: ReportColumn[];
  rows: Record<string, any>[];
  formatCell?: (key: string, value: any, row: Record<string, any>) => ReactNode;
  summaryRow?: Record<string, any>;
  emptyMessage?: string;
  onExportCsv?: () => void;
  defaultSortKey?: string;
  defaultSortDir?: 'asc' | 'desc';
}

function sortRows(rows: Record<string, any>[], key: string, dir: 'asc' | 'desc') {
  return [...rows].sort((a, b) => {
    const av = a[key], bv = b[key];
    if (av == null && bv == null) return 0;
    if (av == null) return 1;
    if (bv == null) return -1;
    const cmp = typeof av === 'number' ? av - bv : String(av).localeCompare(String(bv));
    return dir === 'asc' ? cmp : -cmp;
  });
}

function SortIcon({ active, dir }: { active: boolean; dir: 'asc' | 'desc' }) {
  return (
    <svg width="10" height="10" viewBox="0 0 10 10" fill="none" style={{ marginLeft: 4, opacity: active ? 1 : 0.3, flexShrink: 0 }}>
      {dir === 'asc' || !active
        ? <path d="M5 2L8 7H2L5 2Z" fill="currentColor" opacity={active && dir === 'asc' ? 1 : 0.4} />
        : null}
      {dir === 'desc' || !active
        ? <path d="M5 8L2 3H8L5 8Z" fill="currentColor" opacity={active && dir === 'desc' ? 1 : 0.4} />
        : null}
    </svg>
  );
}

export function ReportTable({
  columns, rows, formatCell, summaryRow, emptyMessage, onExportCsv,
  defaultSortKey, defaultSortDir = 'desc',
}: ReportTableProps) {
  const [sortKey, setSortKey] = useState<string | null>(defaultSortKey ?? null);
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>(defaultSortDir);

  const handleSort = (key: string) => {
    if (sortKey === key) {
      setSortDir(d => d === 'asc' ? 'desc' : 'asc');
    } else {
      setSortKey(key);
      setSortDir('desc');
    }
  };

  const displayRows = sortKey ? sortRows(rows, sortKey, sortDir) : rows;
  const gridTemplate = columns.map(c => c.width).join(' ');

  const renderCell = (col: ReportColumn, value: any, row: Record<string, any>) => {
    if (formatCell) {
      const custom = formatCell(col.key, value, row);
      if (custom !== undefined) return custom;
    }
    if (value == null) return '—';
    if (typeof value === 'number') {
      return Number.isInteger(value) ? value.toLocaleString() : value.toFixed(1);
    }
    return String(value);
  };

  return (
    <div className="report-table">
      {onExportCsv && (
        <div className="report-table-toolbar">
          <button className="report-csv-btn" onClick={onExportCsv} title="Export CSV">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4M7 10l5 5 5-5M12 15V3" />
            </svg>
            CSV
          </button>
        </div>
      )}

      <div className="report-table-header" style={{ gridTemplateColumns: gridTemplate }}>
        {columns.map(col => (
          <div
            key={col.key}
            className={`report-table-cell ${col.align === 'right' ? 'right' : col.align === 'center' ? 'center' : ''} ${col.sortable ? 'report-th-sortable' : ''} ${sortKey === col.key ? 'report-th-active' : ''}`}
            onClick={col.sortable ? () => handleSort(col.key) : undefined}
            style={{ userSelect: col.sortable ? 'none' : undefined, display: 'flex', alignItems: 'center', justifyContent: col.align === 'right' ? 'flex-end' : col.align === 'center' ? 'center' : 'flex-start', gap: 2 }}
          >
            {col.label}
            {col.sortable && <SortIcon active={sortKey === col.key} dir={sortKey === col.key ? sortDir : 'desc'} />}
          </div>
        ))}
      </div>

      {displayRows.length === 0 ? (
        <div className="report-table-empty">{emptyMessage || 'No data for this period.'}</div>
      ) : (
        <>
          {displayRows.map((row, i) => (
            <div key={i} className="report-table-row" style={{ gridTemplateColumns: gridTemplate }}>
              {columns.map(col => (
                <div
                  key={col.key}
                  className={`report-table-cell ${col.align === 'right' ? 'right' : col.align === 'center' ? 'center' : ''}`}
                >
                  {renderCell(col, row[col.key], row)}
                </div>
              ))}
            </div>
          ))}
          {summaryRow && (
            <div className="report-table-summary" style={{ gridTemplateColumns: gridTemplate }}>
              {columns.map(col => (
                <div
                  key={col.key}
                  className={`report-table-cell ${col.align === 'right' ? 'right' : col.align === 'center' ? 'center' : ''}`}
                >
                  {renderCell(col, summaryRow[col.key], summaryRow)}
                </div>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}
