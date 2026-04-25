import { useEffect, useCallback, useRef, useState, useMemo, type DragEvent } from 'react';
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  addEdge,
  applyNodeChanges,
  applyEdgeChanges,
  useReactFlow,
  ReactFlowProvider,
  BaseEdge,
  getBezierPath,
  type Connection,
  type NodeChange,
  type EdgeChange,
  type Node,
  type EdgeProps,
  BackgroundVariant,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';

import { useAutomationStore } from '../../store/automationStore';
import { useUIStore } from '../../store/uiStore';
import { useThemeStore } from '../../store/themeStore';
import { pushUrl } from '../../utils/url';
import { TriggerNode } from './nodes/TriggerNode';
import { ConditionNode } from './nodes/ConditionNode';
import { ActionNode } from './nodes/ActionNode';
import { CommentNode } from './nodes/CommentNode';
import { NodeConfigPanel } from './NodeConfigPanel';
import { RunHistory } from './RunHistory';

/** Clickable edge — hover turns it red, click removes it. */
function DeletableEdge({
  id, sourceX, sourceY, targetX, targetY,
  sourcePosition, targetPosition, style, animated,
}: EdgeProps) {
  const [hovered, setHovered] = useState(false);

  const [edgePath] = getBezierPath({
    sourceX, sourceY, sourcePosition,
    targetX, targetY, targetPosition,
  });

  const handleDelete = useCallback(() => {
    useAutomationStore.setState((s: any) => ({
      ...s,
      edges: (s.edges as any[]).filter((e: any) => e.id !== id),
      dirty: true,
    }));
  }, [id]);

  const baseStroke = hovered
    ? 'var(--t-error, #f44336)'
    : (style?.stroke || 'var(--t-text-muted)');

  return (
    <g>
      {/* Base solid line */}
      <path
        d={edgePath}
        fill="none"
        style={{ stroke: baseStroke, strokeWidth: hovered ? 2 : 1.5, transition: 'stroke 0.15s, stroke-width 0.15s', pointerEvents: 'none' }}
      />
      {/* Traveling accent dot — hidden when hovered (solid red = delete cue) */}
      {animated && !hovered && (
        <path
          d={edgePath}
          fill="none"
          className="react-flow__edge-traveling-dot"
          style={{ stroke: 'var(--t-accent)', strokeWidth: 3.5, pointerEvents: 'none' }}
        />
      )}
      {/* Wide transparent hit area for easy clicking */}
      <path
        d={edgePath}
        fill="none"
        stroke="transparent"
        strokeWidth={20}
        style={{ cursor: 'pointer' }}
        onMouseEnter={() => setHovered(true)}
        onMouseLeave={() => setHovered(false)}
        onClick={handleDelete}
      />
    </g>
  );
}

const nodeTypes = {
  trigger: TriggerNode,
  condition: ConditionNode,
  action: ActionNode,
  comment: CommentNode,
};

const edgeTypes = {
  deletable: DeletableEdge,
};

interface PaletteItem {
  type: 'trigger' | 'condition' | 'action' | 'comment';
  subtype: string;
  label: string;
}

const PALETTE: { category: string; items: PaletteItem[] }[] = [
  {
    category: 'Triggers',
    items: [
      { type: 'trigger', subtype: 'ticket_created', label: 'Ticket Created' },
      { type: 'trigger', subtype: 'status_changed', label: 'Status Changed' },
      { type: 'trigger', subtype: 'priority_changed', label: 'Priority Changed' },
      { type: 'trigger', subtype: 'comment_added', label: 'Comment Added' },
      { type: 'trigger', subtype: 'assignee_changed', label: 'Assignee Changed' },
      { type: 'trigger', subtype: 'tag_added', label: 'Tag Added' },
      { type: 'trigger', subtype: 'sla_breached', label: 'SLA Breached' },
      { type: 'trigger', subtype: 'schedule', label: 'Schedule' },
    ],
  },
  {
    category: 'Conditions',
    items: [
      { type: 'condition', subtype: 'assignee_set', label: 'Assignee Set?' },
      { type: 'condition', subtype: 'category_is', label: 'Category Is' },
      { type: 'condition', subtype: 'custom_field_equals', label: 'Custom Field Value' },
      { type: 'condition', subtype: 'hours_since', label: 'Hours Since' },
      { type: 'condition', subtype: 'location_is', label: 'Location Is' },
      { type: 'condition', subtype: 'priority_is', label: 'Priority Is' },
      { type: 'condition', subtype: 'requester_role', label: 'Requester Role' },
      { type: 'condition', subtype: 'status_is', label: 'Status Is' },
      { type: 'condition', subtype: 'tag_contains', label: 'Tag Contains' },
    ],
  },
  {
    category: 'Actions',
    items: [
      { type: 'action', subtype: 'add_tag', label: 'Add Tag' },
      { type: 'action', subtype: 'assign_team', label: 'Assign Team' },
      { type: 'action', subtype: 'assign_to', label: 'Assign To Agent' },
      { type: 'action', subtype: 'change_priority', label: 'Change Priority' },
      { type: 'action', subtype: 'change_status', label: 'Change Status' },
      { type: 'action', subtype: 'do_nothing', label: 'Do Nothing' },
      { type: 'action', subtype: 'email_group', label: 'Email Group' },
      { type: 'action', subtype: 'post_comment', label: 'Post Comment' },
      { type: 'action', subtype: 'remove_tag', label: 'Remove Tag' },
      { type: 'action', subtype: 'send_notification', label: 'Send Notification' },
      { type: 'action', subtype: 'set_custom_field', label: 'Set Custom Field' },
      { type: 'action', subtype: 'webhook', label: 'Webhook' },
    ],
  },
  {
    category: 'Utility',
    items: [
      { type: 'comment', subtype: 'comment', label: 'Comment / Note' },
    ],
  },
];

const ALL_PALETTE_ITEMS = PALETTE.flatMap((g) => g.items);

let nodeIdCounter = 0;
function nextNodeId(type: string) {
  nodeIdCounter++;
  return `${type}-${Date.now()}-${nodeIdCounter}`;
}

/** Wrapped component that has access to useReactFlow */
function BuilderInner({ automationId }: { automationId: number }) {
  const {
    activeAutomation, nodes, edges, selectedNodeId, dirty, saving,
    fetchAutomation, saveCanvas, toggleAutomation, selectNode,
    clearBuilder, updateAutomation,
  } = useAutomationStore();
  const setNodes = useAutomationStore((s) => s.setNodes);
  const setEdges = useAutomationStore((s) => s.setEdges);
  const themeMode = useThemeStore((s) => s.mode);
  const reactFlowWrapper = useRef<HTMLDivElement>(null);
  const { screenToFlowPosition } = useReactFlow();
  const [showRuns, setShowRuns] = useState(false);
  const [editingName, setEditingName] = useState(false);
  const [nameValue, setNameValue] = useState('');

  // Quick-add picker
  const [quickAdd, setQuickAdd] = useState<{ x: number; y: number; flowX: number; flowY: number; sourceNodeId: string; sourceHandle: string } | null>(null);
  const [quickFilter, setQuickFilter] = useState('');
  const quickFilterRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    fetchAutomation(automationId);
    return () => clearBuilder();
  }, [automationId]);

  useEffect(() => {
    if (activeAutomation) setNameValue(activeAutomation.name);
  }, [activeAutomation?.name]);

  const onNodesChange = useCallback((changes: NodeChange[]) => {
    const updated = applyNodeChanges(changes, nodes);
    setNodes(updated);
  }, [nodes, setNodes]);

  const onEdgesChange = useCallback((changes: EdgeChange[]) => {
    const updated = applyEdgeChanges(changes, edges);
    setEdges(updated);
  }, [edges, setEdges]);

  const onConnect = useCallback((connection: Connection) => {
    const newEdge = {
      ...connection,
      id: `edge-${Date.now()}`,
      animated: true,
      style: { stroke: 'var(--t-text-muted)' },
    };
    setEdges(addEdge(newEdge, edges as any[]) as any);
  }, [edges, setEdges]);

  // Drag from handle → release on empty canvas → show quick-add picker
  const onConnectEnd = useCallback((event: any, connectionState: any) => {
    if (connectionState?.isValid) return;
    const wrapper = reactFlowWrapper.current;
    if (!wrapper) return;

    const bounds = wrapper.getBoundingClientRect();
    const clientX = event.clientX || event.changedTouches?.[0]?.clientX || 0;
    const clientY = event.clientY || event.changedTouches?.[0]?.clientY || 0;
    const flowPos = screenToFlowPosition({ x: clientX, y: clientY });

    setQuickAdd({
      x: clientX - bounds.left,
      y: clientY - bounds.top,
      flowX: flowPos.x,
      flowY: flowPos.y,
      sourceNodeId: connectionState?.fromNode?.id || '',
      sourceHandle: connectionState?.fromHandle?.id || 'default',
    });
    setQuickFilter('');
    setTimeout(() => quickFilterRef.current?.focus(), 50);
  }, [screenToFlowPosition]);

  const addNodeFromQuickPicker = (item: PaletteItem) => {
    if (!quickAdd) return;
    if (item.type === 'trigger' && nodes.some((n) => n.type === 'trigger')) return;

    const newNode: Node = {
      id: nextNodeId(item.type),
      type: item.type,
      position: { x: quickAdd.flowX, y: quickAdd.flowY - 30 },
      data: { label: item.label, subtype: item.subtype, config: {} },
    };

    const newEdge = {
      id: `edge-${Date.now()}`,
      source: quickAdd.sourceNodeId,
      sourceHandle: quickAdd.sourceHandle,
      target: newNode.id,
      targetHandle: 'target',
      animated: true,
      style: { stroke: 'var(--t-text-muted)' },
    };

    const updatedNodes = [...nodes, newNode];
    const updatedEdges = quickAdd.sourceNodeId ? [...edges, newEdge] : [...edges];
    setNodes(updatedNodes);
    setEdges(updatedEdges as any);
    selectNode(newNode.id);
    setQuickAdd(null);
  };

  // Alt+click = extract node from chain, bridge edges around it
  const onNodeClick = useCallback((event: any, node: Node) => {
    if (event.altKey) {
      event.stopPropagation();
      event.preventDefault();

      const incoming = edges.filter((e) => e.target === node.id);
      const outgoing = edges.filter((e) => e.source === node.id);

      // For non-condition nodes: bridge incoming → outgoing
      // For condition nodes: just remove (can't meaningfully bridge two outputs)
      const bridges: any[] = [];
      if (node.type !== 'condition') {
        for (const inc of incoming) {
          for (const out of outgoing) {
            bridges.push({
              id: `edge-bridge-${Date.now()}-${bridges.length}`,
              source: inc.source,
              sourceHandle: inc.sourceHandle,
              target: out.target,
              targetHandle: out.targetHandle,
              animated: true,
              style: { stroke: 'var(--t-text-muted)' },
            });
          }
        }
      }

      // Apply atomically: remove node + its edges, add bridges
      const newNodes = nodes.filter((n) => n.id !== node.id);
      const newEdges = [
        ...edges.filter((e) => e.source !== node.id && e.target !== node.id),
        ...bridges,
      ];

      // Set both at once so React Flow doesn't auto-clean edges
      useAutomationStore.setState((s) => ({
        ...s,
        nodes: newNodes as any,
        edges: newEdges as any,
        dirty: true,
        selectedNodeId: null,
      }));
      return;
    }
    selectNode(node.id);
  }, [nodes, edges, selectNode]);

  const onPaneClick = useCallback(() => {
    selectNode(null);
    setQuickAdd(null);
  }, [selectNode]);

  // Add deletable type to all edges for display; store keeps plain edges
  const displayEdges = useMemo(
    () => edges.map((e: any) => ({ ...e, type: 'deletable', animated: true })),
    [edges],
  );

  // ── Drag & drop from palette ──────────────────────────
  const onDragOver = useCallback((event: DragEvent) => {
    event.preventDefault();
    event.dataTransfer.dropEffect = 'move';
  }, []);

  const onDrop = useCallback((event: DragEvent) => {
    event.preventDefault();
    const raw = event.dataTransfer.getData('application/reactflow');
    if (!raw) return;

    const item: PaletteItem = JSON.parse(raw);
    if (item.type === 'trigger' && nodes.some((n) => n.type === 'trigger')) return;

    const position = screenToFlowPosition({ x: event.clientX, y: event.clientY });

    const newNode: Node = {
      id: nextNodeId(item.type),
      type: item.type,
      position,
      data: { label: item.label, subtype: item.subtype, config: {} },
    };
    setNodes([...nodes, newNode]);
    selectNode(newNode.id);
  }, [nodes, setNodes, selectNode, screenToFlowPosition]);

  const onPaletteDragStart = (event: DragEvent, item: PaletteItem) => {
    event.dataTransfer.setData('application/reactflow', JSON.stringify(item));
    event.dataTransfer.effectAllowed = 'move';
  };

  // ── Keyboard ──────────────────────────────────────────
  const addNodeFromPalette = (item: PaletteItem) => {
    if (item.type === 'trigger' && nodes.some((n) => n.type === 'trigger')) return;
    const newNode: Node = {
      id: nextNodeId(item.type),
      type: item.type,
      position: { x: 100 + nodes.length * 250, y: 250 },
      data: { label: item.label, subtype: item.subtype, config: {} },
    };
    setNodes([...nodes, newNode]);
    selectNode(newNode.id);
  };

  const goBack = () => {
    clearBuilder();
    pushUrl('/automations');
    useUIStore.getState().setView('automations');
    window.dispatchEvent(new PopStateEvent('popstate'));
  };

  const saveName = async () => {
    if (activeAutomation && nameValue.trim()) {
      await updateAutomation(activeAutomation.id, { name: nameValue.trim() });
    }
    setEditingName(false);
  };

  const handleSaveDraft = async () => {
    await saveCanvas();
    // If currently active, deactivate — edits must be explicitly re-published
    if (activeAutomation?.is_active) {
      await toggleAutomation(activeAutomation!.id);
    }
  };

  const handlePublish = async () => {
    await saveCanvas();
    if (!activeAutomation?.is_active) {
      await toggleAutomation(activeAutomation!.id);
    }
  };

  const handleKeyboardSave = useCallback((e: KeyboardEvent) => {
    if ((e.ctrlKey || e.metaKey) && e.key === 's') {
      e.preventDefault();
      saveCanvas();
    }
  }, [saveCanvas]);

  useEffect(() => {
    window.addEventListener('keydown', handleKeyboardSave);
    return () => window.removeEventListener('keydown', handleKeyboardSave);
  }, [handleKeyboardSave]);

  if (!activeAutomation) {
    return <div className="automation-loading">Loading automation...</div>;
  }

  const hasTrigger = nodes.some((n) => n.type === 'trigger');
  const isDraft = !activeAutomation.is_active;

  const filteredQuickItems = quickFilter
    ? ALL_PALETTE_ITEMS.filter((i) => i.label.toLowerCase().includes(quickFilter.toLowerCase()))
    : ALL_PALETTE_ITEMS;

  return (
    <div className="auto-builder">
      {/* Toolbar */}
      <div className="auto-builder-toolbar">
        <div className="auto-builder-toolbar-left">
          <button className="btn btn-ghost btn-sm" onClick={goBack}>
            <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5">
              <path d="M10 4L6 8l4 4" />
            </svg>
            Back
          </button>
          {editingName ? (
            <input
              className="input auto-builder-name-input"
              value={nameValue}
              onChange={(e) => setNameValue(e.target.value)}
              onBlur={saveName}
              onKeyDown={(e) => e.key === 'Enter' && saveName()}
              autoFocus
            />
          ) : (
            <h2 className="auto-builder-name" onClick={() => setEditingName(true)}>
              {activeAutomation.name}
            </h2>
          )}
          <span className={`auto-builder-status ${isDraft ? 'draft' : 'published'}`}>
            {isDraft ? 'Draft' : 'Published'}
          </span>
          {dirty && <span className="auto-builder-dirty">Unsaved</span>}
        </div>
        <div className="auto-builder-toolbar-right">
          <button className="btn btn-ghost btn-sm" onClick={() => setShowRuns(!showRuns)}>
            {showRuns ? 'Hide' : 'Show'} Runs ({activeAutomation.run_count})
          </button>
          <button className="btn btn-ghost btn-sm" onClick={handleSaveDraft} disabled={!dirty || saving}>
            {saving ? 'Saving...' : 'Save Draft'}
          </button>
          {isDraft ? (
            <button className="btn btn-primary btn-sm" onClick={handlePublish} disabled={saving}>Publish</button>
          ) : (
            <button className="btn btn-ghost btn-sm" onClick={() => toggleAutomation(activeAutomation.id)} style={{ color: 'var(--t-warning)' }}>
              Unpublish
            </button>
          )}
        </div>
      </div>

      <div className="auto-builder-body">
        {/* Palette — draggable items */}
        <div className="auto-palette">
          {PALETTE.map((group) => (
            <div key={group.category} className="auto-palette-group">
              <div className="auto-palette-category">{group.category}</div>
              {group.items.map((item) => {
                const disabled = item.type === 'trigger' && hasTrigger;
                return (
                  <div
                    key={item.subtype}
                    className={`auto-palette-item auto-palette-${item.type} ${disabled ? 'disabled' : ''}`}
                    draggable={!disabled}
                    onDragStart={(e) => !disabled && onPaletteDragStart(e, item)}
                    onClick={() => !disabled && addNodeFromPalette(item)}
                    title={disabled ? 'Only one trigger per automation' : `Drag or click to add ${item.label}`}
                  >
                    <span className={`auto-palette-dot auto-palette-dot-${item.type}`} />
                    {item.label}
                  </div>
                );
              })}
            </div>
          ))}
          <div className="auto-palette-hint">
            Drag or click to add. Click an edge to disconnect it. Alt+click a node to remove it.
          </div>
        </div>

        {/* Canvas */}
        <div className="auto-canvas" ref={reactFlowWrapper}>
          <ReactFlow
            nodes={nodes}
            edges={displayEdges}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            onConnect={onConnect}
            onConnectEnd={onConnectEnd as any}
            onNodeClick={onNodeClick}
            onPaneClick={onPaneClick}
            onDragOver={onDragOver}
            onDrop={onDrop}
            nodeTypes={nodeTypes}
            edgeTypes={edgeTypes}
            snapToGrid
            snapGrid={[20, 20]}
            fitView
            fitViewOptions={{ padding: 0.3 }}
            deleteKeyCode="Delete"
            defaultEdgeOptions={{ animated: true, style: { stroke: 'var(--t-text-muted)' } }}
          >
            <Background variant={BackgroundVariant.Dots} gap={20} size={1.5} color={themeMode === 'dark' ? 'rgba(68, 221, 68, 0.15)' : 'rgba(0, 120, 0, 0.18)'} />
            <Controls position="bottom-right" />
            <MiniMap
              nodeColor={(node) => {
                if (node.type === 'trigger') return 'var(--t-success)';
                if (node.type === 'condition') return 'var(--t-warning)';
                if (node.type === 'comment') return 'var(--t-text-dim)';
                return 'var(--t-info)';
              }}
              maskColor="rgba(0,0,0,0.7)"
              style={{ background: 'var(--t-panel)' }}
            />
          </ReactFlow>

          {/* Quick-add picker */}
          {quickAdd && (
            <div className="auto-quickadd" style={{ left: quickAdd.x, top: quickAdd.y }} onClick={(e) => e.stopPropagation()}>
              <input
                ref={quickFilterRef}
                className="auto-quickadd-search"
                placeholder="Search nodes..."
                value={quickFilter}
                onChange={(e) => setQuickFilter(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Escape') setQuickAdd(null);
                  if (e.key === 'Enter' && filteredQuickItems.length > 0) addNodeFromQuickPicker(filteredQuickItems[0]);
                }}
              />
              <div className="auto-quickadd-list">
                {filteredQuickItems.map((item) => {
                  const disabled = item.type === 'trigger' && hasTrigger;
                  return (
                    <button
                      key={`${item.type}-${item.subtype}`}
                      className={`auto-quickadd-item ${disabled ? 'disabled' : ''}`}
                      onClick={() => !disabled && addNodeFromQuickPicker(item)}
                      disabled={disabled}
                    >
                      <span className={`auto-palette-dot auto-palette-dot-${item.type}`} />
                      {item.label}
                    </button>
                  );
                })}
              </div>
            </div>
          )}
        </div>

        {/* Config panel */}
        {selectedNodeId && <NodeConfigPanel />}
      </div>

      {/* Run history drawer */}
      {showRuns && (
        <div className="auto-builder-runs">
          <RunHistory automationId={automationId} />
        </div>
      )}
    </div>
  );
}

/** Exported component wraps with ReactFlowProvider for useReactFlow() */
export function AutomationBuilder({ automationId }: { automationId: number }) {
  return (
    <ReactFlowProvider>
      <BuilderInner automationId={automationId} />
    </ReactFlowProvider>
  );
}
