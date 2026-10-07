"use client";

import { Undo2, X } from "lucide-react";
import { useRef, useState } from "react";
import { mediaUrl, type Param } from "@/lib/api";
import { cn } from "@/lib/cn";
import type { SlotAsset } from "@/lib/draft";

export type MotionPath = { points: [number, number][] };

// Distinct on both the dark UI and a busy photo; the first is the studio's tungsten accent.
const COLORS = ["#f0a93b", "#6cc4d9", "#8fbfa8", "#e88aa8", "#b39ddb"];
const MIN_STEP = 0.006; // ignore pointer jitter smaller than this (fraction of the frame)
const TAP = 0.012; // a stroke shorter than this is a tap: pin the spot instead

/**
 * Draw motion paths on the start frame (Wan-Move, motion brush). The frame is shown cropped to the clip's
 * aspect, the way the model sees it, so points are stored as 0-1 coordinates of that crop.
 */
export function MotionPathsField({
  param,
  value,
  onChange,
  frame,
  aspect,
}: {
  param: Param;
  value: unknown;
  onChange: (v: MotionPath[]) => void;
  frame?: SlotAsset;
  aspect: string;
}) {
  const paths = Array.isArray(value) ? (value as MotionPath[]) : [];
  const max = "max_paths" in param && typeof param.max_paths === "number" ? param.max_paths : 5;
  const maxPoints = "max_points" in param && typeof param.max_points === "number" ? param.max_points : 256;
  // The stroke lives in a ref so a quick tap's pointerup sees the pointerdown point before React re-renders;
  // the state copy only drives what's drawn.
  const strokeRef = useRef<[number, number][] | null>(null);
  const [drawing, setDrawingState] = useState<[number, number][] | null>(null);
  const setDrawing = (pts: [number, number][] | null) => {
    strokeRef.current = pts;
    setDrawingState(pts);
  };
  const svgRef = useRef<SVGSVGElement>(null);
  const [w, h] = aspect.split(":").map(Number);
  const vw = 1000;
  const vh = Math.round((1000 * (h || 9)) / (w || 16));
  const full = paths.length >= max;
  const label = param.label ?? "Motion paths";

  function point(e: React.PointerEvent): [number, number] {
    const r = svgRef.current!.getBoundingClientRect();
    const clamp = (n: number) => Math.min(1, Math.max(0, n));
    return [clamp((e.clientX - r.left) / r.width), clamp((e.clientY - r.top) / r.height)];
  }

  function finish() {
    const stroke = strokeRef.current;
    if (!stroke) return;
    const length = stroke.slice(1).reduce((sum, p, i) => sum + Math.hypot(p[0] - stroke[i][0], p[1] - stroke[i][1]), 0);
    const pts = length < TAP ? [stroke[0]] : thin(stroke, maxPoints);
    setDrawing(null);
    onChange([...paths, { points: pts.map(([x, y]) => [round(x), round(y)]) }]);
  }

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-baseline justify-between gap-3">
        <span id="motion-paths-label" className="text-xs text-muted">
          {label}
        </span>
        <span className="tabular text-2xs text-faint">
          {paths.length} of {max}
        </span>
      </div>

      {!frame ? (
        <div
          className="grid place-items-center rounded-[var(--radius-control)] border border-dashed border-line-soft px-6 text-center text-xs leading-relaxed text-faint"
          style={{ aspectRatio: `${w} / ${h}` }}
        >
          Add a start frame above, then draw on it where things should move.
        </div>
      ) : (
        <div
          className="relative overflow-hidden rounded-[var(--radius-frame)] border border-line-soft bg-bay"
          style={{ aspectRatio: `${w} / ${h}` }}
        >
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src={mediaUrl(frame.url)} alt="" className="absolute inset-0 size-full object-cover" draggable={false} />
          <svg
            ref={svgRef}
            role="img"
            aria-labelledby="motion-paths-label"
            aria-describedby="motion-paths-help"
            viewBox={`0 0 ${vw} ${vh}`}
            preserveAspectRatio="none"
            className={cn("absolute inset-0 size-full touch-none", full ? "cursor-not-allowed" : "cursor-crosshair")}
            onPointerDown={(e) => {
              if (full || e.button !== 0) return;
              e.currentTarget.setPointerCapture(e.pointerId);
              setDrawing([point(e)]);
            }}
            onPointerMove={(e) => {
              const stroke = strokeRef.current;
              if (!stroke) return;
              const p = point(e);
              const last = stroke[stroke.length - 1];
              if (Math.hypot(p[0] - last[0], p[1] - last[1]) >= MIN_STEP) setDrawing([...stroke, p]);
            }}
            onPointerUp={finish}
            onPointerCancel={() => setDrawing(null)}
          >
            <defs>
              {COLORS.map((c, i) => (
                <marker key={c} id={`mp-arrow-${i}`} viewBox="0 0 10 10" refX="6" refY="5" markerWidth="5" markerHeight="5"
                        orient="auto-start-reverse">
                  <path d="M0,0 L10,5 L0,10 z" fill={c} />
                </marker>
              ))}
            </defs>
            {paths.map((p, i) => (
              <PathShape key={i} path={p} color={i % COLORS.length} vw={vw} vh={vh} />
            ))}
            {drawing && <PathShape path={{ points: drawing }} color={paths.length % COLORS.length} vw={vw} vh={vh} live />}
          </svg>
        </div>
      )}

      <p id="motion-paths-help" className="text-2xs leading-relaxed text-faint">
        {param.help ?? "Drag to draw where something should go; tap to pin something in place."}
        {full && ` You've drawn the most this model takes (${max}).`}
      </p>

      {paths.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5">
          {paths.map((p, i) => (
            <span
              key={i}
              className="flex h-7 items-center gap-1.5 rounded-full border border-line-soft pl-2.5 pr-1 text-2xs text-muted"
            >
              <span aria-hidden className="size-2 rounded-full" style={{ background: COLORS[i % COLORS.length] }} />
              {describe(p)}
              <button
                type="button"
                aria-label={`Remove ${describe(p).toLowerCase()}`}
                onClick={() => onChange(paths.filter((_, j) => j !== i))}
                className="grid size-5 place-items-center rounded-full text-faint hover:bg-raised hover:text-chalk"
              >
                <X className="size-3" aria-hidden />
              </button>
            </span>
          ))}
          <button
            type="button"
            onClick={() => onChange(paths.slice(0, -1))}
            className="flex h-7 items-center gap-1 rounded-full px-2 text-2xs text-muted hover:text-chalk"
          >
            <Undo2 className="size-3" aria-hidden />
            Undo
          </button>
          <button
            type="button"
            onClick={() => onChange([])}
            className="flex h-7 items-center rounded-full px-2 text-2xs text-muted hover:text-chalk"
          >
            Clear all
          </button>
        </div>
      )}
    </div>
  );
}

function PathShape({ path, color, vw, vh, live }: { path: MotionPath; color: number; vw: number; vh: number; live?: boolean }) {
  const c = COLORS[color];
  const pts = path.points.map(([x, y]) => [x * vw, y * vh] as const);
  if (pts.length === 1) {
    const [x, y] = pts[0];
    return (
      <g>
        <circle cx={x} cy={y} r={16} fill="none" stroke={c} strokeWidth={4} />
        <circle cx={x} cy={y} r={5} fill={c} />
      </g>
    );
  }
  const d = pts.map(([x, y], i) => `${i ? "L" : "M"}${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
  return (
    <g opacity={live ? 0.8 : 1}>
      <path d={d} fill="none" stroke="rgba(0,0,0,.45)" strokeWidth={10} strokeLinecap="round" strokeLinejoin="round" />
      <path d={d} fill="none" stroke={c} strokeWidth={5} strokeLinecap="round" strokeLinejoin="round"
            markerEnd={live ? undefined : `url(#mp-arrow-${color})`} />
      <circle cx={pts[0][0]} cy={pts[0][1]} r={7} fill={c} />
    </g>
  );
}

/** "Path, moves right" / "Pin": what the chip and its remove button say. */
function describe(path: MotionPath): string {
  if (path.points.length === 1) return "Pin";
  const [x0, y0] = path.points[0];
  const [x1, y1] = path.points[path.points.length - 1];
  const dx = x1 - x0;
  const dy = y1 - y0;
  const horiz = Math.abs(dx) > 0.05 ? (dx > 0 ? "right" : "left") : "";
  const vert = Math.abs(dy) > 0.05 ? (dy > 0 ? "down" : "up") : "";
  const dir = [vert, horiz].filter(Boolean).join(" and ");
  return dir ? `Path, moves ${dir}` : "Path, loops back";
}

function thin(points: [number, number][], max: number): [number, number][] {
  if (points.length <= max) return points;
  const step = (points.length - 1) / (max - 1);
  return Array.from({ length: max }, (_, i) => points[Math.round(i * step)]);
}

function round(n: number) {
  return Math.round(n * 10000) / 10000;
}

