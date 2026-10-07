"use client";

import { Link2 } from "lucide-react";
import { useRef, useState } from "react";
import type { Asset } from "@/lib/api";
import { cn } from "@/lib/cn";
import { type AudioClip, audioSeconds, type Entry, FPS, timecode, toFrames, TRACKS } from "@/lib/cut";

export type Selection = { kind: "item"; id: string } | { kind: "sound"; id: string } | null;

const TRACK_TONE: Record<string, string> = {
  dialogue: "bg-[#3b5a4a] border-[#5f8a72]", music: "bg-[#4a4060] border-[#7468a0]", sfx: "bg-[#5a4630] border-[#9a7a4c]",
};

/**
 * The cut laid out in time: the picture track (takes and cards, overlapping where they fade) and the dialogue,
 * music and SFX tracks. Click the ruler to move the playhead; drag a sound sideways to move it.
 */
export function CutTimeline({ entries, sounds, assets, frames, playhead, selected, onSelect, onSeek, onMoveSound }: {
  entries: Entry[]; sounds: AudioClip[]; assets: Map<string, Asset>; frames: number; playhead: number;
  selected: Selection; onSelect: (s: Selection) => void; onSeek: (frame: number) => void;
  onMoveSound: (id: string, start: number) => void;
}) {
  const lanes = useRef<HTMLDivElement>(null);
  const [drag, setDrag] = useState<{ id: string; dx: number } | null>(null);
  const span = Math.max(frames, FPS * 5);
  const pct = (f: number) => `${(f / span) * 100}%`;
  const seconds = span / FPS;
  const tick = seconds > 60 ? 10 : seconds > 20 ? 5 : seconds > 8 ? 2 : 1;

  const frameAt = (clientX: number) => {
    const box = lanes.current?.getBoundingClientRect();
    return box ? Math.max(0, Math.min(span, ((clientX - box.left) / box.width) * span)) : 0;
  };

  return (
    <div className="flex select-none flex-col gap-1.5" aria-label="Timeline">
      <div className="flex">
        <span className="w-24 shrink-0" />
        <div className="relative h-5 flex-1 cursor-pointer border-b border-line-soft" role="slider" tabIndex={0}
             aria-label="Playhead" aria-valuemin={0} aria-valuemax={frames} aria-valuenow={playhead}
             aria-valuetext={timecode(playhead / FPS)}
             onPointerDown={(e) => onSeek(Math.round(frameAt(e.clientX)))}
             onKeyDown={(e) => {
               if (e.key === "ArrowRight") onSeek(Math.min(frames, playhead + FPS));
               if (e.key === "ArrowLeft") onSeek(Math.max(0, playhead - FPS));
             }}>
          {Array.from({ length: Math.floor(seconds / tick) + 1 }, (_, i) => i * tick).map((t) => (
            <span key={t} className="tabular absolute top-0 -translate-x-1/2 text-[10px] text-faint" style={{ left: pct(t * FPS) }}>
              {t}s
            </span>
          ))}
        </div>
      </div>

      <div className="relative flex flex-col gap-1.5 overflow-hidden">
        <Lane label="Picture">
          {entries.map((e) => {
            const on = selected?.kind === "item" && selected.id === e.item.id;
            return (
              <button key={e.item.id} type="button" onClick={() => onSelect({ kind: "item", id: e.item.id })}
                      aria-pressed={on} title={e.card ? e.card.text : e.shot?.action}
                      className={cn("absolute inset-y-0 overflow-hidden rounded-[3px] border text-left text-[10px] leading-tight",
                                    e.card ? "border-line bg-raised" : "border-[#55647a] bg-[#2c3646]",
                                    on && "ring-2 ring-tungsten")}
                      style={{ left: pct(e.start), width: pct(e.frames), zIndex: on ? 2 : 1 }}>
                {e.overlap > 0 && <span className="absolute inset-y-0 left-0 bg-gradient-to-r from-black/50 to-transparent"
                                        style={{ width: `${(e.overlap / e.frames) * 100}%` }} aria-hidden />}
                <span className="relative block truncate px-1.5 pt-1 text-chalk">
                  {e.card ? `“${e.card.text}”` : `${String(e.n).padStart(2, "0")} ${e.shot?.action ?? ""}`}
                </span>
              </button>
            );
          })}
        </Lane>
        <div ref={lanes} className="pointer-events-none absolute inset-y-0 left-24 right-0" aria-hidden />
        {TRACKS.map((t) => (
          <Lane key={t.id} label={t.label}>
            {sounds.filter((s) => s.track === t.id).map((s) => {
              const len = toFrames(audioSeconds(s, assets.get(s.asset_id)));
              const tied = Boolean(s.shot_id);
              const dx = drag?.id === s.id ? drag.dx : 0;
              const on = selected?.kind === "sound" && selected.id === s.id;
              return (
                <button key={s.id} type="button" aria-pressed={on}
                        aria-label={`${s.label || assets.get(s.asset_id)?.title || "Sound"} at ${timecode(s.start)}`}
                        onClick={() => onSelect({ kind: "sound", id: s.id })}
                        onPointerDown={(e) => {
                          if (tied) return;
                          const from = e.clientX;
                          e.currentTarget.setPointerCapture(e.pointerId);
                          setDrag({ id: s.id, dx: 0 });
                          const target = e.currentTarget;
                          const move = (ev: PointerEvent) => setDrag({ id: s.id, dx: frameAt(ev.clientX) - frameAt(from) });
                          const up = (ev: PointerEvent) => {
                            target.removeEventListener("pointermove", move);
                            target.removeEventListener("pointerup", up);
                            const moved = frameAt(ev.clientX) - frameAt(from);
                            setDrag(null);
                            if (Math.abs(moved) >= 1) onMoveSound(s.id, Math.max(0, Math.round((s.start * FPS + moved) / (FPS / 10)) / 10));
                          };
                          target.addEventListener("pointermove", move);
                          target.addEventListener("pointerup", up);
                        }}
                        className={cn("absolute inset-y-0 flex items-center gap-1 overflow-hidden rounded-[3px] border px-1.5 text-[10px] text-chalk",
                                      TRACK_TONE[s.track], tied ? "cursor-default" : "cursor-grab active:cursor-grabbing",
                                      on && "ring-2 ring-tungsten")}
                        style={{ left: pct(Math.max(0, toFrames(s.start) + dx)), zIndex: on ? 2 : 1,
                                 // what plays past the end of the film is cut, so the block stops there too
                                 width: pct(Math.max(2, Math.min(len, span - Math.max(0, toFrames(s.start) + dx)))) }}>
                  {tied && <Link2 className="size-3 shrink-0" aria-label="Moves with its shot" />}
                  <span className="truncate">{s.label || assets.get(s.asset_id)?.title || "Sound"}</span>
                </button>
              );
            })}
          </Lane>
        ))}
        <span className="pointer-events-none absolute inset-y-0 w-px bg-tungsten" aria-hidden
              style={{ left: `calc(6rem + (100% - 6rem) * ${playhead / span})` }} />
      </div>
    </div>
  );
}

function Lane({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex h-9">
      <span className="w-24 shrink-0 self-center pr-2 text-2xs text-muted">{label}</span>
      <div className="relative flex-1 rounded-[3px] bg-bay/60">{children}</div>
    </div>
  );
}
