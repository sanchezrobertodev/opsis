"use client";

import type { Asset } from "./api";
import { allShots, type CinemaDoc, type Shot } from "./cinema";

/** Mirrors studio/cinema/document.py (Cut) and the frame plan in studio/media/timeline.py. */
export const FPS = 24;
const MIN_FRAMES = 5;
const MIN_SECONDS = 0.2;

export type TitleCard = { text: string; subtext: string; duration: number; background: string; color: string;
                          transition: "cut" | "fade" };
export type CutItem = { id: string; shot_id?: string | null; card?: TitleCard | null; volume: number };
export type Track = "dialogue" | "music" | "sfx";
export type AudioClip = { id: string; track: Track; asset_id: string; label: string; start: number; trim_in: number;
                          length: number | null; volume: number; fade_in: number; fade_out: number;
                          shot_id?: string | null };
export type Cut = { items: CutItem[]; audio: AudioClip[]; duck_music: boolean; fade_seconds: number };

export const EMPTY_CUT: Cut = { items: [], audio: [], duck_music: true, fade_seconds: 0.5 };
export const TRACKS: { id: Track; label: string }[] = [
  { id: "dialogue", label: "Dialogue" }, { id: "music", label: "Music" }, { id: "sfx", label: "Sound effects" },
];
export const DEFAULT_CARD: TitleCard = { text: "Title", subtext: "", duration: 3, background: "#0d0e10",
                                         color: "#f2efe8", transition: "fade" };

export function cutOf(doc: CinemaDoc): Cut {
  return { ...EMPTY_CUT, ...(doc.cut ?? {}) };
}

export function newCutId(prefix: string) {
  const bytes = crypto.getRandomValues(new Uint8Array(3));
  return prefix + Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}

/** Seconds to whole frames; halves round up (Python's side does the same). */
export function toFrames(seconds: number) {
  return Math.floor(seconds * FPS + 0.5);
}

/** [start, length] of a take's used part, given its full length in seconds. */
export function clipSeconds(full: number, trimIn: number, trimOut: number | null): [number, number] {
  const start = Math.min(trimIn, Math.max(0, full - MIN_SECONDS));
  const end = full ? Math.min(trimOut || full, full) : trimOut || 0;
  return [start, Math.max(MIN_SECONDS, end - start)];
}

/** One place on the picture track, resolved: a picked take or a title card, with its frames on the film. */
export type Entry = {
  item: CutItem;
  shot?: Shot; n?: number; asset?: Asset; card?: TitleCard;
  transition: "cut" | "fade";
  trimFrom: number; // seconds into the take where it starts (0 for a card)
  frames: number; overlap: number; start: number; // where it sits, in frames
};

/**
 * The picture track: the cut's items whose shots have a picked take (cards always), then picked shots the cut
 * doesn't list yet, in story order. Timed in frames exactly as the export times them.
 */
export function sequence(doc: CinemaDoc, assets: Map<string, Asset>): { entries: Entry[]; frames: number } {
  const cut = cutOf(doc);
  const shots = allShots(doc);
  const byId = new Map(shots.map((s) => [s.shot.id, s]));
  const listed = new Set(cut.items.map((i) => i.shot_id).filter(Boolean));
  const items = [...cut.items, ...shots.filter((s) => !listed.has(s.shot.id))
    .map((s): CutItem => ({ id: autoId(s.shot.id), shot_id: s.shot.id, volume: 1 }))];
  const entries: Entry[] = [];
  let total = 0;
  for (const item of items) {
    let seconds: number, transition: "cut" | "fade", trimFrom = 0, rest: Partial<Entry>;
    if (item.card) {
      seconds = item.card.duration;
      transition = item.card.transition;
      rest = { card: item.card };
    } else {
      const s = item.shot_id ? byId.get(item.shot_id) : undefined;
      const asset = s?.shot.take_asset_id ? assets.get(s.shot.take_asset_id) : undefined;
      if (!s || !asset) continue;
      const [from, length] = clipSeconds((asset.duration_ms ?? 0) / 1000, s.shot.trim_in, s.shot.trim_out);
      seconds = length;
      trimFrom = from;
      transition = s.shot.transition;
      rest = { shot: s.shot, n: s.index, asset };
    }
    const frames = Math.max(MIN_FRAMES, toFrames(seconds));
    const prev = entries[entries.length - 1];
    const overlap = prev && transition === "fade"
      ? Math.min(toFrames(cut.fade_seconds), Math.floor(prev.frames / 2), Math.floor(frames / 2)) : 0;
    const start = total - overlap;
    entries.push({ item, transition: entries.length ? transition : "cut", trimFrom, frames, overlap, start, ...rest });
    total = start + frames;
  }
  return { entries, frames: total };
}

/** A shot's place in the cut before the cut lists it: a stable id, so it stays selected once the cut is saved. */
function autoId(shotId: string) {
  return `i${shotId}`.slice(0, 16);
}

/** The items as the cut stores them (the order the editor shows, including shots it added on its own). */
export function itemsOf(entries: Entry[]): CutItem[] {
  return entries.map((e) => e.item);
}

/** Seconds of an audio clip the film uses (to its end when no length is set). */
export function audioSeconds(clip: AudioClip, asset: Asset | undefined) {
  const available = Math.max(0, (asset?.duration_ms ?? 0) / 1000 - clip.trim_in);
  return clip.length ? Math.min(clip.length, available || clip.length) : available;
}

/**
 * The audio clips as they play: dialogue that belongs to a shot follows it (it starts where the shot starts, from
 * where the shot's take is trimmed, and lasts as long as the shot). Clips of shots that aren't in the cut drop out.
 */
export function placeSounds(cut: Cut, entries: Entry[]): AudioClip[] {
  const byShot = new Map(entries.filter((e) => e.shot).map((e) => [e.shot!.id, e]));
  return cut.audio.flatMap((clip) => {
    if (!clip.shot_id) return [clip];
    const e = byShot.get(clip.shot_id);
    return e ? [{ ...clip, start: e.start / FPS, trim_in: e.trimFrom, length: e.frames / FPS }] : [];
  });
}

/** What the export recipe takes. */
export function exportInput(projectId: string, title: string, doc: CinemaDoc, entries: Entry[]) {
  const cut = cutOf(doc);
  return {
    project_id: projectId, title, fade_seconds: cut.fade_seconds, duck_music: cut.duck_music,
    items: entries.map((e, i) => (e.card
      ? { card: { ...e.card, transition: i === 0 ? "cut" : e.card.transition } }
      : { take: { asset_id: e.asset!.id, trim_in: e.shot!.trim_in, trim_out: e.shot!.trim_out,
                  transition: i === 0 ? "cut" : e.transition, volume: e.item.volume } })),
    audio: placeSounds(cut, entries).map(({ asset_id, track, start, trim_in, length, volume, fade_in, fade_out }) =>
      ({ asset_id, track, start, trim_in, length, volume, fade_in, fade_out })),
  };
}

export function timecode(seconds: number) {
  const m = Math.floor(seconds / 60);
  const s = seconds - m * 60;
  return `${m}:${s.toFixed(1).padStart(4, "0")}`;
}
