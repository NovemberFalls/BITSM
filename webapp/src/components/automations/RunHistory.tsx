import { useEffect } from 'react';
import { useAutomationStore } from '../../store/automationStore';

export function RunHistory({ automationId }: { automationId: number }) {
  const { runs, fetchRuns } = useAutomationStore();

  useEffect(() => {
    fetchRuns(automationId);
    const interval = setInterval(() => fetchRuns(automationId), 15000);
    return () => clearInterval(interval);
  }, [automationId]);

  if (runs.length === 0) {
    return (
      <div className="auto-runs-empty">No runs yet. Activate the automation and trigger an event.</div>
    );
  }

  return (
    <div className="auto-runs">
      <table className="auto-runs-table">
        <thead>
          <tr>
            <th>Status</th>
            <th>Ticket</th>
            <th>Actions</th>
            <th>Duration</th>
            <th>Time</th>
          </tr>
        </thead>
        <tbody>
          {runs.map((r) => (
            <tr key={r.id} className={`auto-run-row auto-run-${r.status}`}>
              <td>
                <span className={`auto-run-badge auto-run-badge-${r.status}`}>
                  {r.status}
                </span>
              </td>
              <td>
                {r.ticket_number ? (
                  <span className="auto-run-ticket">#{r.ticket_number}</span>
                ) : '—'}
              </td>
              <td>
                {r.actions_taken?.length > 0 ? (
                  <div className="auto-run-actions">
                    {r.actions_taken.map((a, i) => (
                      <span key={i} className="auto-run-action-tag">
                        {a.subtype}: {a.result}
                      </span>
                    ))}
                  </div>
                ) : (
                  <span className="text-muted">{r.nodes_executed} nodes</span>
                )}
              </td>
              <td>{r.duration_ms != null ? `${r.duration_ms}ms` : '—'}</td>
              <td className="text-muted">
                {new Date(r.started_at).toLocaleString('en-US', {
                  month: 'short', day: 'numeric',
                  hour: '2-digit', minute: '2-digit',
                })}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
