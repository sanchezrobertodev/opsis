"use client";

import { ArrowDown, ArrowUp, AudioLines, LoaderCircle, Trash2, Type } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import { toast } from "sonner";
import { type Asset, isActive, mediaUrl } from "@/lib/api";
import { cn } from "@/lib/cn";
import { type AudioClip, audioSeconds, type CutItem, type Entry, FPS, timecode, type TitleCard, type Track,
         TRACKS } from "@/lib/cut";
import { useAssets, useGeneration } from "@/lib/queries";
import { QuickGenerate } from "@/components/create/QuickGenerate";
import { Button, Dialog, Field, Segmented, Slider } from "@/components/ui/controls";

const INPUT = "tabular h-7 w-16 rounded-[4px] border border-line-soft bg-bay px-1.5 text-xs text-chalk";

function Num({ label, value, onChange, min = 0, max, step = 0.1 }: {
  label: string; value: number; onChange: (v: number) => void; min?: number; max?: number; step?: number;
}) {
  return (
    <label className="flex items-center gap-1.5 text-2xs text-faint">{label}
      <input type="number" min={min} max={max} step={step} value={Number(value.toFixed(2))} aria-label={label}
             onChange={(e) => onChange(Math.max(min, Math.min(max ?? Infinity, Number(e.target.value) || 0)))}
             className={INPUT} />
    </label>
  );
}

/** One picture-track item: reorder it, and trim, fade and level a take, or edit a card. */
export function ItemEditor({ entry, index, count, onMove, onShot, onItem, onRemove }: {
  entry: Entry; index: number; count: number; onMove: (to: number) => void;
  onShot: (patch: Partial<{ trim_in: number; trim_out: number | null; transition: "cut" | "fade" }>) => void;
  onItem: (patch: Partial<CutItem>) => void; onRemove: () => void;
}) {
  const length = (entry.asset?.duration_ms ?? 0) / 1000;
  return (
    <section className="flex flex-col gap-3" aria-label="Selected item">
      <div className="flex items-center gap-2">
        <h3 className="min-w-0 flex-1 truncate text-sm text-chalk">
          {entry.card ? "Title card" : `Shot ${String(entry.n).padStart(2, "0")}`}
        </h3>
        <Button size="sm" tone="ghost" aria-label="Earlier" onClick={() => onMove(index - 1)} disabled={index === 0}>
          <ArrowUp className="size-4" aria-hidden /></Button>
        <Button size="sm" tone="ghost" aria-label="Later" onClick={() => onMove(index + 1)} disabled={index === count - 1}>
          <ArrowDown className="size-4" aria-hidden /></Button>
        {entry.card && <Button size="sm" tone="ghost" aria-label="Remove the card" onClick={onRemove}>
          <Trash2 className="size-4" aria-hidden /></Button>}
      </div>
      {entry.card ? <CardEditor card={entry.card} first={index === 0}
                                onChange={(card) => onItem({ card })} /> : entry.shot && (
        <>
          <p className="line-clamp-2 text-xs text-muted">{entry.shot.action}</p>
          <div className="flex flex-wrap gap-3">
            <Num label="In" value={entry.shot.trim_in} max={length} onChange={(v) => onShot({ trim_in: v })} />
            <Num label="Out" value={entry.shot.trim_out ?? length} min={0.1} max={length}
                 onChange={(v) => onShot({ trim_out: v && v < length ? v : null })} />
            <span className="tabular self-center text-2xs text-faint">of {length.toFixed(1)} s</span>
          </div>
          {index > 0 && (
            <Segmented label="Into this shot" value={entry.shot.transition} options={["cut", "fade"] as const}
                       render={(v) => (v === "cut" ? "Cut" : "Fade")} onChange={(v) => onShot({ transition: v })} />
          )}
          <Field label={`The take's own sound${entry.item.volume === 0 ? " (muted)" : ""}`}>
            <Slider value={entry.item.volume} min={0} max={2} step={0.05} format={(v) => `${Math.round(v * 100)}%`}
                    onChange={(v) => onItem({ volume: v })} />
          </Field>
        </>
      )}
      <p className="tabular text-2xs text-faint">
        {timecode(entry.start / FPS)} to {timecode((entry.start + entry.frames) / FPS)}
        {entry.overlap ? `, fading in over ${(entry.overlap / FPS).toFixed(2)} s` : ""}
      </p>
    </section>
  );
}

function CardEditor({ card, first, onChange }: { card: TitleCard; first: boolean; onChange: (c: TitleCard) => void }) {
  const ids = { text: useId(), sub: useId() };
  const set = (p: Partial<TitleCard>) => onChange({ ...card, ...p });
  return (
    <div className="flex flex-col gap-3">
      <Field label="Title" htmlFor={ids.text}>
        <input id={ids.text} value={card.text} maxLength={200} onChange={(e) => set({ text: e.target.value || " " })}
               className="h-9 rounded-[var(--radius-control)] border border-line-soft bg-bay px-3 text-sm text-chalk outline-none focus:border-tungsten/60" />
      </Field>
      <Field label="Under it (optional)" htmlFor={ids.sub}>
        <input id={ids.sub} value={card.subtext} maxLength={200} onChange={(e) => set({ subtext: e.target.value })}
               className="h-9 rounded-[var(--radius-control)] border border-line-soft bg-bay px-3 text-sm text-chalk outline-none focus:border-tungsten/60" />
      </Field>
      <div className="flex flex-wrap items-center gap-3">
        <Num label="Seconds" value={card.duration} min={0.5} max={20} step={0.5} onChange={(v) => set({ duration: v })} />
        <label className="flex items-center gap-1.5 text-2xs text-faint">Background
          <input type="color" value={card.background} onChange={(e) => set({ background: e.target.value })}
                 aria-label="Card background" className="h-7 w-9 cursor-pointer rounded border border-line-soft bg-bay" />
        </label>
        <label className="flex items-center gap-1.5 text-2xs text-faint">Text
          <input type="color" value={card.color} onChange={(e) => set({ color: e.target.value })}
                 aria-label="Card text colour" className="h-7 w-9 cursor-pointer rounded border border-line-soft bg-bay" />
        </label>
      </div>
      {!first && (
        <Segmented label="Into the card" value={card.transition} options={["cut", "fade"] as const}
                   render={(v) => (v === "cut" ? "Cut" : "Fade")} onChange={(v) => set({ transition: v })} />
      )}
    </div>
  );
}

/** A sound on a track: where it starts, which part plays, how loud, fades. */
export function SoundEditor({ clip, asset, onChange, onRemove }: {
  clip: AudioClip; asset: Asset | undefined; onChange: (p: Partial<AudioClip>) => void; onRemove: () => void;
}) {
  const full = (asset?.duration_ms ?? 0) / 1000;
  const tied = Boolean(clip.shot_id);
  return (
    <section className="flex flex-col gap-3" aria-label="Selected sound">
      <div className="flex items-center gap-2">
        <h3 className="min-w-0 flex-1 truncate text-sm text-chalk">{clip.label || asset?.title || "Sound"}</h3>
        <Button size="sm" tone="ghost" aria-label="Remove the sound" onClick={onRemove}><Trash2 className="size-4" aria-hidden /></Button>
      </div>
      {asset && <audio src={mediaUrl(asset.url)} controls className="h-8 w-full" aria-label="Listen to the sound" />}
      {tied ? (
        <p className="text-2xs text-faint">This is a shot&apos;s dialogue: it starts with the shot and follows its trims.</p>
      ) : (
        <div className="flex flex-wrap gap-3">
          <Num label="Starts at" value={clip.start} max={3600} onChange={(v) => onChange({ start: v })} />
          <Num label="From" value={clip.trim_in} max={full} onChange={(v) => onChange({ trim_in: v })} />
          <Num label="Length" value={clip.length ?? audioSeconds(clip, asset)} min={0.1} max={full - clip.trim_in}
               onChange={(v) => onChange({ length: v >= full - clip.trim_in ? null : v })} />
        </div>
      )}
      <Field label="Level">
        <Slider value={clip.volume} min={0} max={2} step={0.05} format={(v) => `${Math.round(v * 100)}%`}
                onChange={(v) => onChange({ volume: v })} />
      </Field>
      <div className="flex flex-wrap gap-3">
        <Num label="Fade in" value={clip.fade_in} max={10} onChange={(v) => onChange({ fade_in: v })} />
        <Num label="Fade out" value={clip.fade_out} max={10} onChange={(v) => onChange({ fade_out: v })} />
      </div>
    </section>
  );
}

const MAKE_OP: Record<Track, string> = { dialogue: "tts", music: "music", sfx: "sfx" };

/** Put a sound on a track at the playhead: one from the library, or a new one (narration, music, an effect). */
export function AddSound({ track, open, onClose, onAdd }: {
  track: Track; open: boolean; onClose: () => void; onAdd: (asset: Asset) => void;
}) {
  const [from, setFrom] = useState<"library" | "make">("library");
  const [genId, setGenId] = useState<string>();
  const { data: gen } = useGeneration(genId);
  const added = useRef<string | null>(null);
  const label = TRACKS.find((t) => t.id === track)?.label.toLowerCase();
  const made = gen?.assets?.find((a) => a.kind === "audio");

  useEffect(() => {
    if (!made || added.current === made.id) return;
    added.current = made.id;
    onAdd(made);
    toast(`Added to the ${label} track`);
    onClose();
  }, [made, onAdd, onClose, label]);

  return (
    <Dialog open={open} onClose={onClose} title={`Add to ${label}`} wide>
      <div className="flex flex-col gap-4">
        <Segmented label="From" value={from} options={["library", "make"] as const} onChange={setFrom}
                   render={(f) => (f === "library" ? "Your sounds" : track === "dialogue" ? "Voice a line" : `Make ${track === "music" ? "music" : "an effect"}`)} />
        {from === "library" ? <SoundLibrary onPick={(a) => { onAdd(a); onClose(); }} /> : genId ? (
          <p className="flex items-center gap-2 text-sm text-muted" aria-live="polite">
            {gen && !isActive(gen.status) && gen.status !== "succeeded"
              ? <span className="text-fault">{gen.error_message ?? "It wasn't made."}</span>
              : <><LoaderCircle className="size-4 animate-spin text-tungsten" aria-hidden /> Making it; it goes on the track when it&apos;s ready.</>}
          </p>
        ) : (
          <QuickGenerate op={MAKE_OP[track]} submitLabel="Make it" onStarted={(g) => setGenId(g.id)} onCancel={onClose}
                         promptPlaceholder={track === "music" ? "Slow strings under a grey harbour dawn, building at the end"
                           : track === "sfx" ? "A heavy wooden door slams in a stone hall" : "The lamp has burned every night for forty years."} />
        )}
      </div>
    </Dialog>
  );
}

function SoundLibrary({ onPick }: { onPick: (a: Asset) => void }) {
  const { data, isLoading } = useAssets({ kind: "audio" });
  const sounds = data?.items ?? [];
  if (isLoading) return <p className="text-sm text-muted">Loading your sounds…</p>;
  if (!sounds.length) return <p className="text-sm text-muted">No sounds yet. Make one, or upload audio in the library.</p>;
  return (
    <ul className="flex max-h-[50vh] flex-col gap-1 overflow-y-auto scrollbar-quiet">
      {sounds.map((a) => (
        <li key={a.id}>
          <button type="button" onClick={() => onPick(a)}
                  className="flex w-full items-center gap-2 rounded-[var(--radius-control)] px-2 py-1.5 text-left text-xs text-muted hover:bg-raised hover:text-chalk">
            <AudioLines className="size-3.5 shrink-0" aria-hidden />
            <span className="min-w-0 flex-1 truncate">{a.title || "Sound"}</span>
            <span className="tabular text-2xs text-faint">{((a.duration_ms ?? 0) / 1000).toFixed(1)} s</span>
          </button>
        </li>
      ))}
    </ul>
  );
}

/** "Add a title card" and "Add sound" buttons. */
export function AddButtons({ onCard, onSound, onDialogue, dialogueReady }: {
  onCard: () => void; onSound: (t: Track) => void; onDialogue: () => void; dialogueReady: number;
}) {
  return (
    <div className="flex flex-wrap gap-1.5">
      <Button size="sm" onClick={onCard}><Type className="size-4" aria-hidden /> Title card</Button>
      {TRACKS.map((t) => (
        <Button key={t.id} size="sm" onClick={() => onSound(t.id)}>+ {t.label}</Button>
      ))}
      <Button size="sm" onClick={onDialogue} disabled={!dialogueReady}
              className={cn(!dialogueReady && "opacity-60")}
              title={dialogueReady ? undefined : "No shot in the cut has a dialogue track yet"}>
        Use the shots&apos; dialogue{dialogueReady ? ` (${dialogueReady})` : ""}
      </Button>
    </div>
  );
}
