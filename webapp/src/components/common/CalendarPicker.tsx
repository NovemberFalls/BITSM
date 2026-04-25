import { useState, useRef, useEffect } from 'react';

interface CalendarPickerProps {
  value: string;            // ISO date YYYY-MM-DD
  onChange: (date: string) => void;
  placeholder?: string;
}

const DAYS = ['Su', 'Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa'];

function pad(n: number) { return n < 10 ? '0' + n : '' + n; }

function toISO(y: number, m: number, d: number) {
  return `${y}-${pad(m + 1)}-${pad(d)}`;
}

function formatDisplay(iso: string): string {
  if (!iso) return '';
  const [y, m, d] = iso.split('-').map(Number);
  const months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
  return `${months[m - 1]} ${d}, ${y}`;
}

function daysInMonth(year: number, month: number) {
  return new Date(year, month + 1, 0).getDate();
}

function firstDayOfWeek(year: number, month: number) {
  return new Date(year, month, 1).getDay();
}

const MONTH_NAMES = [
  'January','February','March','April','May','June',
  'July','August','September','October','November','December',
];

export function CalendarPicker({ value, onChange, placeholder = 'Select date' }: CalendarPickerProps) {
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  // Viewing month/year (independent of selected value)
  const now = new Date();
  const initial = value ? new Date(value + 'T00:00:00') : now;
  const [viewYear, setViewYear] = useState(initial.getFullYear());
  const [viewMonth, setViewMonth] = useState(initial.getMonth());

  // Sync view to value when it changes externally
  useEffect(() => {
    if (value) {
      const d = new Date(value + 'T00:00:00');
      setViewYear(d.getFullYear());
      setViewMonth(d.getMonth());
    }
  }, [value]);

  // Close on click outside
  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [open]);

  // Close on Escape
  useEffect(() => {
    if (!open) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false);
    };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, [open]);

  const prevMonth = () => {
    if (viewMonth === 0) { setViewMonth(11); setViewYear(viewYear - 1); }
    else setViewMonth(viewMonth - 1);
  };

  const nextMonth = () => {
    if (viewMonth === 11) { setViewMonth(0); setViewYear(viewYear + 1); }
    else setViewMonth(viewMonth + 1);
  };

  const selectDay = (day: number) => {
    onChange(toISO(viewYear, viewMonth, day));
    setOpen(false);
  };

  const todayISO = toISO(now.getFullYear(), now.getMonth(), now.getDate());
  const totalDays = daysInMonth(viewYear, viewMonth);
  const startDay = firstDayOfWeek(viewYear, viewMonth);

  // Build grid cells: leading blanks + day numbers
  const cells: (number | null)[] = [];
  for (let i = 0; i < startDay; i++) cells.push(null);
  for (let d = 1; d <= totalDays; d++) cells.push(d);

  return (
    <div ref={containerRef} style={{ position: 'relative' }}>
      <input
        className="form-input"
        readOnly
        value={value ? formatDisplay(value) : ''}
        placeholder={placeholder}
        onClick={() => setOpen(!open)}
        style={{ cursor: 'pointer' }}
      />

      {open && (
        <div style={{
          position: 'absolute',
          top: '100%',
          left: 0,
          marginTop: 4,
          zIndex: 1000,
          background: 'var(--t-surface)',
          border: '1px solid var(--t-border)',
          borderRadius: 6,
          padding: 10,
          width: 260,
          boxShadow: '0 8px 24px rgba(0,0,0,0.25)',
        }}>
          {/* Month/Year header with nav */}
          <div style={{
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
            marginBottom: 8,
          }}>
            <button
              type="button"
              onClick={prevMonth}
              style={{
                background: 'none',
                border: 'none',
                color: 'var(--t-text-bright)',
                cursor: 'pointer',
                fontSize: 16,
                padding: '2px 6px',
                borderRadius: 4,
                lineHeight: 1,
              }}
            >
              &#8249;
            </button>
            <span style={{
              fontSize: 13,
              fontWeight: 600,
              color: 'var(--t-text-bright)',
            }}>
              {MONTH_NAMES[viewMonth]} {viewYear}
            </span>
            <button
              type="button"
              onClick={nextMonth}
              style={{
                background: 'none',
                border: 'none',
                color: 'var(--t-text-bright)',
                cursor: 'pointer',
                fontSize: 16,
                padding: '2px 6px',
                borderRadius: 4,
                lineHeight: 1,
              }}
            >
              &#8250;
            </button>
          </div>

          {/* Day-of-week header */}
          <div style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(7, 1fr)',
            textAlign: 'center',
            marginBottom: 4,
          }}>
            {DAYS.map((d) => (
              <div key={d} style={{
                fontSize: 10,
                fontWeight: 600,
                color: 'var(--t-text-muted)',
                padding: '2px 0',
              }}>
                {d}
              </div>
            ))}
          </div>

          {/* Day grid */}
          <div style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(7, 1fr)',
            textAlign: 'center',
          }}>
            {cells.map((day, i) => {
              if (day === null) {
                return <div key={`blank-${i}`} />;
              }
              const iso = toISO(viewYear, viewMonth, day);
              const isSelected = iso === value;
              const isToday = iso === todayISO;

              return (
                <button
                  key={day}
                  type="button"
                  onClick={() => selectDay(day)}
                  style={{
                    width: 32,
                    height: 32,
                    margin: '1px auto',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    fontSize: 12,
                    border: 'none',
                    borderRadius: '50%',
                    cursor: 'pointer',
                    fontWeight: isSelected || isToday ? 600 : 400,
                    background: isSelected
                      ? 'var(--c-accent)'
                      : 'transparent',
                    color: isSelected
                      ? '#fff'
                      : isToday
                        ? 'var(--c-accent)'
                        : 'var(--t-text)',
                    outline: isToday && !isSelected
                      ? '1px solid var(--t-border)'
                      : 'none',
                    transition: 'background 0.1s, color 0.1s',
                  }}
                  onMouseEnter={(e) => {
                    if (!isSelected) {
                      (e.target as HTMLElement).style.background = 'var(--t-panel)';
                    }
                  }}
                  onMouseLeave={(e) => {
                    if (!isSelected) {
                      (e.target as HTMLElement).style.background = 'transparent';
                    }
                  }}
                >
                  {day}
                </button>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
