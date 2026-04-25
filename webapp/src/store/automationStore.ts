import { create } from 'zustand';
import { immer } from 'zustand/middleware/immer';
import { api } from '../api/client';
import type { Automation, AutomationRun, AutomationNode, AutomationEdge } from '../types';
import type { Node, Edge } from '@xyflow/react';

interface AutomationState {
  // List view
  automations: Automation[];
  loading: boolean;

  // Builder view
  activeAutomation: Automation | null;
  nodes: Node[];
  edges: Edge[];
  selectedNodeId: string | null;
  dirty: boolean;
  runs: AutomationRun[];
  saving: boolean;

  // Actions
  fetchAutomations: () => Promise<void>;
  fetchAutomation: (id: number) => Promise<void>;
  createAutomation: (data: { name: string; trigger_type: string; description?: string }) => Promise<number>;
  updateAutomation: (id: number, data: Partial<Automation>) => Promise<void>;
  deleteAutomation: (id: number) => Promise<void>;
  toggleAutomation: (id: number) => Promise<void>;
  saveCanvas: () => Promise<void>;
  setNodes: (nodes: Node[]) => void;
  setEdges: (edges: Edge[]) => void;
  onNodesChange: (changes: any) => void;
  onEdgesChange: (changes: any) => void;
  selectNode: (id: string | null) => void;
  updateNodeConfig: (nodeId: string, config: Record<string, any>) => void;
  updateNodeLabel: (nodeId: string, label: string) => void;
  fetchRuns: (automationId: number) => Promise<void>;
  clearBuilder: () => void;
}

/** Convert DB nodes → React Flow nodes */
function dbToFlowNodes(dbNodes: AutomationNode[]): Node[] {
  return dbNodes.map((n) => ({
    id: n.id,
    type: n.node_type,       // 'trigger' | 'condition' | 'action'
    position: { x: n.position_x, y: n.position_y },
    data: {
      label: n.label || n.node_subtype,
      subtype: n.node_subtype,
      config: n.config || {},
    },
  }));
}

/** Convert DB edges → React Flow edges */
function dbToFlowEdges(dbEdges: AutomationEdge[]): Edge[] {
  return dbEdges.map((e) => ({
    id: e.id,
    source: e.source_node,
    target: e.target_node,
    sourceHandle: e.source_handle || 'default',
    animated: true,
    style: { stroke: 'var(--t-text-muted)' },
  }));
}

/** Convert React Flow nodes → DB format for save */
function flowToDbNodes(nodes: Node[]): any[] {
  return nodes.map((n) => ({
    id: n.id,
    node_type: n.type || 'action',
    node_subtype: n.data?.subtype || '',
    position_x: n.position.x,
    position_y: n.position.y,
    config: n.data?.config || {},
    label: n.data?.label || '',
  }));
}

/** Convert React Flow edges → DB format for save */
function flowToDbEdges(edges: Edge[]): any[] {
  return edges.map((e) => ({
    id: e.id,
    source_node: e.source,
    target_node: e.target,
    source_handle: e.sourceHandle || 'default',
  }));
}

export const useAutomationStore = create<AutomationState>()(
  immer((set, get) => ({
    automations: [],
    loading: false,
    activeAutomation: null,
    nodes: [],
    edges: [],
    selectedNodeId: null,
    dirty: false,
    runs: [],
    saving: false,

    fetchAutomations: async () => {
      set((s) => { s.loading = true; });
      try {
        const data = await api.listAutomations();
        set((s) => { s.automations = data; s.loading = false; });
      } catch {
        set((s) => { s.loading = false; });
      }
    },

    fetchAutomation: async (id) => {
      try {
        const data = await api.getAutomation(id);
        set((s) => {
          s.activeAutomation = data;
          s.nodes = dbToFlowNodes(data.nodes || []);
          s.edges = dbToFlowEdges(data.edges || []);
          s.dirty = false;
          s.selectedNodeId = null;
        });
      } catch { /* ignore */ }
    },

    createAutomation: async (data) => {
      const res = await api.createAutomation(data);
      await get().fetchAutomations();
      return res.id;
    },

    updateAutomation: async (id, data) => {
      await api.updateAutomation(id, data);
      set((s) => {
        if (s.activeAutomation?.id === id) {
          Object.assign(s.activeAutomation, data);
        }
      });
      await get().fetchAutomations();
    },

    deleteAutomation: async (id) => {
      await api.deleteAutomation(id);
      set((s) => {
        s.automations = s.automations.filter((a) => a.id !== id);
        if (s.activeAutomation?.id === id) {
          s.activeAutomation = null;
          s.nodes = [];
          s.edges = [];
        }
      });
    },

    toggleAutomation: async (id) => {
      const res = await api.toggleAutomation(id);
      set((s) => {
        const a = s.automations.find((x) => x.id === id);
        if (a) a.is_active = res.is_active;
        if (s.activeAutomation?.id === id) s.activeAutomation.is_active = res.is_active;
      });
    },

    saveCanvas: async () => {
      const { activeAutomation, nodes, edges } = get();
      if (!activeAutomation) return;
      set((s) => { s.saving = true; });
      try {
        await api.saveAutomationCanvas(activeAutomation.id, {
          nodes: flowToDbNodes(nodes),
          edges: flowToDbEdges(edges),
        });
        set((s) => { s.dirty = false; s.saving = false; });
      } catch {
        set((s) => { s.saving = false; });
      }
    },

    setNodes: (nodes) => set((s) => { s.nodes = nodes as any; s.dirty = true; }),
    setEdges: (edges) => set((s) => { s.edges = edges as any; s.dirty = true; }),

    onNodesChange: () => { /* handled in builder component */ },
    onEdgesChange: () => { /* handled in builder component */ },

    selectNode: (id) => set((s) => { s.selectedNodeId = id; }),

    updateNodeConfig: (nodeId, config) => set((s) => {
      const node = s.nodes.find((n) => n.id === nodeId);
      if (node) {
        node.data = { ...node.data, config };
        s.dirty = true;
      }
    }),

    updateNodeLabel: (nodeId, label) => set((s) => {
      const node = s.nodes.find((n) => n.id === nodeId);
      if (node) {
        node.data = { ...node.data, label };
        s.dirty = true;
      }
    }),

    fetchRuns: async (automationId) => {
      try {
        const data = await api.listAutomationRuns(automationId, 50);
        set((s) => { s.runs = data; });
      } catch { /* ignore */ }
    },

    clearBuilder: () => set((s) => {
      s.activeAutomation = null;
      s.nodes = [];
      s.edges = [];
      s.selectedNodeId = null;
      s.dirty = false;
      s.runs = [];
    }),
  }))
);
