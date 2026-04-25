import { create } from 'zustand';

type Accent = 'green' | 'red' | 'gold' | 'blue' | 'white';
type Mode = 'dark' | 'light';

const ACCENT_PRESETS: Record<Accent, { accent: string; hover: string; rgb: [number, number, number] }> = {
  green: { accent: '#44dd44', hover: '#66ff66', rgb: [68, 221, 68] },
  red:   { accent: '#ff4444', hover: '#ff6666', rgb: [255, 68, 68] },
  gold:  { accent: '#ddaa22', hover: '#ffcc44', rgb: [221, 170, 34] },
  blue:  { accent: '#4488ff', hover: '#66aaff', rgb: [68, 136, 255] },
  white: { accent: '#ffffff', hover: '#e0e0e0', rgb: [255, 255, 255] },
};

const DARK_SURFACES = {
  '--t-bg': '#000000',
  '--t-panel': '#0a0a0a',
  '--t-panel-alt': '#141414',
  '--t-input-bg': '#0a0a0a',
  '--t-text': '#cccccc',
  '--t-text-bright': '#ffffff',
  '--t-text-muted': '#666666',
  '--t-text-dim': '#444444',
  '--t-text-on-accent': '#000000',
  '--t-border': '#1e1e1e',
  '--t-border-light': '#2a2a2a',
  '--t-hover': '#1e1e1e',
};

const LIGHT_SURFACES = {
  '--t-bg': '#f5f5f5',
  '--t-panel': '#ffffff',
  '--t-panel-alt': '#f0f0f0',
  '--t-input-bg': '#ffffff',
  '--t-text': '#333333',
  '--t-text-bright': '#111111',
  '--t-text-muted': '#888888',
  '--t-text-dim': '#aaaaaa',
  '--t-text-on-accent': '#000000',
  '--t-border': '#d4d4d4',
  '--t-border-light': '#c0c0c0',
  '--t-hover': '#e8e8e8',
};

const STORAGE_KEY = 'helpdesk-theme';

function hexToRgba(hex: string, alpha: number): string {
  const r = parseInt(hex.slice(1, 3), 16);
  const g = parseInt(hex.slice(3, 5), 16);
  const b = parseInt(hex.slice(5, 7), 16);
  return `rgba(${r},${g},${b},${alpha})`;
}

function applyTheme(accent: Accent, mode: Mode) {
  const el = document.documentElement;
  const preset = ACCENT_PRESETS[accent];

  // Accent vars
  el.style.setProperty('--t-accent', preset.accent);
  el.style.setProperty('--t-accent-hover', preset.hover);
  el.style.setProperty('--t-accent-text', preset.accent);
  el.style.setProperty('--t-accent-bg', hexToRgba(preset.accent, 0.10));
  el.style.setProperty('--t-accent-border', hexToRgba(preset.accent, 0.25));
  el.style.setProperty('--t-accent-glow', hexToRgba(preset.accent, 0.20));
  el.style.setProperty('--nova-1', preset.accent);

  // Surface vars
  const surfaces = mode === 'light' ? LIGHT_SURFACES : DARK_SURFACES;
  for (const [prop, val] of Object.entries(surfaces)) {
    el.style.setProperty(prop, val);
  }
}

interface ThemeState {
  accent: Accent;
  mode: Mode;
  setAccent: (a: Accent) => void;
  setMode: (m: Mode) => void;
  initTheme: () => void;
  getAccentRGB: () => [number, number, number];
  getBaseRGB: () => [number, number, number];
}

export const useThemeStore = create<ThemeState>()((set, get) => ({
  accent: 'green',
  mode: 'dark',

  setAccent: (accent) => {
    set({ accent });
    applyTheme(accent, get().mode);
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ accent, mode: get().mode }));
  },

  setMode: (mode) => {
    set({ mode });
    applyTheme(get().accent, mode);
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ accent: get().accent, mode }));
  },

  initTheme: () => {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (raw) {
        const { accent, mode } = JSON.parse(raw);
        if (accent && mode) {
          set({ accent, mode });
          applyTheme(accent, mode);
          return;
        }
      }
    } catch { /* use defaults */ }
    applyTheme(get().accent, get().mode);
  },

  getAccentRGB: () => ACCENT_PRESETS[get().accent].rgb,
  getBaseRGB: () => get().mode === 'light' ? [220, 220, 220] : [20, 20, 20],
}));
