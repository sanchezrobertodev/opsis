"use client";

import type { PlayerRef } from "@remotion/player";
import { useQueries } from "@tanstack/react-query";
import { Clapperboard } from "lucide-react";
import dynamic from "next/dynamic";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, type Asset, mediaUrl, unwrap } from "@/lib/api";
import { type CinemaDoc, type Shot, type Take, useDialogue } from "@/lib/cinema";
import { type AudioClip, audioSeconds, type Cut, cutOf, DEFAULT_CARD, exportInput, FPS, itemsOf, newCutId,
         placeSounds, sequence, timecode, toFrames, type Track } from "@/lib/cut";
import { keys } from "@/lib/queries";
import type { Run } from "@/lib/runs";
import { Button, Switch } from "@/components/ui/controls";
import { AddButtons, AddSound, ItemEditor, SoundEditor } from "./CutInspector";
import type { PreviewItem, PreviewSound } from "./CutPreview";
import { CutTimeline, type Selection } from "./CutTimeline";
import type { Launch } from "./Editor";

// the player draws with the browser's media elements only
const CutPlayer = dynamic(() => import("./CutPreview").then((m) => m.CutPlayer), {
  ssr: false, loading: () => <div className="aspect-video w-full rounded-[var(--radius-frame)] bg-black" />,
});

const SIZE: Record<string, [number, number]> = { "16:9": [1280, 720], "9:16": [720, 1280], "1:1": [1024, 1024],
                                                 "21:9": [1344, 576], "4:3": [1024, 768] };

/** Assets a cut's sounds use (fetched one by one, cached like any asset). */
function useSoundAssets(ids: string[]) {
  return useQueries({
    queries: ids.map((id) => ({
      queryKey: keys.asset(id),
      queryFn: () => unwrap(api.GET("/assets/{asset_id}", { params: { path: { asset_id: id } } })),
      staleTime: 60_000,
    })),
    combine: (results) => new Map(results.flatMap((r) => (r.data ? [[r.data.id, r.data as Asset] as const] : []))),
  });
}

/**
 * The cut: picked takes and title cards in order with cuts or fades, and dialogue, music and SFX tracks under
 * them. The preview plays the same frames the export renders.
 */
export function CutTab({ projectId, title, doc, update, takes, launch, runs }: {
  projectId: string; title: string; doc: CinemaDoc; update: (fn: (d: CinemaDoc) => CinemaDoc) => void; takes: Take[];
  launch: Launch; runs: Run[];
}) {
  const takeAssets = useMemo(() => new Map<string, Asset>(takes.flatMap((t) => (t.asset ? [[t.asset.id, t.asset as Asset]] : []))),
                             [takes]);
  const { entries, frames } = useMemo(() => sequence(doc, takeAssets), [doc, takeAssets]);
  const cut = cutOf(doc);
  const sounds = useMemo(() => placeSounds(cut, entries), [cut, entries]);
  const soundAssets = useSoundAssets([...new Set(cut.audio.map((a) => a.asset_id))]);
  const dialogue = useDialogue(projectId);
  const player = useRef<PlayerRef>(null);
  const [playhead, setPlayhead] = useState(0);
  const [selected, setSelected] = useState<Selection>(null);
  const [adding, setAdding] = useState<Track | null>(null);
  const exports = runs.filter((r) => r.recipe === "cinema_export");
  const latest = exports[exports.length - 1];
  const [w, h] = SIZE[doc.settings.aspect_ratio] ?? SIZE["16:9"];

  // the player tells the timeline where it is
  const [ready, setReady] = useState(false);
  useEffect(() => {
    const p = player.current;
    if (!p) return;
    const on = (e: { detail: { frame: number } }) => setPlayhead(e.detail.frame);
    p.addEventListener("frameupdate", on);
    return () => p.removeEventListener("frameupdate", on);
  }, [ready]);
  useEffect(() => {
    const t = setInterval(() => { if (player.current) { setReady(true); clearInterval(t); } }, 200);
    return () => clearInterval(t);
  }, []);
  const seek = useCallback((f: number) => { player.current?.seekTo(f); setPlayhead(f); }, []);

  const setCut = (fn: (c: Cut) => Cut) => update((d) => ({ ...d, cut: fn({ ...cutOf(d), items: itemsOf(sequence(d, takeAssets).entries) }) }));
  const patchShot = (id: string, p: Partial<Shot>) => update((d) => ({
    ...d, scenes: d.scenes.map((sc) => ({ ...sc, shots: sc.shots.map((s) => (s.id === id ? { ...s, ...p } : s)) })) }));
  const patchSound = (id: string, p: Partial<AudioClip>) => setCut((c) => ({ ...c, audio: c.audio.map((a) => (a.id === id ? { ...a, ...p } : a)) }));
  const addSound = useCallback((track: Track, asset: Asset) => {
    const clip: AudioClip = { id: newCutId("a"), track, asset_id: asset.id, label: asset.title ?? "", start: playhead / FPS,
                              trim_in: 0, length: null, volume: track === "music" ? 0.6 : 1, fade_in: track === "music" ? 1 : 0,
                              fade_out: track === "music" ? 2 : 0 };
    update((d) => ({ ...d, cut: { ...cutOf(d), items: itemsOf(sequence(d, takeAssets).entries),
                                  audio: [...cutOf(d).audio, clip] } }));
    setSelected({ kind: "sound", id: clip.id });
  }, [playhead, update, takeAssets]);

  // each shot's own speech on the dialogue track, muting the take where it's the same speech
  const speech = (dialogue.data ?? []).filter((d) => entries.some((e) => e.shot?.id === d.shot_key)
    && !cut.audio.some((a) => a.shot_id === d.shot_key && a.track === "dialogue"));
  const addDialogueTracks = () => setCut((c) => {
    const muted = new Set(speech.filter((d) => d.lip_synced).map((d) => d.shot_key));
    return {
      ...c,
      items: c.items.map((i) => (i.shot_id && muted.has(i.shot_id) ? { ...i, volume: 0 } : i)),
      audio: [...c.audio, ...speech.map((d): AudioClip => ({
        id: newCutId("a"), track: "dialogue", asset_id: d.asset.id, shot_id: d.shot_key, start: 0, trim_in: 0,
        length: null, volume: 1, fade_in: 0, fade_out: 0,
        label: `Shot ${String(entries.find((e) => e.shot?.id === d.shot_key)?.n ?? "").padStart(2, "0")} dialogue` }))],
    };
  });

  const preview = useMemo(() => ({
    items: entries.map((e, i): PreviewItem => ({
      key: e.item.id, frames: e.frames, overlapIn: e.overlap, overlapOut: entries[i + 1]?.overlap ?? 0,
      card: e.card, take: e.asset ? { src: mediaUrl(e.asset.url) ?? e.asset.url, trimBefore: toFrames(e.trimFrom),
                                      volume: e.item.volume } : undefined,
    })),
    sounds: sounds.flatMap((s): PreviewSound[] => {
      const asset = soundAssets.get(s.asset_id);
      const seconds = audioSeconds(s, asset);
      return asset && seconds > 0 ? [{ key: s.id, track: s.track, src: mediaUrl(asset.url) ?? asset.url,
        from: toFrames(s.start), frames: Math.min(toFrames(seconds), Math.max(0, frames - toFrames(s.start))),
        trimBefore: toFrames(s.trim_in), volume: s.volume, fadeIn: toFrames(s.fade_in), fadeOut: toFrames(s.fade_out) }] : [];
    }).filter((s) => s.frames > 0),
  }), [entries, sounds, soundAssets, frames]);

  if (!entries.length) {
    return (
      <div className="p-6 text-sm text-muted">
        Pick a take for at least one shot in the storyboard; picked takes line up here in story order.
      </div>
    );
  }
  const picked = selected?.kind === "item" ? entries.findIndex((e) => e.item.id === selected.id) : -1;
  const sound = selected?.kind === "sound" ? cut.audio.find((a) => a.id === selected.id) : undefined;
  const missing = doc.scenes.flatMap((s) => s.shots).filter((s) => !s.take_asset_id).length;

  const move = (from: number, to: number) => setCut((c) => {
    const items = [...c.items];
    const [it] = items.splice(from, 1);
    items.splice(to, 0, it);
    return { ...c, items };
  });

  return (
    <div className="grid h-full grid-cols-1 lg:grid-cols-[minmax(0,1fr)_380px]">
      <div className="flex min-h-0 flex-col gap-4 overflow-y-auto p-6 scrollbar-quiet">
        <div className="mx-auto w-full max-w-4xl">
          <CutPlayer ref={player} frames={frames} width={w} height={h} duck={cut.duck_music} {...preview} />
        </div>
        <div className="flex flex-wrap items-center gap-3 text-2xs text-faint">
          <span className="tabular text-xs text-chalk" aria-label="Film length">{timecode(frames / FPS)}</span>
          <span>{entries.length} item{entries.length === 1 ? "" : "s"}{missing ? `; ${missing} shot${missing === 1 ? "" : "s"} without a picked take left out` : ""}</span>
        </div>
        <CutTimeline entries={entries} sounds={sounds} assets={soundAssets} frames={frames} playhead={playhead}
                     selected={selected} onSelect={setSelected} onSeek={seek}
                     onMoveSound={(id, start) => patchSound(id, { start })} />
        <AddButtons onCard={() => {
          const at = entries.findIndex((e) => e.start + e.frames > playhead);
          const item = { id: newCutId("i"), card: { ...DEFAULT_CARD }, volume: 1 };
          setCut((c) => { const items = [...c.items]; items.splice(at < 0 ? items.length : at, 0, item); return { ...c, items }; });
          setSelected({ kind: "item", id: item.id });
        }} onSound={setAdding} onDialogue={addDialogueTracks} dialogueReady={speech.length} />
      </div>

      <aside className="flex min-h-0 flex-col gap-5 overflow-y-auto border-t border-line-soft p-5 scrollbar-quiet lg:border-t-0 lg:border-l">
        {picked >= 0 ? (
          <ItemEditor entry={entries[picked]} index={picked} count={entries.length}
                      onMove={(to) => { move(picked, to); }}
                      onShot={(p) => entries[picked].shot && patchShot(entries[picked].shot!.id, p)}
                      onItem={(p) => setCut((c) => ({ ...c, items: c.items.map((i) => (i.id === entries[picked].item.id
                        || (i.shot_id && i.shot_id === entries[picked].shot?.id) ? { ...i, ...p } : i)) }))}
                      onRemove={() => { setCut((c) => ({ ...c, items: c.items.filter((i) => i.id !== entries[picked].item.id) })); setSelected(null); }} />
        ) : sound ? (
          <SoundEditor clip={sounds.find((s) => s.id === sound.id) ?? sound} asset={soundAssets.get(sound.asset_id)}
                       onChange={(p) => patchSound(sound.id, p)}
                       onRemove={() => { setCut((c) => ({ ...c, audio: c.audio.filter((a) => a.id !== sound.id) })); setSelected(null); }} />
        ) : (
          <p className="text-xs leading-relaxed text-muted">
            Select a shot, card or sound on the timeline to edit it. New cards and sounds go in at the playhead.
          </p>
        )}
        <div className="mt-auto flex flex-col gap-3 border-t border-line-soft pt-4">
          <Switch label="Lower the music under dialogue" checked={cut.duck_music}
                  onChange={(v) => setCut((c) => ({ ...c, duck_music: v }))} />
          {latest && (
            <p className="text-xs text-muted">
              {latest.status === "succeeded"
                ? `Exported, ${timecode(Number(latest.output?.duration_ms ?? 0) / 1000)}. `
                : latest.status === "failed" ? <span className="text-fault">{latest.error} </span> : "Exporting… "}
              <Link href={`/runs/${latest.id}`} className="text-chalk underline">Open the export</Link>
            </p>
          )}
          <Button tone="primary" size="lg" onClick={() => void launch({ recipe: "cinema_export", input: exportInput(projectId, title, doc, entries) }, "Export")}>
            <Clapperboard className="size-4" aria-hidden />
            Export the film
          </Button>
        </div>
      </aside>
      {adding && <AddSound track={adding} open onClose={() => setAdding(null)} onAdd={(a) => addSound(adding, a)} />}
    </div>
  );
}
