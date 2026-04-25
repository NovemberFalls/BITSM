import { create } from 'zustand';
import { api } from '../api/client';
import type { LocationNode, ProblemCategory } from '../types';

interface HierarchyState {
  locations: LocationNode[];
  problemCategories: ProblemCategory[];
  loading: boolean;

  loadLocations: () => Promise<void>;
  loadProblemCategories: () => Promise<void>;
  loadAll: () => Promise<void>;

  createLocation: (data: any) => Promise<number>;
  updateLocation: (id: number, data: any) => Promise<void>;
  deleteLocation: (id: number) => Promise<void>;

  createProblemCategory: (data: any) => Promise<number>;
  updateProblemCategory: (id: number, data: any) => Promise<void>;
  deleteProblemCategory: (id: number) => Promise<void>;
}

/** Build children array for tree rendering from flat list with parent_id. */
export function buildTree<T extends { id: number; parent_id: number | null }>(
  items: T[],
  parentId: number | null = null,
): (T & { children: T[] })[] {
  return items
    .filter((item) => item.parent_id === parentId)
    .map((item) => ({
      ...item,
      children: buildTree(items, item.id),
    }));
}

/** Get children of a specific parent from flat list. */
export function getChildren<T extends { parent_id: number | null }>(
  items: T[],
  parentId: number | null,
): T[] {
  return items.filter((item) => item.parent_id === parentId);
}

/** Build breadcrumb path from leaf to root. */
export function getBreadcrumb<T extends { id: number; parent_id: number | null; name: string }>(
  items: T[],
  nodeId: number | null,
): string[] {
  const parts: string[] = [];
  let currentId = nodeId;
  const seen = new Set<number>();
  while (currentId != null) {
    if (seen.has(currentId)) break;
    seen.add(currentId);
    const node = items.find((n) => n.id === currentId);
    if (!node) break;
    parts.unshift(node.name);
    currentId = node.parent_id;
  }
  return parts;
}

export const useHierarchyStore = create<HierarchyState>((set, get) => ({
  locations: [],
  problemCategories: [],
  loading: false,

  loadLocations: async () => {
    try {
      const data = await api.listLocations();
      set({ locations: data });
    } catch {}
  },

  loadProblemCategories: async () => {
    try {
      const data = await api.listProblemCategories();
      set({ problemCategories: data });
    } catch {}
  },

  loadAll: async () => {
    set({ loading: true });
    await Promise.all([get().loadLocations(), get().loadProblemCategories()]);
    set({ loading: false });
  },

  createLocation: async (data) => {
    const result = await api.createLocation(data);
    await get().loadLocations();
    return result.id;
  },

  updateLocation: async (id, data) => {
    await api.updateLocation(id, data);
    await get().loadLocations();
  },

  deleteLocation: async (id) => {
    await api.deleteLocation(id);
    await get().loadLocations();
  },

  createProblemCategory: async (data) => {
    const result = await api.createProblemCategory(data);
    await get().loadProblemCategories();
    return result.id;
  },

  updateProblemCategory: async (id, data) => {
    await api.updateProblemCategory(id, data);
    await get().loadProblemCategories();
  },

  deleteProblemCategory: async (id) => {
    await api.deleteProblemCategory(id);
    await get().loadProblemCategories();
  },
}));
