"use client";

import type { Schemas } from "@studio/api-client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, unwrap } from "./api";

export type Run = Schemas["RunOut"];
export type RunSummary = Schemas["RunSummary"];
export type Step = Schemas["StepOut"];
export type RunCreate = Schemas["RunCreate"];

export const RUN_STATUS_TEXT: Record<string, string> = {
  running: "Running",
  waiting: "Waiting for you",
  succeeded: "Done",
  failed: "Failed",
  canceled: "Canceled",
};

export function useRuns() {
  return useQuery({ queryKey: ["runs"], queryFn: () => unwrap(api.GET("/pipelines")) });
}

export function useRun(id: string) {
  return useQuery({
    queryKey: ["run", id],
    queryFn: () => unwrap(api.GET("/pipelines/{run_id}", { params: { path: { run_id: id } } })),
  });
}

export function useEstimateRun(body: RunCreate | null) {
  return useQuery({
    queryKey: ["run-estimate", body],
    queryFn: () => unwrap(api.POST("/pipelines/estimate", { body: body! })),
    enabled: Boolean(body),
    retry: false,
    placeholderData: (prev) => prev,
  });
}

function useRunMutation<V>(fn: (v: V) => Promise<Run>) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: (run) => {
      qc.setQueryData(["run", run.id], run);
      qc.invalidateQueries({ queryKey: ["runs"] });
    },
  });
}

export function useStartRun() {
  return useRunMutation((body: RunCreate) => unwrap(api.POST("/pipelines", { body })));
}

export function useCancelRun() {
  return useRunMutation((id: string) =>
    unwrap(api.POST("/pipelines/{run_id}/cancel", { params: { path: { run_id: id } } })));
}

export function useRetryRun() {
  return useRunMutation((id: string) =>
    unwrap(api.POST("/pipelines/{run_id}/retry", { params: { path: { run_id: id } } })));
}

export function useReviseRun() {
  return useRunMutation(({ id, input }: { id: string; input: Record<string, unknown> }) =>
    unwrap(api.POST("/pipelines/{run_id}/revise", { params: { path: { run_id: id } }, body: { input } })));
}

export function useAnswerCheckpoint() {
  return useRunMutation(({ id, key, choice }: { id: string; key: string; choice: string }) =>
    unwrap(api.POST("/pipelines/{run_id}/checkpoints/{key}", {
      params: { path: { run_id: id, key } }, body: { choice } })));
}
