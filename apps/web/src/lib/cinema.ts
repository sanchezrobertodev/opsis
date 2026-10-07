"use client";

import type { Schemas } from "@studio/api-client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef } from "react";
import type { Direction } from "@/components/create/DirectorPanel";
import { api, isActive, unwrap } from "./api";

export type ProjectSummary = Schemas["ProjectSummary"];
export type Project = Schemas["ProjectOut"];
export type Revision = Schemas["RevisionOut"];
export type Take = Schemas["TakeOut"];

/** Mirrors studio/cinema/document.py. */
export type Shot = {
  id: string;
  action: string;
  duration: number | null;
  direction: Direction;
  dialogue: string;
  continuity: "cut" | "continue";
  hero_asset_id: string | null;
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
  aspect_ratio: string;
  resolution: string;
  direction: Direction;
  takes_per_shot: number;
  hero_frames: number;
};
export type CinemaDoc = { logline: string; script: string; cast: string[]; settings: CinemaSettings; scenes: Scene[] };

export function newId(prefix: "s" | "c") {
  const bytes = crypto.getRandomValues(new Uint8Array(3));
  return prefix + Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}

export function newShot(partial: Partial<Shot> = {}): Shot {
  return {
    id: newId("s"), action: "", duration: null, direction: { camera_moves: [] } as unknown as Direction, dialogue: "",
    continuity: "cut", hero_asset_id: null, take_asset_id: null, trim_in: 0, trim_out: null, transition: "cut",
    ...partial,
  };
}

export function allShots(doc: CinemaDoc): { scene: Scene; shot: Shot; index: number }[] {
  let index = 0;
  return doc.scenes.flatMap((scene) => scene.shots.map((shot) => ({ scene, shot, index: ++index })));
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
