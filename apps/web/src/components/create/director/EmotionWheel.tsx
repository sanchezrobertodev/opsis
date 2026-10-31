"use client";

import type { Schemas } from "@opsis/api-client";
import { cn } from "@/lib/cn";

type Emotion = Schemas["EmotionItem"];

// Around the wheel in the usual order, so opposites face each other (joy / sadness, trust / disgust...)
const FAMILIES = ["joy", "trust", "fear", "surprise", "sadness", "disgust", "anger", "anticipation"] as const;
export const FAMILY_COLOR: Record<string, string> = {
  joy: "#f2cf3d", trust: "#9bd770", fear: "#3aa66b", surprise: "#3fb6c9",
  sadness: "#5b86d9", disgust: "#9a6bd1", anger: "#e5484d", anticipation: "#f08a3b",
};
// Intense feelings sit in the middle, mild ones at the rim
const RINGS: Record<number, [number, number]> = { 3: [20, 44], 2: [44, 66], 1: [66, 86] };
const SIZE = 184;
const C = SIZE / 2;

function cell(i: number, level: number): string {
  const [r0, r1] = RINGS[level];
  const span = 360 / FAMILIES.length;
  const a0 = ((i * span - 90 - span / 2 + 1) * Math.PI) / 180;
  const a1 = ((i * span - 90 + span / 2 - 1) * Math.PI) / 180;
  const p = (r: number, a: number) => `${(C + r * Math.cos(a)).toFixed(2)} ${(C + r * Math.sin(a)).toFixed(2)}`;
  return `M ${p(r0, a0)} L ${p(r1, a0)} A ${r1} ${r1} 0 0 1 ${p(r1, a1)} L ${p(r0, a1)} A ${r0} ${r0} 0 0 0 ${p(r0, a0)} Z`;
}

/** Plutchik-style wheel: pick a feeling by family (the petal) and strength (inner = intense). Click it again to clear. */
export function EmotionWheel({ emotions, value, onChange, who }: {
  emotions: Emotion[]; value: string | null | undefined; onChange: (id: string | null) => void; who: string;
}) {
  const chosen = emotions.find((e) => e.id === value);
  return (
    <div className="flex flex-col items-center gap-2">
      <svg viewBox={`0 0 ${SIZE} ${SIZE}`} width={SIZE} height={SIZE} role="radiogroup" aria-label={`How ${who} feels`}>
        {emotions.map((e) => {
          const i = FAMILIES.indexOf(e.family);
          if (i < 0) return null;
          const selected = e.id === value;
          const pick = () => onChange(selected ? null : e.id);
          return (
            <path key={e.id} d={cell(i, e.level)} role="radio" aria-checked={selected} aria-label={e.label} tabIndex={0}
                  onClick={pick}
                  onKeyDown={(ev) => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); pick(); } }}
                  fill={FAMILY_COLOR[e.family]} fillOpacity={selected ? 1 : { 1: 0.3, 2: 0.55, 3: 0.8 }[e.level]}
                  stroke={selected ? "var(--color-chalk)" : "var(--color-panel)"} strokeWidth={selected ? 2.5 : 1}
                  className={cn("cursor-pointer outline-none transition-[fill-opacity] hover:fill-opacity-100 focus-visible:stroke-chalk")}>
              <title>{e.label}</title>
            </path>
          );
        })}
      </svg>
      <p className="text-xs text-muted">
        {chosen ? <>{who} feels <span className="text-chalk">{chosen.label.toLowerCase()}</span></> : `Pick how ${who} feels`}
      </p>
    </div>
  );
}
