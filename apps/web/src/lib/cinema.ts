"use client";

import type { Schemas } from "@studio/api-client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef } from "react";
import type { Direction } from "@/components/create/DirectorPanel";
import { api, isActive, unwrap } from "./api";
import type { Cut } from "./cut";

export type ProjectSummary = Schemas["ProjectSummary"];
export type Project = Schemas["ProjectOut"];
export type Revision = Schemas["RevisionOut"];
export type Take = Schemas["TakeOut"];

/** One spoken line; voice_id overrides the speaker's own voice. */
export type Line = { who: string; text: string; voice_id?: string | null };

/** Mirrors studio/cinema/document.py. */
export type Shot = {
  id: string;
  action: string;
  duration: number | null;
  direction: Direction;
  lines: Line[];
  continuity: "cut" | "continue";
  hero_asset_id: string | null;
  /** The picked closing frame: the take animates from the opening frame to this one. */
  end_asset_id?: string | null;
  take_asset_id: string | null;
  trim_in: number;
  trim_out: number | null;
  transition: "cut" | "fade";
};
export type Scene = { id: string; heading: string; summary: string; shots: Shot[] };
export type CinemaSettings = {
  model_id: string;
  model_params: Record<string, unknown>;
  image_model_id: string;
  lipsync_model_id: string;
  aspect_ratio: string;
  resolution: string;
  direction: Direction;
  takes_per_shot: number;
  hero_frames: number;
};
export type CinemaDoc = { logline: string; script: string; cast: string[]; settings: CinemaSettings; scenes: Scene[];
                          cut?: Cut };

export function newId(prefix: "s" | "c" | "i" | "a") {
  const bytes = crypto.getRandomValues(new Uint8Array(3));
  return prefix + Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}

export function newShot(partial: Partial<Shot> = {}): Shot {
  return {
    id: newId("s"), action: "", duration: null, direction: { camera_moves: [] } as unknown as Direction, lines: [],
    continuity: "cut", hero_asset_id: null, end_asset_id: null, take_asset_id: null, trim_in: 0, trim_out: null,
    transition: "cut",
    ...partial,
  };
}

export function allShots(doc: CinemaDoc): { scene: Scene; shot: Shot; index: number }[] {
  let index = 0;
  return doc.scenes.flatMap((scene) => scene.shots.map((shot) => ({ scene, shot, index: ++index })));
}

/** The film's first shot can't continue anything; after a move it opens on a cut. */
function openOnCut(doc: CinemaDoc): CinemaDoc {
  const first = doc.scenes.find((sc) => sc.shots.length)?.shots[0];
  if (!first || first.continuity === "cut") return doc;
  return { ...doc, scenes: doc.scenes.map((sc) => ({
    ...sc, shots: sc.shots.map((s) => (s.id === first.id ? { ...s, continuity: "cut" as const } : s)) })) };
}

/** Moves a shot to `index` in a scene (the same scene or another). */
export function moveShot(doc: CinemaDoc, shotId: string, sceneId: string, index: number): CinemaDoc {
  const shot = allShots(doc).find((x) => x.shot.id === shotId)?.shot;
  if (!shot || !doc.scenes.some((sc) => sc.id === sceneId)) return doc;
  const scenes = doc.scenes.map((sc) => ({ ...sc, shots: sc.shots.filter((s) => s.id !== shotId) }));
  return openOnCut({ ...doc, scenes: scenes.map((sc) => {
    if (sc.id !== sceneId) return sc;
    const shots = [...sc.shots];
    shots.splice(Math.max(0, Math.min(index, shots.length)), 0, shot);
    return { ...sc, shots };
  }) });
}

/** Puts `added` right after the shot `afterId`, in its scene and, if the cut lists that shot, in the cut too. */
function insertAfter(doc: CinemaDoc, afterId: string, added: Shot, replaced?: Shot): CinemaDoc {
  const scenes = doc.scenes.map((sc) => {
    const i = sc.shots.findIndex((s) => s.id === afterId);
    if (i < 0) return sc;
    const shots = [...sc.shots];
    shots.splice(i, 1, replaced ?? shots[i], added);
    return { ...sc, shots };
  });
  if (!doc.cut) return { ...doc, scenes };
  const at = doc.cut.items.findIndex((it) => it.shot_id === afterId);
  const items = [...doc.cut.items];
  if (at >= 0) items.splice(at + 1, 0, { id: newId("i"), shot_id: added.id, volume: doc.cut.items[at].volume });
  return { ...doc, scenes, cut: { ...doc.cut, items } };
}

/** A copy of the shot right after it: same description, direction, lines and opening frame, no take yet. */
export function duplicateShot(doc: CinemaDoc, shotId: string, id = newId("s")): { doc: CinemaDoc; id: string } {
  const shot = allShots(doc).find((x) => x.shot.id === shotId)?.shot;
  if (!shot) return { doc, id: shotId };
  const copy: Shot = { ...structuredClone(shot), id, take_asset_id: null, trim_in: 0, trim_out: null };
  return { doc: insertAfter(doc, shotId, copy), id: copy.id };
}

/**
 * Splits a shot in two at `at` seconds, the second part continuing the first.
 * A filmed shot keeps its take: the first part plays it up to the split, the second from there (seconds into the
 * take, so `take` is the take's [trimmed start, used length]), and dialogue in the cut tied to the shot is given to
 * both parts, each playing its own stretch. An unfilmed shot's length and lines are shared between the parts (`at`
 * equal to its length gives both parts that length). Either way a picked closing frame goes to the second part, which
 * now ends the shot.
 */
export function splitShot(doc: CinemaDoc, shotId: string, at: number, take?: [number, number],
                          id = newId("s")): { doc: CinemaDoc; id: string } {
  const shot = allShots(doc).find((x) => x.shot.id === shotId)?.shot;
  if (!shot) return { doc, id: shotId };
  const base = { id, action: shot.action, direction: structuredClone(shot.direction), continuity: "continue" as const,
                 transition: "cut" as const };
  let first: Shot, second: Shot;
  if (shot.take_asset_id && take) {
    const point = Math.round((take[0] + at) * 100) / 100;
    first = { ...shot, trim_out: point, end_asset_id: null };
    second = newShot({ ...base, duration: shot.duration, take_asset_id: shot.take_asset_id, trim_in: point,
                       trim_out: shot.trim_out, end_asset_id: shot.end_asset_id ?? null });
  } else {
    const lines = shot.lines ?? [];
    const half = Math.ceil(lines.length / 2);
    const length = shot.duration;
    // `at` as long as the shot keeps the length for both parts (the model makes nothing shorter)
    first = { ...shot, lines: lines.slice(0, half), duration: length ? at : null, end_asset_id: null };
    second = newShot({ ...base, lines: lines.slice(half), duration: length ? (at < length ? length - at : length) : null,
                       end_asset_id: shot.end_asset_id ?? null });
  }
  let out = insertAfter(doc, shotId, second, first);
  if (out.cut && shot.take_asset_id) {
    const tied = out.cut.audio.filter((c) => c.shot_id === shotId);
    out = { ...out, cut: { ...out.cut, audio: [...out.cut.audio, ...tied.map((c) => ({
      ...c, id: newId("a"), shot_id: second.id }))] } };
  }
  return { doc: out, id: second.id };
}

const projectKey = (id: string) => ["project", id] as const;

export function useProjects() {
  return useQuery({ queryKey: ["projects"], queryFn: () => unwrap(api.GET("/projects", { params: { query: { kind: "cinema" } } })) });
}

export function useProject(id: string) {
  return useQuery({
    queryKey: projectKey(id),
    queryFn: () => unwrap(api.GET("/projects/{project_id}", { params: { path: { project_id: id } } })),
    staleTime: Infinity, // the editor owns the document once loaded
  });
}

export function useCreateProject() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (title: string) => unwrap(api.POST("/projects", { body: { kind: "cinema", title, document: {} } })),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["projects"] }),
  });
}

export function useSaveProject(id: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { version: number; title?: string; document: CinemaDoc; note?: string }) =>
      unwrap(api.PUT("/projects/{project_id}", { params: { path: { project_id: id } },
                                                 body: { note: "", ...body, document: body.document as never } })),
    onSuccess: (p) => {
      qc.setQueryData(projectKey(id), p);
      qc.invalidateQueries({ queryKey: ["projects"] });
      qc.invalidateQueries({ queryKey: ["revisions", id] });
    },
  });
}

export function useRevisions(id: string, enabled: boolean) {
  return useQuery({
    queryKey: ["revisions", id],
    queryFn: () => unwrap(api.GET("/projects/{project_id}/revisions", { params: { path: { project_id: id } } })),
    enabled,
  });
}

export function useRestoreRevision(id: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (version: number) => unwrap(api.POST("/projects/{project_id}/revisions/{version}/restore", {
      params: { path: { project_id: id, version } } })),
    onSuccess: (p) => {
      qc.setQueryData(projectKey(id), p);
      qc.invalidateQueries({ queryKey: ["revisions", id] });
    },
  });
}

export function usePlanShots(id: string) {
  return useMutation({
    mutationFn: (body: { max_shots: number; notes: string; script: string; logline: string; cast: string[] }) =>
      unwrap(api.POST("/projects/{project_id}/plan", { params: { path: { project_id: id } }, body })),
  });
}

/** Hero frames and takes for the project, refreshed while any are being made or a run is still planning them. */
export function useTakes(id: string, runsActive: boolean) {
  const qc = useQueryClient();
  const takes = useQuery({
    queryKey: ["takes", id],
    queryFn: () => unwrap(api.GET("/projects/{project_id}/takes", { params: { path: { project_id: id } } })),
    refetchInterval: (q) => (runsActive || (q.state.data ?? []).some((t) => isActive(t.status)) ? 1500 : false),
  });
  const was = useRef(runsActive);
  useEffect(() => {
    // a take cut or joined by a run's last step has nothing pending to poll for, so look again once runs settle
    if (was.current && !runsActive) void qc.invalidateQueries({ queryKey: ["takes", id] });
    was.current = runsActive;
  }, [runsActive, id, qc]);
  return takes;
}

/** Each shot's speech as its own track, for the cut's dialogue track. */
export function useDialogue(id: string) {
  return useQuery({
    queryKey: ["dialogue", id],
    queryFn: () => unwrap(api.GET("/projects/{project_id}/dialogue", { params: { path: { project_id: id } } })),
  });
}

/** What the film's generations have cost so far, per shot (refreshed while runs are going). */
export function useProjectSpend(id: string, active: boolean) {
  const qc = useQueryClient();
  const spend = useQuery({
    queryKey: ["project-spend", id],
    queryFn: () => unwrap(api.GET("/projects/{project_id}/spend", { params: { path: { project_id: id } } })),
    refetchInterval: active ? 3000 : false,
  });
  const was = useRef(active);
  useEffect(() => {
    // the last charges land as the runs settle, possibly between polls: look once more
    if (was.current && !active) void qc.invalidateQueries({ queryKey: ["project-spend", id] });
    was.current = active;
  }, [active, id, qc]);
  return spend;
}

/** Whether any of these runs is still going (polled until they all settle). */
export function useRunsActive(ids: string[]) {
  const { data } = useQuery({
    queryKey: ["runs-active", ids],
    queryFn: async () => Promise.all(ids.map((id) => unwrap(api.GET("/pipelines/{run_id}", { params: { path: { run_id: id } } })))),
    enabled: ids.length > 0,
    refetchInterval: (q) => ((q.state.data ?? []).some((r) => r.status === "running") ? 2000 : false),
  });
  return { active: ids.length > 0 && (!data || data.some((r) => r.status === "running")), runs: data ?? [] };
}

/** Whether any run for this film is still going, wherever it started (a region edit in Object swap, too). */
export function useProjectRunsActive(id: string) {
  const { data } = useQuery({
    queryKey: ["project-runs", id],
    queryFn: () => unwrap(api.GET("/pipelines", { params: { query: { project_id: id, status: "running", limit: 20 } } })),
    refetchInterval: (q) => ((q.state.data ?? []).length ? 2000 : 8000),
  });
  return (data ?? []).length > 0;
}
