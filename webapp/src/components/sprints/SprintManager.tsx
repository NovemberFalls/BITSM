import { useEffect, useState, useCallback } from 'react';
import { api } from '../../api/client';
import { useUIStore } from '../../store/uiStore';
import { pushUrl, stripSlug } from '../../utils/url';
import { CalendarPicker } from '../common/CalendarPicker';
import { TicketDetail } from '../tickets/TicketDetail';

type SprintView = 'list' | 'board';
type TopTab = 'sprints' | 'tasks';

export function SprintManager() {
  const [topTab, setTopTab] = useState<TopTab>('sprints');
  const [view, setView] = useState<SprintView>('list');
  const [sprints, setSprints] = useState<any[]>([]);
  const [teams, setTeams] = useState<any[]>([]);
  const [teamFilter, setTeamFilter] = useState<number | null>(null);
  const [activeSprintId, setActiveSprintId] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [inSprintDetail, setInSprintDetail] = useState(false);
  const ticketDetailId = useUIStore((s) => s.ticketDetailId);

  // Create sprint state
  const [showCreate, setShowCreate] = useState(false);
  const [newName, setNewName] = useState('');
  const [newGoal, setNewGoal] = useState('');
  const [newTeamId, setNewTeamId] = useState<number | null>(null);
  const [newStart, setNewStart] = useState('');
  const [newEnd, setNewEnd] = useState('');
  const [creating, setCreating] = useState(false);

  const loadSprints = useCallback(async () => {
    setLoading(true);
    try {
      const params: any = {};
      if (teamFilter) params.team_id = teamFilter;
      setSprints(await api.listSprints(params));
    } catch {}
    setLoading(false);
  }, [teamFilter]);

  // On mount, parse sprint ID, tab, and item from URL for deep-linking
  useEffect(() => {
    const stripped = stripSlug(window.location.pathname);

    // /sprints/:id/items/:itemId — item within a sprint board
    const boardItem = stripped.match(/^\/sprints\/(\d+)\/items\/(\d+)$/);
    if (boardItem) {
      setActiveSprintId(parseInt(boardItem[1], 10));
      setView('board');
      setInSprintDetail(true);
      useUIStore.setState({ ticketDetailId: parseInt(boardItem[2], 10) });
      return;
    }

    // /sprints/items/:itemId — item from tasks tab
    const taskItem = stripped.match(/^\/sprints\/items\/(\d+)$/);
    if (taskItem) {
      setTopTab('tasks');
      setInSprintDetail(true);
      useUIStore.setState({ ticketDetailId: parseInt(taskItem[1], 10) });
      return;
    }

    // /sprints/:id — sprint board
    const board = stripped.match(/^\/sprints\/(\d+)$/);
    if (board) {
      setActiveSprintId(parseInt(board[1], 10));
      setView('board');
    }

    // ?tab=tasks
    const params = new URLSearchParams(window.location.search);
    if (params.get('tab') === 'tasks') {
      setTopTab('tasks');
    }
  }, []);

  useEffect(() => {
    api.listTeams().then(setTeams).catch(() => {});
    loadSprints();
  }, [loadSprints]);

  const handleCreate = async () => {
    if (!newName.trim() || !newTeamId) return;
    setCreating(true);
    try {
      await api.createSprint({
        name: newName.trim(),
        team_id: newTeamId,
        goal: newGoal.trim() || undefined,
        start_date: newStart || undefined,
        end_date: newEnd || undefined,
      });
      setNewName(''); setNewGoal(''); setNewStart(''); setNewEnd('');
      setShowCreate(false);
      await loadSprints();
    } catch {}
    setCreating(false);
  };

  const handleStatusChange = async (id: number, status: string) => {
    await api.updateSprint(id, { status });
    await loadSprints();
  };

  const handleDelete = async (id: number) => {
    if (!confirm('Delete this sprint? Tickets will be moved to backlog.')) return;
    await api.deleteSprint(id);
    if (activeSprintId === id) setActiveSprintId(null);
    await loadSprints();
  };

  const openBoard = (id: number) => {
    setActiveSprintId(id);
    setView('board');
    pushUrl('/sprints/' + id);
  };

  const openItem = (id: number) => {
    setInSprintDetail(true);
    useUIStore.setState({ ticketDetailId: id });
    // Push URL based on context: sprint board or tasks tab
    if (activeSprintId) {
      pushUrl(`/sprints/${activeSprintId}/items/${id}`);
    } else {
      pushUrl(`/sprints/items/${id}`);
    }
  };

  const closeItem = () => {
    setInSprintDetail(false);
    useUIStore.setState({ ticketDetailId: null });
    // Navigate back to the correct sprint context
    if (activeSprintId) {
      pushUrl(`/sprints/${activeSprintId}`);
    } else {
      pushUrl('/sprints', 'tab=tasks');
    }
  };

  // Sync: if ticketDetailId is cleared externally (e.g. Escape key), exit sprint detail mode
  useEffect(() => {
    if (!ticketDetailId) setInSprintDetail(false);
  }, [ticketDetailId]);

  // Listen for browser back/forward to restore sprint item state
  useEffect(() => {
    const onPopState = () => {
      const stripped = stripSlug(window.location.pathname);

      // Check for sprint item URLs
      const boardItem = stripped.match(/^\/sprints\/(\d+)\/items\/(\d+)$/);
      if (boardItem) {
        setActiveSprintId(parseInt(boardItem[1], 10));
        setView('board');
        setInSprintDetail(true);
        useUIStore.setState({ ticketDetailId: parseInt(boardItem[2], 10) });
        return;
      }
      const taskItem = stripped.match(/^\/sprints\/items\/(\d+)$/);
      if (taskItem) {
        setTopTab('tasks');
        setInSprintDetail(true);
        useUIStore.setState({ ticketDetailId: parseInt(taskItem[1], 10) });
        return;
      }

      // Sprint board (no item)
      const board = stripped.match(/^\/sprints\/(\d+)$/);
      if (board) {
        setActiveSprintId(parseInt(board[1], 10));
        setView('board');
        setInSprintDetail(false);
        useUIStore.setState({ ticketDetailId: null });
        return;
      }

      // Sprint list or tasks tab
      setInSprintDetail(false);
      useUIStore.setState({ ticketDetailId: null });
      const params = new URLSearchParams(window.location.search);
      if (params.get('tab') === 'tasks') {
        setTopTab('tasks');
      } else {
        setTopTab('sprints');
        setView('list');
        setActiveSprintId(null);
      }
    };
    window.addEventListener('popstate', onPopState);
    return () => window.removeEventListener('popstate', onPopState);
  }, []);

  // When an item is selected, show TicketDetail inline (no URL change, stays in sprint context)
  if (inSprintDetail && ticketDetailId) {
    return <TicketDetail onClose={closeItem} />;
  }

  if (view === 'board' && activeSprintId) {
    return <SprintBoard sprintId={activeSprintId} onBack={() => { setView('list'); setActiveSprintId(null); pushUrl('/sprints'); }} onOpenItem={openItem} />;
  }

  return (
    <div>
      {/* Top-level tab bar: Sprints | Tasks */}
      <div style={{ display: 'flex', gap: 0, borderBottom: '1px solid var(--t-border)', marginBottom: 16 }}>
        {(['sprints', 'tasks'] as const).map(tab => (
          <button
            key={tab}
            className="btn btn-ghost"
            style={{
              fontSize: 14,
              padding: '8px 20px',
              borderBottom: topTab === tab ? '2px solid var(--c-accent)' : '2px solid transparent',
              borderRadius: 0,
              color: topTab === tab ? 'var(--t-text-bright)' : 'var(--t-text-muted)',
              fontWeight: topTab === tab ? 600 : 400,
            }}
            onClick={() => { setTopTab(tab); pushUrl('/sprints', tab === 'tasks' ? 'tab=tasks' : ''); }}
          >
            {tab === 'sprints' ? 'Sprints' : 'Tasks'}
          </button>
        ))}
      </div>

      {/* Tasks tab — standalone task repository */}
      {topTab === 'tasks' && <TaskPool sprints={sprints} onOpenItem={openItem} />}

      {/* Sprints tab — existing sprint list */}
      {topTab === 'sprints' && (
        <>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
            <div style={{ display: 'flex', gap: 8 }}>
              {teams.length > 0 && (
                <select
                  className="form-input form-select"
                  style={{ width: 160, height: 30, fontSize: 12 }}
                  value={teamFilter ?? ''}
                  onChange={(e) => setTeamFilter(e.target.value ? Number(e.target.value) : null)}
                >
                  <option value="">All Teams</option>
                  {teams.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
                </select>
              )}
              <button className="btn btn-primary btn-sm" onClick={() => setShowCreate(true)}>+ New Sprint</button>
            </div>
          </div>

          {showCreate && (
            <div className="card" style={{ padding: 16, marginBottom: 16 }}>
              <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
                <div className="form-group" style={{ flex: 2, minWidth: 200 }}>
                  <label className="form-label">Sprint Name</label>
                  <input className="form-input" value={newName} onChange={(e) => setNewName(e.target.value)} placeholder="e.g. Sprint 12" autoFocus />
                </div>
                <div className="form-group" style={{ flex: 1, minWidth: 150 }}>
                  <label className="form-label">Team</label>
                  <select className="form-input form-select" value={newTeamId ?? ''} onChange={(e) => setNewTeamId(e.target.value ? Number(e.target.value) : null)}>
                    <option value="">Select team...</option>
                    {teams.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
                  </select>
                </div>
                <div className="form-group" style={{ flex: 1, minWidth: 130 }}>
                  <label className="form-label">Start</label>
                  <CalendarPicker value={newStart} onChange={setNewStart} placeholder="Start date" />
                </div>
                <div className="form-group" style={{ flex: 1, minWidth: 130 }}>
                  <label className="form-label">End</label>
                  <CalendarPicker value={newEnd} onChange={setNewEnd} placeholder="End date" />
                </div>
              </div>
              <div className="form-group">
                <label className="form-label">Goal (optional)</label>
                <input className="form-input" value={newGoal} onChange={(e) => setNewGoal(e.target.value)} placeholder="What should this sprint accomplish?" />
              </div>
              <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
                <button className="btn btn-ghost btn-sm" onClick={() => setShowCreate(false)}>Cancel</button>
                <button className="btn btn-primary btn-sm" onClick={handleCreate} disabled={creating || !newName.trim() || !newTeamId}>
                  {creating ? 'Creating...' : 'Create Sprint'}
                </button>
              </div>
            </div>
          )}

          {loading ? (
            <div className="empty-state"><div className="empty-state-text">Loading sprints...</div></div>
          ) : sprints.length === 0 ? (
            <div className="empty-state">
              <div className="empty-state-icon">&#x1F3C3;</div>
              <div className="empty-state-title">No sprints yet</div>
              <div className="empty-state-text">Create a sprint to start organizing dev work into time-boxed iterations.</div>
            </div>
          ) : (
            <div className="kb-doc-list">
              {sprints.map((s) => (
                <div key={s.id} className="kb-doc-card" style={{ cursor: 'pointer' }} onClick={() => openBoard(s.id)}>
                  <div className="kb-doc-header">
                    <div className="kb-doc-info" style={{ gap: 8 }}>
                      <span className={`badge ${s.status === 'active' ? 'badge-open' : s.status === 'completed' ? 'badge-resolved' : 'badge-pending'}`}>
                        {s.status}
                      </span>
                      <span className="kb-doc-title">{s.name}</span>
                      <span style={{ fontSize: 11, color: 'var(--t-text-dim)' }}>{s.team_name}</span>
                    </div>
                    <div className="kb-doc-meta" style={{ gap: 10 }}>
                      <span style={{ fontSize: 11 }}>{s.ticket_count} ticket{s.ticket_count !== 1 ? 's' : ''}</span>
                      <span style={{ fontSize: 11 }}>{s.completed_points}/{s.total_points} pts</span>
                      {s.start_date && <span style={{ fontSize: 11, color: 'var(--t-text-dim)' }}>{s.start_date} - {s.end_date || '?'}</span>}
                      <select
                        className="form-input form-select"
                        style={{ width: 100, height: 24, fontSize: 10, padding: '0 4px' }}
                        value={s.status}
                        onClick={(e) => e.stopPropagation()}
                        onChange={(e) => { e.stopPropagation(); handleStatusChange(s.id, e.target.value); }}
                      >
                        <option value="planning">Planning</option>
                        <option value="active">Active</option>
                        <option value="completed">Completed</option>
                      </select>
                      <button
                        className="btn btn-sm btn-danger"
                        style={{ padding: '2px 6px', fontSize: 11 }}
                        onClick={(e) => { e.stopPropagation(); handleDelete(s.id); }}
                      >&times;</button>
                    </div>
                  </div>
                  {s.goal && <div style={{ fontSize: 12, color: 'var(--t-text-muted)', padding: '0 14px 10px' }}>{s.goal}</div>}
                </div>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}


/* ============================================================
   SPRINT BOARD — kanban by status with tabs
   ============================================================ */
function SprintBoard({ sprintId, onBack, onOpenItem }: { sprintId: number; onBack: () => void; onOpenItem: (id: number) => void }) {
  const [data, setData] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [boardTab, setBoardTab] = useState<'board' | 'timeline' | 'velocity'>('board');
  const [showCreateItem, setShowCreateItem] = useState(false);
  const [showBacklog, setShowBacklog] = useState(false);
  const [editingPoints, setEditingPoints] = useState<number | null>(null);
  const [editPointsVal, setEditPointsVal] = useState('');
  const [workItemTypes, setWorkItemTypes] = useState<any[]>([]);
  const [dragTicketId, setDragTicketId] = useState<number | null>(null);
  const [dragOverCol, setDragOverCol] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try { setData(await api.getSprintBoard(sprintId)); } catch {}
    setLoading(false);
  }, [sprintId]);

  useEffect(() => { load(); }, [load]);
  useEffect(() => { api.listWorkItemTypes().then(setWorkItemTypes).catch(() => {}); }, []);

  const handleStatusChange = async (ticketId: number, newStatus: string) => {
    try {
      await fetch(`/api/tickets/${ticketId}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ status: newStatus }),
      });
      await load();
    } catch {}
  };

  const handleRemoveItem = async (ticketId: number) => {
    try {
      await api.removeSprintItem(sprintId, ticketId);
      await load();
    } catch {}
  };

  const handleSavePoints = async (ticketId: number) => {
    const val = editPointsVal.trim();
    const pts = val === '' ? null : parseInt(val, 10);
    if (val !== '' && isNaN(pts as number)) { setEditingPoints(null); return; }
    try {
      await fetch(`/api/tickets/${ticketId}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ story_points: pts }),
      });
      setEditingPoints(null);
      await load();
    } catch { setEditingPoints(null); }
  };

  if (loading || !data) {
    return <div className="empty-state"><div className="empty-state-text">Loading sprint board...</div></div>;
  }

  const { sprint, workflow, columns, total_points, completed_points } = data;
  const pct = total_points > 0 ? Math.round((completed_points / total_points) * 100) : 0;

  return (
    <div>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 16 }}>
        <button className="btn btn-ghost btn-sm" onClick={onBack}>&larr; Back</button>
        <h2 style={{ fontSize: 18, fontWeight: 600, margin: 0, color: 'var(--t-text-bright)' }}>{sprint.name}</h2>
        <span className={`badge ${sprint.status === 'active' ? 'badge-open' : sprint.status === 'completed' ? 'badge-resolved' : 'badge-pending'}`}>
          {sprint.status}
        </span>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', fontSize: 12, color: 'var(--t-text-dim)', marginLeft: 'auto' }}>
          <span style={{ color: 'var(--c-success)', fontWeight: 600 }}>{completed_points}</span>
          <span>/</span>
          <span style={{ fontWeight: 600, color: 'var(--t-text-bright)' }}>{total_points}</span>
          <span>pts</span>
          {total_points > 0 && (
            <span style={{ background: 'var(--t-panel)', padding: '1px 6px', borderRadius: 3, fontSize: 11 }}>
              {pct}%
            </span>
          )}
          {total_points - completed_points > 0 && (
            <span style={{ fontSize: 10, color: 'var(--t-text-muted)' }}>
              ({total_points - completed_points} remaining)
            </span>
          )}
        </div>
      </div>

      {/* Progress bar */}
      <div style={{ height: 4, background: 'var(--t-border)', borderRadius: 2, marginBottom: 12, overflow: 'hidden' }}>
        <div style={{ height: '100%', width: `${pct}%`, background: 'var(--c-success)', borderRadius: 2, transition: 'width 0.3s' }} />
      </div>

      {/* Tab bar */}
      <div style={{ display: 'flex', gap: 0, borderBottom: '1px solid var(--t-border)', marginBottom: 16 }}>
        {(['board', 'timeline', 'velocity'] as const).map(tab => (
          <button
            key={tab}
            className="btn btn-ghost"
            style={{
              fontSize: 12,
              padding: '6px 16px',
              borderBottom: boardTab === tab ? '2px solid var(--c-accent)' : '2px solid transparent',
              borderRadius: 0,
              color: boardTab === tab ? 'var(--t-text-bright)' : 'var(--t-text-muted)',
              fontWeight: boardTab === tab ? 600 : 400,
            }}
            onClick={() => setBoardTab(tab)}
          >
            {tab.charAt(0).toUpperCase() + tab.slice(1)}
          </button>
        ))}
      </div>

      {/* Board tab content */}
      {boardTab === 'board' && (
        <>
          {/* Action buttons */}
          <div style={{ display: 'flex', gap: 8, marginBottom: 12 }}>
            <button className="btn btn-primary btn-sm" onClick={() => { setShowCreateItem(true); setShowBacklog(false); }}>
              + Create Item
            </button>
            <button className="btn btn-ghost btn-sm" onClick={() => { setShowBacklog(!showBacklog); setShowCreateItem(false); }}>
              {showBacklog ? 'Hide Backlog' : '+ Add from Backlog'}
            </button>
          </div>

          {/* Create item form */}
          {showCreateItem && (
            <CreateItemForm
              sprintId={sprintId}
              workItemTypes={workItemTypes}
              onCreated={() => { setShowCreateItem(false); load(); }}
              onCancel={() => setShowCreateItem(false)}
            />
          )}

          {/* Backlog picker */}
          {showBacklog && (
            <BacklogPicker
              sprintId={sprintId}
              onAdded={() => { setShowBacklog(false); load(); }}
            />
          )}

          {/* Kanban columns */}
          <div style={{ display: 'flex', gap: 12, overflowX: 'auto', paddingBottom: 20 }}>
            {workflow.map((ws: any) => {
              const cards = columns[ws.key] || [];
              return (
                <div
                  key={ws.key}
                  style={{
                    minWidth: 200,
                    flex: 1,
                    borderRadius: 6,
                    border: dragOverCol === ws.key ? '2px dashed var(--c-accent)' : '2px dashed transparent',
                    background: dragOverCol === ws.key ? 'var(--t-accent-bg)' : 'transparent',
                    transition: 'background 0.15s, border-color 0.15s',
                    padding: 4,
                  }}
                  onDragOver={(e) => { e.preventDefault(); setDragOverCol(ws.key); }}
                  onDragLeave={() => setDragOverCol(null)}
                  onDrop={(e) => {
                    e.preventDefault();
                    setDragOverCol(null);
                    if (dragTicketId != null) {
                      // Find the card's current status to avoid no-op API calls
                      const currentStatus = Object.entries(columns).find(([, items]) =>
                        (items as any[]).some((item: any) => item.id === dragTicketId)
                      )?.[0];
                      if (currentStatus !== ws.key) {
                        handleStatusChange(dragTicketId, ws.key);
                      }
                    }
                    setDragTicketId(null);
                  }}
                >
                  <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--t-text-muted)', textTransform: 'uppercase', marginBottom: 8, letterSpacing: '0.03em' }}>
                    {ws.label} <span style={{ fontWeight: 400, color: 'var(--t-text-dim)' }}>({cards.length})</span>
                  </div>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                    {cards.map((t: any) => (
                      <div
                        key={t.id}
                        className="card"
                        draggable
                        onDragStart={(e) => { setDragTicketId(t.id); e.dataTransfer.effectAllowed = 'move'; }}
                        onDragEnd={() => { setDragTicketId(null); setDragOverCol(null); }}
                        style={{
                          padding: '8px 10px',
                          fontSize: 12,
                          cursor: dragTicketId ? 'grabbing' : 'grab',
                          opacity: dragTicketId === t.id ? 0.5 : 1,
                        }}
                        onClick={() => onOpenItem(t.id)}>
                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4 }}>
                          <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                            <span style={{ fontSize: 10, color: 'var(--t-text-dim)' }}>{t.work_item_number || t.ticket_number}</span>
                            {t.work_item_type_icon && (
                              <span style={{ fontSize: 10 }} title={t.work_item_type_name}>
                                {t.work_item_type_icon}
                              </span>
                            )}
                          </div>
                          <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                            <span className={`badge badge-${t.priority}`} style={{ fontSize: 9, padding: '0 4px' }}>{t.priority?.toUpperCase()}</span>
                            <button
                              className="btn btn-ghost"
                              style={{ fontSize: 9, padding: '0 3px', color: 'var(--t-text-dim)' }}
                              title="Remove from sprint"
                              onClick={(e) => { e.stopPropagation(); handleRemoveItem(t.id); }}
                            >&times;</button>
                          </div>
                        </div>
                        <div style={{ fontWeight: 500, marginBottom: 4 }}>{t.subject}</div>
                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                          <span style={{ fontSize: 10, color: 'var(--t-text-dim)' }}>{t.assignee_name || 'Unassigned'}</span>
                          {editingPoints === t.id ? (
                            <input
                              className="form-input"
                              type="number"
                              min="0"
                              style={{ width: 40, height: 20, fontSize: 10, padding: '0 4px', textAlign: 'center' }}
                              value={editPointsVal}
                              autoFocus
                              onClick={(e) => e.stopPropagation()}
                              onChange={(e) => setEditPointsVal(e.target.value)}
                              onBlur={() => handleSavePoints(t.id)}
                              onKeyDown={(e) => { if (e.key === 'Enter') handleSavePoints(t.id); if (e.key === 'Escape') setEditingPoints(null); }}
                            />
                          ) : t.story_points != null ? (
                            <span
                              style={{ fontSize: 10, fontWeight: 600, background: 'var(--t-panel)', padding: '1px 5px', borderRadius: 3, cursor: 'pointer' }}
                              title="Click to edit"
                              onClick={(e) => { e.stopPropagation(); setEditingPoints(t.id); setEditPointsVal(String(t.story_points)); }}
                            >
                              {t.story_points} pt{t.story_points !== 1 ? 's' : ''}
                            </span>
                          ) : (
                            <button
                              className="btn btn-ghost"
                              style={{ fontSize: 9, padding: '0 4px', color: 'var(--t-text-dim)' }}
                              onClick={(e) => { e.stopPropagation(); setEditingPoints(t.id); setEditPointsVal(''); }}
                            >+ pts</button>
                          )}
                        </div>
                        {/* Status move buttons */}
                        <div style={{ display: 'flex', gap: 4, marginTop: 6, flexWrap: 'wrap' }}>
                          {workflow.filter((w: any) => w.key !== t.status).slice(0, 3).map((w: any) => (
                            <button
                              key={w.key}
                              className="btn btn-ghost"
                              style={{ fontSize: 9, padding: '1px 5px', lineHeight: 1.3 }}
                              onClick={(e) => { e.stopPropagation(); handleStatusChange(t.id, w.key); }}
                            >
                              &rarr; {w.label}
                            </button>
                          ))}
                        </div>
                      </div>
                    ))}
                    {cards.length === 0 && (
                      <div style={{ fontSize: 11, color: 'var(--t-text-dim)', fontStyle: 'italic', padding: '8px 0' }}>Empty</div>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        </>
      )}

      {/* Timeline tab */}
      {boardTab === 'timeline' && (
        <TimelineView sprintId={sprintId} />
      )}

      {/* Velocity tab */}
      {boardTab === 'velocity' && (
        <VelocityView teamId={sprint.team_id} />
      )}
    </div>
  );
}


/* ============================================================
   CREATE ITEM FORM — inline card form for creating tasks
   ============================================================ */
function CreateItemForm({
  sprintId,
  workItemTypes,
  onCreated,
  onCancel,
}: {
  sprintId?: number | null;
  workItemTypes: any[];
  onCreated: () => void;
  onCancel: () => void;
}) {
  const [subject, setSubject] = useState('');
  const [workItemTypeId, setWorkItemTypeId] = useState<number | ''>('');
  const [storyPoints, setStoryPoints] = useState('');
  const [priority, setPriority] = useState('p3');
  const [assignee, setAssignee] = useState('');
  const [creating, setCreating] = useState(false);

  const handleCreate = async () => {
    if (!subject.trim()) return;
    setCreating(true);
    try {
      const payload: any = {
        subject: subject.trim(),
        ticket_type: 'task',
        priority,
      };
      if (sprintId) payload.sprint_id = sprintId;
      if (storyPoints) payload.story_points = parseInt(storyPoints, 10);
      if (workItemTypeId) payload.work_item_type_id = workItemTypeId;
      if (assignee.trim()) payload.assignee_name = assignee.trim();
      await api.createTicket(payload);
      onCreated();
    } catch {}
    setCreating(false);
  };

  return (
    <div className="card" style={{ padding: 16, marginBottom: 16 }}>
      <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--t-text-bright)', marginBottom: 12 }}>{sprintId ? 'Create Sprint Item' : 'Create Task'}</div>
      <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
        <div className="form-group" style={{ flex: 3, minWidth: 200 }}>
          <label className="form-label">Title</label>
          <input
            className="form-input"
            value={subject}
            onChange={(e) => setSubject(e.target.value)}
            placeholder="What needs to be done?"
            autoFocus
          />
        </div>
        <div className="form-group" style={{ flex: 1, minWidth: 120 }}>
          <label className="form-label">Type</label>
          <select
            className="form-input form-select"
            value={workItemTypeId}
            onChange={(e) => setWorkItemTypeId(e.target.value ? Number(e.target.value) : '')}
          >
            <option value="">None</option>
            {workItemTypes.map((t) => (
              <option key={t.id} value={t.id}>{t.icon ? t.icon + ' ' : ''}{t.name}</option>
            ))}
          </select>
        </div>
        <div className="form-group" style={{ flex: 0, minWidth: 80 }}>
          <label className="form-label">Points</label>
          <input
            className="form-input"
            type="number"
            min="0"
            value={storyPoints}
            onChange={(e) => setStoryPoints(e.target.value)}
            placeholder="0"
            style={{ width: 80 }}
          />
        </div>
        <div className="form-group" style={{ flex: 1, minWidth: 100 }}>
          <label className="form-label">Priority</label>
          <select
            className="form-input form-select"
            value={priority}
            onChange={(e) => setPriority(e.target.value)}
          >
            <option value="p1">P1 - Urgent</option>
            <option value="p2">P2 - High</option>
            <option value="p3">P3 - Medium</option>
            <option value="p4">P4 - Low</option>
          </select>
        </div>
        <div className="form-group" style={{ flex: 1, minWidth: 140 }}>
          <label className="form-label">Assignee</label>
          <input
            className="form-input"
            value={assignee}
            onChange={(e) => setAssignee(e.target.value)}
            placeholder="Name (optional)"
          />
        </div>
      </div>
      <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', marginTop: 8 }}>
        <button className="btn btn-ghost btn-sm" onClick={onCancel}>Cancel</button>
        <button className="btn btn-primary btn-sm" onClick={handleCreate} disabled={creating || !subject.trim()}>
          {creating ? 'Creating...' : 'Create Item'}
        </button>
      </div>
    </div>
  );
}


/* ============================================================
   BACKLOG PICKER — select tickets to add to sprint
   ============================================================ */
function BacklogPicker({
  sprintId,
  onAdded,
}: {
  sprintId: number;
  onAdded: () => void;
}) {
  const [tickets, setTickets] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');
  const [teamOnly, setTeamOnly] = useState(true);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [adding, setAdding] = useState(false);

  const loadBacklog = useCallback(async () => {
    setLoading(true);
    try {
      setTickets(await api.getSprintBacklog(sprintId, { team_only: teamOnly, search: search || undefined }));
    } catch {}
    setLoading(false);
  }, [sprintId, teamOnly, search]);

  useEffect(() => {
    const timer = setTimeout(loadBacklog, search ? 300 : 0);
    return () => clearTimeout(timer);
  }, [loadBacklog, search]);

  const toggleSelect = (id: number) => {
    setSelected(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  };

  const handleAdd = async () => {
    if (selected.size === 0) return;
    setAdding(true);
    try {
      await api.addSprintItems(sprintId, Array.from(selected));
      setSelected(new Set());
      onAdded();
    } catch {}
    setAdding(false);
  };

  return (
    <div className="card" style={{ padding: 16, marginBottom: 16 }}>
      <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--t-text-bright)', marginBottom: 12 }}>Add from Backlog</div>
      <div style={{ display: 'flex', gap: 12, alignItems: 'center', marginBottom: 12 }}>
        <input
          className="form-input"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search tickets..."
          style={{ flex: 1, maxWidth: 300 }}
        />
        <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, color: 'var(--t-text-muted)', cursor: 'pointer' }}>
          <input
            type="checkbox"
            checked={teamOnly}
            onChange={(e) => setTeamOnly(e.target.checked)}
          />
          Team only
        </label>
        {selected.size > 0 && (
          <button className="btn btn-primary btn-sm" onClick={handleAdd} disabled={adding}>
            {adding ? 'Adding...' : `Add ${selected.size} Selected`}
          </button>
        )}
      </div>
      <div style={{ maxHeight: 300, overflowY: 'auto', border: '1px solid var(--t-border)', borderRadius: 6 }}>
        {loading ? (
          <div style={{ padding: 16, textAlign: 'center', fontSize: 12, color: 'var(--t-text-dim)' }}>Loading...</div>
        ) : tickets.length === 0 ? (
          <div style={{ padding: 16, textAlign: 'center', fontSize: 12, color: 'var(--t-text-dim)' }}>No available tickets found.</div>
        ) : (
          tickets.map((t) => (
            <div
              key={t.id}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 10,
                padding: '8px 12px',
                borderBottom: '1px solid var(--t-border)',
                cursor: 'pointer',
                background: selected.has(t.id) ? 'var(--t-panel)' : 'transparent',
              }}
              onClick={() => toggleSelect(t.id)}
            >
              <input
                type="checkbox"
                checked={selected.has(t.id)}
                onChange={() => toggleSelect(t.id)}
                onClick={(e) => e.stopPropagation()}
              />
              <span style={{ fontSize: 10, color: 'var(--t-text-dim)', minWidth: 50 }}>{t.ticket_number}</span>
              <span style={{ fontSize: 12, flex: 1, color: 'var(--t-text-bright)' }}>{t.subject}</span>
              <span className={`badge badge-${t.priority}`} style={{ fontSize: 9, padding: '0 4px' }}>{t.priority?.toUpperCase()}</span>
              {t.story_points != null && (
                <span style={{ fontSize: 10, fontWeight: 600, background: 'var(--t-panel)', padding: '1px 5px', borderRadius: 3 }}>
                  {t.story_points} pts
                </span>
              )}
              <span style={{ fontSize: 10, color: 'var(--t-text-dim)' }}>{t.assignee_name || ''}</span>
            </div>
          ))
        )}
      </div>
    </div>
  );
}


/* ============================================================
   TIMELINE VIEW — completed items timeline
   ============================================================ */
function TimelineView({ sprintId }: { sprintId: number }) {
  const [items, setItems] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    api.getSprintTimeline(sprintId)
      .then(setItems)
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [sprintId]);

  if (loading) {
    return <div style={{ padding: 20, textAlign: 'center', fontSize: 12, color: 'var(--t-text-dim)' }}>Loading timeline...</div>;
  }

  if (items.length === 0) {
    return (
      <div className="empty-state" style={{ padding: 40 }}>
        <div className="empty-state-text">No deliveries yet in this sprint.</div>
      </div>
    );
  }

  const formatDate = (d: string) => {
    if (!d) return '';
    try {
      const dt = new Date(d);
      return dt.toLocaleDateString('en-US', { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
    } catch { return d; }
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 0 }}>
      {items.map((item, i) => (
        <div
          key={item.id}
          style={{
            display: 'flex',
            gap: 16,
            paddingLeft: 16,
            borderLeft: '2px solid var(--c-accent)',
            paddingBottom: i < items.length - 1 ? 12 : 0,
            paddingTop: i === 0 ? 0 : 0,
          }}
        >
          <div style={{ flex: 1 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
              <span style={{ fontSize: 11, color: 'var(--t-text-dim)' }}>{formatDate(item.completed_at)}</span>
              {item.work_item_type_icon && (
                <span
                  style={{
                    fontSize: 10,
                    padding: '1px 6px',
                    borderRadius: 3,
                    background: item.work_item_type_color ? item.work_item_type_color + '22' : 'var(--t-panel)',
                    color: item.work_item_type_color || 'var(--t-text-muted)',
                  }}
                >
                  {item.work_item_type_icon} {item.work_item_type_name}
                </span>
              )}
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 2 }}>
              <span style={{ fontSize: 10, color: 'var(--t-text-dim)' }}>{item.ticket_number}</span>
              <span style={{ fontSize: 13, fontWeight: 500, color: 'var(--t-text-bright)' }}>{item.subject}</span>
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, fontSize: 11, color: 'var(--t-text-muted)' }}>
              {item.story_points != null && (
                <span style={{ fontWeight: 600 }}>{item.story_points} pt{item.story_points !== 1 ? 's' : ''}</span>
              )}
              {item.completed_by_name && (
                <span>Delivered by {item.completed_by_name}</span>
              )}
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}


/* ============================================================
   VELOCITY VIEW — team and person averages
   ============================================================ */
function VelocityView({ teamId }: { teamId?: number }) {
  const [data, setData] = useState<{ team_avg: number; sprint_count: number; person_averages: any[] } | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    api.getVelocityAverages(teamId ? { team_id: teamId } : undefined)
      .then(setData)
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [teamId]);

  if (loading) {
    return <div style={{ padding: 20, textAlign: 'center', fontSize: 12, color: 'var(--t-text-dim)' }}>Loading velocity data...</div>;
  }

  if (!data || data.sprint_count === 0) {
    return (
      <div className="empty-state" style={{ padding: 40 }}>
        <div className="empty-state-text">No completed sprints yet. Velocity data will appear after sprints are marked as completed.</div>
      </div>
    );
  }

  return (
    <div>
      {/* Team stats card */}
      <div className="card" style={{ padding: 20, marginBottom: 20, display: 'flex', gap: 32, alignItems: 'center' }}>
        <div>
          <div style={{ fontSize: 11, color: 'var(--t-text-muted)', textTransform: 'uppercase', letterSpacing: '0.03em', marginBottom: 4 }}>Team Average</div>
          <div style={{ fontSize: 28, fontWeight: 700, color: 'var(--t-text-bright)' }}>{data.team_avg} <span style={{ fontSize: 14, fontWeight: 400, color: 'var(--t-text-muted)' }}>pts/sprint</span></div>
        </div>
        <div>
          <div style={{ fontSize: 11, color: 'var(--t-text-muted)', textTransform: 'uppercase', letterSpacing: '0.03em', marginBottom: 4 }}>Completed Sprints</div>
          <div style={{ fontSize: 28, fontWeight: 700, color: 'var(--t-text-bright)' }}>{data.sprint_count}</div>
        </div>
      </div>

      {/* Person leaderboard */}
      {data.person_averages.length > 0 && (
        <div>
          <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--t-text-bright)', marginBottom: 12 }}>Individual Velocity</div>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
            <thead>
              <tr style={{ borderBottom: '1px solid var(--t-border)' }}>
                <th style={{ textAlign: 'left', padding: '8px 12px', color: 'var(--t-text-muted)', fontWeight: 600, fontSize: 11 }}>Name</th>
                <th style={{ textAlign: 'right', padding: '8px 12px', color: 'var(--t-text-muted)', fontWeight: 600, fontSize: 11 }}>Avg Points</th>
                <th style={{ textAlign: 'right', padding: '8px 12px', color: 'var(--t-text-muted)', fontWeight: 600, fontSize: 11 }}>Total Points</th>
                <th style={{ textAlign: 'right', padding: '8px 12px', color: 'var(--t-text-muted)', fontWeight: 600, fontSize: 11 }}>Sprints</th>
              </tr>
            </thead>
            <tbody>
              {data.person_averages.map((p: any) => (
                <tr key={p.user_id} style={{ borderBottom: '1px solid var(--t-border)' }}>
                  <td style={{ padding: '8px 12px', color: 'var(--t-text-bright)' }}>{p.name}</td>
                  <td style={{ padding: '8px 12px', textAlign: 'right', fontWeight: 600, color: 'var(--c-accent)' }}>{p.avg_points}</td>
                  <td style={{ padding: '8px 12px', textAlign: 'right', color: 'var(--t-text-muted)' }}>{p.total_points}</td>
                  <td style={{ padding: '8px 12px', textAlign: 'right', color: 'var(--t-text-muted)' }}>{p.sprint_count}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}


/* ============================================================
   TASK POOL — standalone task repository
   ============================================================ */
function TaskPool({ sprints, onOpenItem }: { sprints: any[]; onOpenItem: (id: number) => void }) {
  const [tasks, setTasks] = useState<any[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [showCreate, setShowCreate] = useState(false);
  const [workItemTypes, setWorkItemTypes] = useState<any[]>([]);
  const [viewMode, setViewMode] = useState<'list' | 'tree'>('list');
  const [treeData, setTreeData] = useState<any[]>([]);
  const [expandedNodes, setExpandedNodes] = useState<Set<number>>(new Set());
  const [childrenCache, setChildrenCache] = useState<Record<number, any[]>>({});

  // Filters
  const [search, setSearch] = useState('');
  const [typeFilter, setTypeFilter] = useState('');
  const [statusFilter, setStatusFilter] = useState('');
  const [sprintFilter, setSprintFilter] = useState('');
  const [priorityFilter, setPriorityFilter] = useState('');

  // Inline sprint assignment
  const [assigningSprint, setAssigningSprint] = useState<number | null>(null);

  useEffect(() => { api.listWorkItemTypes().then(setWorkItemTypes).catch(() => {}); }, []);

  const loadTasks = useCallback(async () => {
    setLoading(true);
    try {
      const params: Record<string, string> = { ticket_type: typeFilter || 'task,bug,feature' };
      if (search) params.search = search;
      if (statusFilter) params.status = statusFilter;
      if (priorityFilter) params.priority = priorityFilter;
      if (sprintFilter === 'none') params.sprint_id = 'null';
      else if (sprintFilter) params.sprint_id = sprintFilter;
      params.limit = '100';
      params.sort_by = 'created_at';
      params.sort_dir = 'desc';
      const res = await api.listTickets(params);
      setTasks(res.tickets);
      setTotal(res.total);
    } catch {}
    setLoading(false);
  }, [search, typeFilter, statusFilter, sprintFilter, priorityFilter]);

  useEffect(() => {
    const timer = setTimeout(loadTasks, search ? 300 : 0);
    return () => clearTimeout(timer);
  }, [loadTasks, search]);

  const handleAssignSprint = async (ticketId: number, sprintId: number | null) => {
    try {
      await fetch(`/api/tickets/${ticketId}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ sprint_id: sprintId }),
      });
      setAssigningSprint(null);
      await loadTasks();
    } catch {}
  };

  const handleUpdatePoints = async (ticketId: number, points: string) => {
    const val = points.trim();
    const pts = val === '' ? null : parseInt(val, 10);
    if (val !== '' && isNaN(pts as number)) return;
    try {
      await fetch(`/api/tickets/${ticketId}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ story_points: pts }),
      });
      await loadTasks();
    } catch {}
  };

  const activeSprints = sprints.filter(s => s.status !== 'completed');

  // Tree view: load epics/stories as top-level nodes
  const loadTreeData = useCallback(async () => {
    try {
      const params: Record<string, string> = {
        ticket_type: 'task,bug,feature',
        limit: '200',
        sort_by: 'created_at',
        sort_dir: 'desc',
      };
      const res = await api.listTickets(params);
      const items = res.tickets || [];
      // Top-level = items with no parent_id
      const roots = items.filter((t: any) => !t.parent_id);
      setTreeData(roots);
    } catch {}
  }, []);

  useEffect(() => {
    if (viewMode === 'tree') loadTreeData();
  }, [viewMode, loadTreeData]);

  const toggleExpand = async (ticketId: number) => {
    const next = new Set(expandedNodes);
    if (next.has(ticketId)) {
      next.delete(ticketId);
    } else {
      next.add(ticketId);
      if (!childrenCache[ticketId]) {
        try {
          const children = await api.getTicketChildren(ticketId);
          setChildrenCache(prev => ({ ...prev, [ticketId]: children }));
        } catch {}
      }
    }
    setExpandedNodes(next);
  };

  return (
    <div>
      {/* Header with create button and view toggle */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
        <div style={{ display: 'flex', gap: 12, alignItems: 'center' }}>
          <span style={{ fontSize: 13, color: 'var(--t-text-muted)' }}>{total} task{total !== 1 ? 's' : ''}</span>
          <div style={{ display: 'flex', gap: 0, border: '1px solid var(--t-border)', borderRadius: 4, overflow: 'hidden' }}>
            <button
              className="btn btn-ghost"
              style={{ fontSize: 10, padding: '2px 8px', borderRadius: 0, background: viewMode === 'list' ? 'var(--t-panel)' : 'transparent' }}
              onClick={() => setViewMode('list')}
            >List</button>
            <button
              className="btn btn-ghost"
              style={{ fontSize: 10, padding: '2px 8px', borderRadius: 0, background: viewMode === 'tree' ? 'var(--t-panel)' : 'transparent' }}
              onClick={() => setViewMode('tree')}
            >Tree</button>
          </div>
        </div>
        <button className="btn btn-primary btn-sm" onClick={() => setShowCreate(!showCreate)}>
          + Create Task
        </button>
      </div>

      {/* Create form */}
      {showCreate && (
        <CreateItemForm
          workItemTypes={workItemTypes}
          onCreated={() => { setShowCreate(false); loadTasks(); }}
          onCancel={() => setShowCreate(false)}
        />
      )}

      {/* Filters */}
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 12 }}>
        <input
          className="form-input"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search tasks..."
          style={{ flex: 1, minWidth: 180, height: 30, fontSize: 12 }}
        />
        <select
          className="form-input form-select"
          style={{ width: 110, height: 30, fontSize: 12 }}
          value={typeFilter}
          onChange={(e) => setTypeFilter(e.target.value)}
        >
          <option value="">All Types</option>
          <option value="task">Task</option>
          <option value="bug">Bug</option>
          <option value="feature">Feature</option>
        </select>
        <select
          className="form-input form-select"
          style={{ width: 120, height: 30, fontSize: 12 }}
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value)}
        >
          <option value="">All Statuses</option>
          <option value="backlog">Backlog</option>
          <option value="todo">To Do</option>
          <option value="in_progress">In Progress</option>
          <option value="in_review">In Review</option>
          <option value="testing">Testing</option>
          <option value="done">Done</option>
          <option value="cancelled">Cancelled</option>
        </select>
        <select
          className="form-input form-select"
          style={{ width: 130, height: 30, fontSize: 12 }}
          value={sprintFilter}
          onChange={(e) => setSprintFilter(e.target.value)}
        >
          <option value="">All Sprints</option>
          <option value="none">Unassigned</option>
          {sprints.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}
        </select>
        <select
          className="form-input form-select"
          style={{ width: 100, height: 30, fontSize: 12 }}
          value={priorityFilter}
          onChange={(e) => setPriorityFilter(e.target.value)}
        >
          <option value="">Priority</option>
          <option value="p1">P1</option>
          <option value="p2">P2</option>
          <option value="p3">P3</option>
          <option value="p4">P4</option>
        </select>
      </div>

      {/* Task list view */}
      {viewMode === 'list' && (
        <>
          {loading ? (
            <div className="empty-state"><div className="empty-state-text">Loading tasks...</div></div>
          ) : tasks.length === 0 ? (
            <div className="empty-state">
              <div className="empty-state-title">No tasks found</div>
              <div className="empty-state-text">Create a task to get started, or adjust your filters.</div>
            </div>
          ) : (
            <div style={{ border: '1px solid var(--t-border)', borderRadius: 6, overflow: 'hidden' }}>
              <div style={{
                display: 'grid',
                gridTemplateColumns: '70px 1fr 70px 60px 60px 130px 100px',
                gap: 8,
                padding: '8px 12px',
                background: 'var(--t-panel)',
                borderBottom: '1px solid var(--t-border)',
                fontSize: 10,
                fontWeight: 600,
                color: 'var(--t-text-muted)',
                textTransform: 'uppercase',
                letterSpacing: '0.03em',
              }}>
                <span>ID</span>
                <span>Title</span>
                <span>Type</span>
                <span>Priority</span>
                <span>Points</span>
                <span>Sprint</span>
                <span>Status</span>
              </div>
              {tasks.map((t) => (
                <div
                  key={t.id}
                  style={{
                    display: 'grid',
                    gridTemplateColumns: '70px 1fr 70px 60px 60px 130px 100px',
                    gap: 8,
                    padding: '8px 12px',
                    borderBottom: '1px solid var(--t-border)',
                    fontSize: 12,
                    alignItems: 'center',
                    cursor: 'pointer',
                  }}
                  onClick={() => onOpenItem(t.id)}
                >
                  <span style={{ fontSize: 10, color: 'var(--t-text-dim)' }}>{t.work_item_number || t.ticket_number}</span>
                  <span style={{ color: 'var(--t-text-bright)', fontWeight: 500, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{t.subject}</span>
                  <span style={{ fontSize: 10, color: 'var(--t-text-muted)' }}>{t.ticket_type}</span>
                  <span className={`badge badge-${t.priority}`} style={{ fontSize: 9, padding: '0 4px', width: 'fit-content' }}>{t.priority?.toUpperCase()}</span>
                  <span onClick={(e) => e.stopPropagation()}>
                    <InlinePoints ticketId={t.id} value={t.story_points} onSave={handleUpdatePoints} />
                  </span>
                  {assigningSprint === t.id ? (
                    <select
                      className="form-input form-select"
                      style={{ height: 24, fontSize: 10, padding: '0 4px' }}
                      autoFocus
                      value={t.sprint_id ?? ''}
                      onClick={(e) => e.stopPropagation()}
                      onChange={(e) => handleAssignSprint(t.id, e.target.value ? Number(e.target.value) : null)}
                      onBlur={() => setAssigningSprint(null)}
                    >
                      <option value="">Unassigned</option>
                      {activeSprints.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}
                    </select>
                  ) : (
                    <span
                      style={{ fontSize: 10, color: t.sprint_name ? 'var(--t-text-bright)' : 'var(--t-text-dim)', cursor: 'pointer' }}
                      title="Click to assign sprint"
                      onClick={(e) => { e.stopPropagation(); setAssigningSprint(t.id); }}
                    >
                      {t.sprint_name || '— assign —'}
                    </span>
                  )}
                  <span style={{ fontSize: 10, color: 'var(--t-text-muted)' }}>{t.status?.replace(/_/g, ' ')}</span>
                </div>
              ))}
            </div>
          )}
        </>
      )}

      {/* Tree view */}
      {viewMode === 'tree' && (
        <div style={{ border: '1px solid var(--t-border)', borderRadius: 6, overflow: 'hidden' }}>
          {treeData.length === 0 ? (
            <div style={{ padding: 20, textAlign: 'center', fontSize: 12, color: 'var(--t-text-dim)' }}>
              No work items found. Create tasks and assign parent relationships to build the tree.
            </div>
          ) : treeData.map((t) => (
            <TreeNode
              key={t.id}
              item={t}
              depth={0}
              expanded={expandedNodes}
              childrenCache={childrenCache}
              onToggle={toggleExpand}
              onOpen={onOpenItem}
            />
          ))}
        </div>
      )}
    </div>
  );
}


/* ============================================================
   TREE NODE — recursive work item hierarchy display
   ============================================================ */
function TreeNode({
  item, depth, expanded, childrenCache, onToggle, onOpen,
}: {
  item: any; depth: number; expanded: Set<number>; childrenCache: Record<number, any[]>;
  onToggle: (id: number) => void; onOpen: (id: number) => void;
}) {
  const isExpanded = expanded.has(item.id);
  const children = childrenCache[item.id] || [];
  const hasChildren = true; // Assume expandable until proven otherwise

  return (
    <>
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          padding: '6px 12px',
          paddingLeft: 12 + depth * 24,
          borderBottom: '1px solid var(--t-border)',
          fontSize: 12,
          cursor: 'pointer',
        }}
        onClick={() => onOpen(item.id)}
      >
        <span
          style={{ fontSize: 10, cursor: 'pointer', width: 14, textAlign: 'center', color: 'var(--t-text-dim)' }}
          onClick={(e) => { e.stopPropagation(); onToggle(item.id); }}
        >
          {isExpanded ? '▼' : '▶'}
        </span>
        {item.work_item_type_icon && (
          <span style={{ fontSize: 12 }} title={item.work_item_type_name}>{item.work_item_type_icon}</span>
        )}
        <span style={{ fontSize: 10, color: 'var(--t-text-dim)', minWidth: 60 }}>{item.work_item_number || item.ticket_number}</span>
        <span style={{ flex: 1, fontWeight: 500, color: 'var(--t-text-bright)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{item.subject}</span>
        <span className={`badge badge-${item.priority}`} style={{ fontSize: 9, padding: '0 4px' }}>{item.priority?.toUpperCase()}</span>
        {item.story_points != null && (
          <span style={{ fontSize: 10, fontWeight: 600, background: 'var(--t-panel)', padding: '1px 5px', borderRadius: 3 }}>
            {item.story_points} pts
          </span>
        )}
        <span style={{ fontSize: 10, color: 'var(--t-text-muted)', minWidth: 70 }}>{item.status?.replace(/_/g, ' ')}</span>
      </div>
      {isExpanded && children.length > 0 && children.map((child: any) => (
        <TreeNode
          key={child.id}
          item={child}
          depth={depth + 1}
          expanded={expanded}
          childrenCache={childrenCache}
          onToggle={onToggle}
          onOpen={onOpen}
        />
      ))}
      {isExpanded && children.length === 0 && (
        <div style={{ paddingLeft: 36 + depth * 24, padding: '4px 12px', fontSize: 11, color: 'var(--t-text-dim)', fontStyle: 'italic', borderBottom: '1px solid var(--t-border)' }}>
          No children
        </div>
      )}
    </>
  );
}


/* ============================================================
   INLINE POINTS — click-to-edit story points in task list
   ============================================================ */
function InlinePoints({ ticketId, value, onSave }: { ticketId: number; value: number | null; onSave: (id: number, val: string) => void }) {
  const [editing, setEditing] = useState(false);
  const [val, setVal] = useState(value != null ? String(value) : '');

  if (editing) {
    return (
      <input
        className="form-input"
        type="number"
        min="0"
        style={{ width: 45, height: 20, fontSize: 10, padding: '0 4px', textAlign: 'center' }}
        value={val}
        autoFocus
        onChange={(e) => setVal(e.target.value)}
        onBlur={() => { onSave(ticketId, val); setEditing(false); }}
        onKeyDown={(e) => { if (e.key === 'Enter') { onSave(ticketId, val); setEditing(false); } if (e.key === 'Escape') setEditing(false); }}
      />
    );
  }
  return (
    <span
      style={{ fontSize: 10, color: value != null ? 'var(--t-text-bright)' : 'var(--t-text-dim)', cursor: 'pointer', fontWeight: value != null ? 600 : 400 }}
      onClick={() => { setVal(value != null ? String(value) : ''); setEditing(true); }}
    >
      {value != null ? `${value} pts` : '+ pts'}
    </span>
  );
}
