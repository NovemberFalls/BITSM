import { useEffect, useRef } from 'react';
import { useThemeStore } from '../../store/themeStore';

const HEX_RADIUS = 28;
const GLOW_RADIUS = 150;
const BASE_ALPHA = 0.06;
const LERP_SPEED = 0.25;
const PULSE_AMP = 0.015;
const PULSE_SPEED = 0.0008;
const HEX_W = Math.sqrt(3) * HEX_RADIUS;
const HEX_H = 2 * HEX_RADIUS;
const GLOW_INTENSITY = 0.5;

// Module-level mutable refs so theme changes don't re-mount canvas
let glowRGB: [number, number, number] = [68, 221, 68];
let baseRGB: [number, number, number] = [20, 20, 20];

interface HexCell {
  cx: number;
  cy: number;
  alpha: number;
  targetAlpha: number;
}

export function HexGrid() {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const mouseRef = useRef({ x: -9999, y: -9999 });
  const cellsRef = useRef<HexCell[]>([]);
  const rafRef = useRef<number>(0);

  // Subscribe to theme changes via module-level refs (no re-render)
  useEffect(() => {
    const unsub = useThemeStore.subscribe((state) => {
      glowRGB = state.getAccentRGB();
      baseRGB = state.getBaseRGB();
    });
    // Init
    glowRGB = useThemeStore.getState().getAccentRGB();
    baseRGB = useThemeStore.getState().getBaseRGB();
    return unsub;
  }, []);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    function resize() {
      if (!canvas) return;
      canvas.width = window.innerWidth;
      canvas.height = window.innerHeight;
      buildGrid();
    }

    function buildGrid() {
      const cells: HexCell[] = [];
      const cols = Math.ceil((canvas!.width + HEX_W) / HEX_W) + 1;
      const rows = Math.ceil((canvas!.height + HEX_H * 0.75) / (HEX_H * 0.75)) + 1;

      for (let row = -1; row < rows; row++) {
        for (let col = -1; col < cols; col++) {
          const cx = col * HEX_W + (row % 2 === 0 ? 0 : HEX_W / 2);
          const cy = row * HEX_H * 0.75;
          cells.push({ cx, cy, alpha: BASE_ALPHA, targetAlpha: BASE_ALPHA });
        }
      }
      cellsRef.current = cells;
    }

    function drawHex(cx: number, cy: number) {
      ctx!.beginPath();
      for (let i = 0; i < 6; i++) {
        const angle = (Math.PI / 180) * (60 * i - 30);
        const x = cx + HEX_RADIUS * Math.cos(angle);
        const y = cy + HEX_RADIUS * Math.sin(angle);
        i === 0 ? ctx!.moveTo(x, y) : ctx!.lineTo(x, y);
      }
      ctx!.closePath();
    }

    function animate(timestamp: number) {
      if (!ctx || !canvas) return;
      ctx.clearRect(0, 0, canvas.width, canvas.height);

      const mx = mouseRef.current.x;
      const my = mouseRef.current.y;
      const pulse = Math.sin(timestamp * PULSE_SPEED) * PULSE_AMP;

      for (const cell of cellsRef.current) {
        const dx = cell.cx - mx;
        const dy = cell.cy - my;
        const dist = Math.sqrt(dx * dx + dy * dy);

        if (dist < GLOW_RADIUS) {
          const intensity = 1 - dist / GLOW_RADIUS;
          cell.targetAlpha = BASE_ALPHA + intensity * GLOW_INTENSITY;
        } else {
          cell.targetAlpha = BASE_ALPHA + pulse;
        }

        cell.alpha += (cell.targetAlpha - cell.alpha) * LERP_SPEED;

        const a = Math.max(0, cell.alpha);
        const blend = Math.min(1, (a - BASE_ALPHA) / Math.max(0.01, GLOW_INTENSITY));
        const r = Math.round(baseRGB[0] + (glowRGB[0] - baseRGB[0]) * blend);
        const g = Math.round(baseRGB[1] + (glowRGB[1] - baseRGB[1]) * blend);
        const b = Math.round(baseRGB[2] + (glowRGB[2] - baseRGB[2]) * blend);

        drawHex(cell.cx, cell.cy);
        ctx.strokeStyle = `rgba(${r},${g},${b},${a})`;
        ctx.lineWidth = 1;
        ctx.stroke();
      }

      rafRef.current = requestAnimationFrame(animate);
    }

    function onMouseMove(e: MouseEvent) {
      mouseRef.current = { x: e.clientX, y: e.clientY };
    }

    function onMouseLeave() {
      mouseRef.current = { x: -9999, y: -9999 };
    }

    window.addEventListener('resize', resize);
    window.addEventListener('mousemove', onMouseMove);
    window.addEventListener('mouseleave', onMouseLeave);
    resize();
    rafRef.current = requestAnimationFrame(animate);

    return () => {
      window.removeEventListener('resize', resize);
      window.removeEventListener('mousemove', onMouseMove);
      window.removeEventListener('mouseleave', onMouseLeave);
      cancelAnimationFrame(rafRef.current);
    };
  }, []);

  return (
    <canvas
      ref={canvasRef}
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: 0,
        pointerEvents: 'none',
      }}
    />
  );
}
