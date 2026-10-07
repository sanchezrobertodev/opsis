"use client";

import { Clapperboard, Pause, Play } from "lucide-react";
import Link from "next/link";
import { useRef, useState } from "react";
import { type Asset, mediaUrl } from "@/lib/api";
import { cn } from "@/lib/cn";
import { allShots, type CinemaDoc, type Shot, type Take } from "@/lib/cinema";
import type { Run } from "@/lib/runs";
import { Button, Segmented } from "@/components/ui/controls";
import type { Launch } from "./Editor";

type Row = { shot: Shot; n: number; asset: Asset };

/** The picked takes in story order: trim each, choose cut or fade into it, watch it through, export the film. */
export function CutTab({ projectId, title, doc, update, takes, launch, runs }: {
  projectId: string; title: string; doc: CinemaDoc; update: (fn: (d: CinemaDoc) => CinemaDoc) => void; takes: Take[];
  launch: Launch; runs: Run[];
}) {
  const assets = new Map<string, Asset>(takes.flatMap((t) => (t.asset ? [[t.asset.id, t.asset as Asset]] : [])));
  const rows: Row[] = allShots(doc).flatMap(({ shot, index }) => {
    const asset = shot.take_asset_id ? assets.get(shot.take_asset_id) : undefined;
    return asset ? [{ shot, n: index, asset }] : [];
  });
  const missing = allShots(doc).length - rows.length;
  const exports = runs.filter((r) => r.recipe === "cinema_export");
  const latest = exports[exports.length - 1];

  const patch = (id: string, p: Partial<Shot>) => update((d) => ({
    ...d, scenes: d.scenes.map((sc) => ({ ...sc, shots: sc.shots.map((s) => (s.id === id ? { ...s, ...p } : s)) })) }));

  const exportCut = () => launch({ recipe: "cinema_export", input: {
    project_id: projectId, title, fade_seconds: 0.5,
    clips: rows.map((r, i) => ({ asset_id: r.asset.id, trim_in: r.shot.trim_in, trim_out: r.shot.trim_out,
                                 transition: i === 0 ? "cut" : r.shot.transition })) } }, "Export");

  if (!rows.length) {
    return (
      <div className="p-6 text-sm text-muted">
        Pick a take for at least one shot in the storyboard; picked takes line up here in story order.
      </div>
    );
  }
  return (
    <div className="grid h-full grid-cols-1 lg:grid-cols-[minmax(0,1fr)_420px]">
      <div className="min-h-0 overflow-y-auto p-6 scrollbar-quiet">
        <SequencePlayer rows={rows} />
        {missing > 0 && <p className="mt-3 text-2xs text-faint">{missing} shot{missing === 1 ? " has" : "s have"} no picked take yet and {missing === 1 ? "is" : "are"} left out.</p>}
      </div>
      <aside className="flex min-h-0 flex-col gap-3 overflow-y-auto border-t border-line-soft p-5 scrollbar-quiet lg:border-t-0 lg:border-l">
        <ol className="flex flex-col gap-2">
          {rows.map((r, i) => {
            const length = (r.asset.duration_ms ?? 0) / 1000;
            return (
              <li key={r.shot.id} className="flex gap-3 rounded-[var(--radius-panel)] bg-panel p-2.5">
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img src={mediaUrl(r.asset.thumb_url ?? r.asset.poster_url)} alt="" className="h-14 w-24 shrink-0 rounded-[var(--radius-frame)] bg-bay object-cover" />
                <div className="flex min-w-0 flex-1 flex-col gap-1.5">
                  <span className="truncate text-xs text-chalk"><span className="tabular text-faint">{String(r.n).padStart(2, "0")}</span> {r.shot.action}</span>
                  <div className="flex flex-wrap items-center gap-2 text-2xs text-faint">
                    <label className="flex items-center gap-1">In
                      <input type="number" min={0} max={length} step={0.1} value={r.shot.trim_in} aria-label={`Shot ${r.n} starts at`}
                             onChange={(e) => patch(r.shot.id, { trim_in: Math.max(0, Number(e.target.value) || 0) })}
                             className="tabular h-7 w-14 rounded-[4px] border border-line-soft bg-bay px-1.5 text-xs text-chalk" />
                    </label>
                    <label className="flex items-center gap-1">Out
                      <input type="number" min={0.1} max={length} step={0.1} value={r.shot.trim_out ?? length} aria-label={`Shot ${r.n} ends at`}
                             onChange={(e) => { const v = Number(e.target.value); patch(r.shot.id, { trim_out: v && v < length ? v : null }); }}
                             className="tabular h-7 w-14 rounded-[4px] border border-line-soft bg-bay px-1.5 text-xs text-chalk" />
                    </label>
                    <span className="tabular">of {length.toFixed(1)} s</span>
                    {i > 0 && (
                      <div className="ml-auto w-28">
                        <Segmented label={`Into shot ${r.n}`} value={r.shot.transition} options={["cut", "fade"] as const}
                                   render={(v) => (v === "cut" ? "Cut" : "Fade")} onChange={(v) => patch(r.shot.id, { transition: v })} />
                      </div>
                    )}
                  </div>
                </div>
              </li>
            );
          })}
        </ol>
        <div className="mt-auto flex flex-col gap-2 pt-2">
          {latest && (
            <p className="text-xs text-muted">
              {latest.status === "succeeded" ? "Exported. " : latest.status === "failed" ? <span className="text-fault">{latest.error} </span> : "Exporting… "}
              <Link href={`/runs/${latest.id}`} className="text-chalk underline">Open the export</Link>
            </p>
          )}
          <Button tone="primary" size="lg" onClick={() => void exportCut()}>
            <Clapperboard className="size-4" aria-hidden />
            Export the film
          </Button>
        </div>
      </aside>
    </div>
  );
}

/** Plays the picked takes back to back, honouring each trim. */
function SequencePlayer({ rows }: { rows: Row[] }) {
  const video = useRef<HTMLVideoElement>(null);
  const [at, setAt] = useState(0);
  const [playing, setPlaying] = useState(false);
  const row = rows[Math.min(at, rows.length - 1)];
  const end = row.shot.trim_out ?? Infinity;

  function toggle() {
    const v = video.current;
    if (!v) return;
    if (v.paused) { void v.play(); setPlaying(true); } else { v.pause(); setPlaying(false); }
  }

  return (
    <div className="flex max-w-4xl flex-col gap-3">
      <video ref={video} key={row.asset.id} src={mediaUrl(row.asset.proxy_url ?? row.asset.url)} playsInline
             autoPlay={playing} className="aspect-video w-full rounded-[var(--radius-frame)] bg-black object-contain"
             onLoadedMetadata={(e) => { e.currentTarget.currentTime = row.shot.trim_in; }}
             onTimeUpdate={(e) => {
               if (e.currentTarget.currentTime >= end) {
                 if (at < rows.length - 1) setAt(at + 1); else { e.currentTarget.pause(); setPlaying(false); }
               }
             }}
             onEnded={() => { if (at < rows.length - 1) setAt(at + 1); else setPlaying(false); }} />
      <div className="flex items-center gap-3">
        <Button size="sm" onClick={toggle} aria-label={playing ? "Pause" : "Play"}>
          {playing ? <Pause className="size-4" aria-hidden /> : <Play className="size-4" aria-hidden />}
        </Button>
        <ol className="flex flex-1 gap-1" aria-label="Shots in the cut">
          {rows.map((r, i) => (
            <li key={r.shot.id} className="flex-1">
              <button type="button" onClick={() => setAt(i)} aria-label={`Go to shot ${r.n}`} aria-current={i === at}
                      className={cn("h-2 w-full rounded-full", i === at ? "bg-tungsten" : i < at ? "bg-tungsten-dim" : "bg-line")} />
            </li>
          ))}
        </ol>
        <span className="tabular text-2xs text-faint">Shot {at + 1} of {rows.length}</span>
      </div>
    </div>
  );
}
