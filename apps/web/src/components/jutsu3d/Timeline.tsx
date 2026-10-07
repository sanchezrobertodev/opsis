"use client";

import { X } from "lucide-react";
import { useRef } from "react";
import { cn } from "@/lib/cn";
import { KIND_LABEL, type SceneDoc, useScene } from "@/lib/jutsu3d";

/** The scene's time: a ruler with a playhead, and a row of keys for each keyframed object. */
export function Timeline({ doc, time, selected, onTime }: {
  doc: SceneDoc; time: number; selected: string | null; onTime: (t: number) => void;
}) {
  const ruler = useRef<HTMLDivElement>(null);
  const { duration } = doc.timeline;
  const pct = (t: number) => `${(Math.min(duration, Math.max(0, t)) / duration) * 100}%`;
  const ticks = Array.from({ length: Math.floor(duration) + 1 }, (_, i) => i);
  const rows = doc.timeline.tracks.filter((t) => t.keys.length);
  const nameOf = (id: string) => {
    const o = doc.objects.find((x) => x.id === id);
    return o ? o.name || KIND_LABEL[o.kind] : id;
  };

  function scrub(clientX: number) {
    const r = ruler.current?.getBoundingClientRect();
    if (!r) return;
    const t = ((clientX - r.left) / r.width) * duration;
    onTime(Math.round(Math.min(duration, Math.max(0, t)) * doc.timeline.fps) / doc.timeline.fps);
  }
  const dropKey = (objectId: string, t: number) => useScene.getState().change((d) => ({ ...d, timeline: {
    ...d.timeline, tracks: d.timeline.tracks.map((tr) => tr.object_id !== objectId ? tr
      : { ...tr, keys: tr.keys.filter((k) => k.t !== t) }) } }));

  return (
    <div className="grid grid-cols-[120px_minmax(0,1fr)] gap-x-2 px-4 pb-3 pt-1 text-2xs">
      <span className="tabular self-end text-faint">{time.toFixed(2)} s of {duration} s</span>
      <div ref={ruler} role="slider" tabIndex={0} aria-label="Playhead" aria-valuemin={0} aria-valuemax={duration}
           aria-valuenow={Math.round(time * 100) / 100}
           onPointerDown={(e) => { e.currentTarget.setPointerCapture(e.pointerId); scrub(e.clientX); }}
           onPointerMove={(e) => { if (e.buttons) scrub(e.clientX); }}
           onKeyDown={(e) => {
             const step = 1 / doc.timeline.fps;
             if (e.key === "ArrowRight") onTime(Math.min(duration, time + step));
             if (e.key === "ArrowLeft") onTime(Math.max(0, time - step));
           }}
           className="relative h-6 cursor-ew-resize rounded-[3px] bg-bay outline-none focus-visible:ring-1 focus-visible:ring-tungsten">
        {ticks.map((s) => (
          <span key={s} className="tabular absolute top-0 h-full border-l border-line-soft pl-0.5 text-[9px] text-faint" style={{ left: pct(s) }}>
            {s % (duration > 20 ? 5 : 1) === 0 ? s : ""}
          </span>
        ))}
        <span aria-hidden className="absolute -top-0.5 h-[calc(100%+4px)] w-px bg-tungsten" style={{ left: pct(time) }} />
      </div>
      {rows.length === 0 && (
        <p className="col-span-2 mt-1.5 text-faint">
          Move the playhead, place an object, and press K (or Key) to keyframe it. The camera keys its position, aim and lens.
        </p>
      )}
      {rows.map((tr) => (
        <div key={tr.object_id} className="contents">
          <span className={cn("mt-1.5 truncate", tr.object_id === selected ? "text-chalk" : "text-muted")}>{nameOf(tr.object_id)}</span>
          <div className="relative mt-1.5 h-5 rounded-[3px] bg-bay/60">
            {tr.keys.map((k) => (
              <span key={k.t} className="group absolute top-1/2 -translate-x-1/2 -translate-y-1/2" style={{ left: pct(k.t) }}>
                <button type="button" aria-label={`Key at ${k.t} s on ${nameOf(tr.object_id)}`} onClick={() => onTime(k.t)}
                        className={cn("block size-2.5 rotate-45 border", Math.abs(k.t - time) < 1e-3 ? "border-tungsten bg-tungsten" : "border-muted bg-panel hover:bg-tungsten/60")} />
                <button type="button" aria-label={`Remove the key at ${k.t} s`} onClick={() => dropKey(tr.object_id, k.t)}
                        className="absolute -top-3 left-1/2 hidden -translate-x-1/2 text-faint hover:text-chalk group-hover:block">
                  <X className="size-2.5" aria-hidden />
                </button>
              </span>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}
