interface DateRangePickerProps {
  startDate: string;
  endDate: string;
  onChange: (start: string, end: string) => void;
}

const PRESETS = [
  { label: '7d', days: 7 },
  { label: '30d', days: 30 },
  { label: '90d', days: 90 },
  { label: 'YTD', days: -1 },
] as const;

function formatDate(d: Date): string {
  return d.toISOString().split('T')[0];
}

function daysAgo(n: number): string {
  const d = new Date();
  d.setDate(d.getDate() - n);
  return formatDate(d);
}

function yearStart(): string {
  return `${new Date().getFullYear()}-01-01`;
}

export function DateRangePicker({ startDate, endDate, onChange }: DateRangePickerProps) {
  const today = formatDate(new Date());

  const handlePreset = (days: number) => {
    if (days === -1) {
      onChange(yearStart(), today);
    } else {
      onChange(daysAgo(days), today);
    }
  };

  const isActivePreset = (days: number): boolean => {
    const expectedStart = days === -1 ? yearStart() : daysAgo(days);
    return startDate === expectedStart && endDate === today;
  };

  return (
    <div className="report-date-picker">
      <input
        type="date"
        className="report-date-input"
        value={startDate}
        max={endDate || today}
        onChange={e => onChange(e.target.value, endDate)}
      />
      <span style={{ color: 'var(--t-text-dim)', fontSize: 12 }}>to</span>
      <input
        type="date"
        className="report-date-input"
        value={endDate}
        min={startDate}
        max={today}
        onChange={e => onChange(startDate, e.target.value)}
      />
      <div className="report-date-presets">
        {PRESETS.map(p => (
          <button
            key={p.label}
            className={`report-date-preset ${isActivePreset(p.days) ? 'active' : ''}`}
            onClick={() => handlePreset(p.days)}
          >
            {p.label}
          </button>
        ))}
      </div>
    </div>
  );
}
