import { create } from 'zustand';
import type { AppUser } from '../types';

interface AuthState {
  user: AppUser | null;
  setUser: (user: AppUser) => void;
  isAdmin: () => boolean;
  isSuperAdmin: () => boolean;
  hasPermission: (slug: string) => boolean;
  hasAnyPermission: (...slugs: string[]) => boolean;
}

export const useAuthStore = create<AuthState>((set, get) => ({
  user: null,
  setUser: (user) => set({ user }),
  isAdmin: () => {
    const { user } = get();
    return user?.role === 'super_admin' || user?.role === 'tenant_admin';
  },
  isSuperAdmin: () => get().user?.role === 'super_admin',
  hasPermission: (slug: string) => {
    const { user } = get();
    if (!user) return false;
    if (user.role === 'super_admin') return true;
    return (user.permissions || []).includes(slug);
  },
  hasAnyPermission: (...slugs: string[]) => {
    const { user } = get();
    if (!user) return false;
    if (user.role === 'super_admin') return true;
    const perms = user.permissions || [];
    return slugs.some(s => perms.includes(s));
  },
}));
