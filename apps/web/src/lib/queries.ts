"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useDraft } from "./draft";
import { api, API_URL, ApiError, isActive, unwrap, type Asset, type Generation } from "./api";

export const keys = {
  registry: ["registry"] as const,
  model: (id: string) => ["model", id] as const,
  generations: (outputType?: string) => ["generations", outputType ?? "all"] as const,
  assets: (filter: AssetFilter) => ["assets", filter] as const,
  asset: (id: string) => ["asset", id] as const,
  lineage: (id: string) => ["lineage", id] as const,
  budget: ["budget"] as const,
  health: ["health"] as const,
};

export type AssetFilter = {
  kind?: "video" | "image" | "audio" | "model3d" | "splat";
  source?: "upload" | "generation";
  favorite?: boolean;
  tag?: string;
  projection?: "equirectangular";
};

export function useRegistry() {
  return useQuery({
    queryKey: keys.registry,
    queryFn: () => unwrap(api.GET("/registry")),
    staleTime: 60_000,
  });
}

export function useModel(id: string | undefined) {
  return useQuery({
    queryKey: keys.model(id ?? ""),
    queryFn: () => unwrap(api.GET("/registry/models/{model_id}", { params: { path: { model_id: id! } } })),
    enabled: Boolean(id),
    staleTime: 60_000,
  });
}

export function useGenerations(outputType?: "video" | "image", ops?: string[]) {
  const opsParam = ops?.length ? ops.join(",") : undefined;
  return useQuery({
    queryKey: [...keys.generations(outputType), opsParam ?? ""],
    queryFn: () =>
      unwrap(api.GET("/generations", { params: { query: { output_type: outputType, ops: opsParam, limit: 60 } } })),
  });
}

export function useAssets(filter: AssetFilter) {
  return useQuery({
    queryKey: keys.assets(filter),
    queryFn: () => unwrap(api.GET("/assets", { params: { query: { ...filter, limit: 120 } } })),
  });
}

export function useAsset(id: string | undefined) {
  return useQuery({
    queryKey: keys.asset(id ?? ""),
    queryFn: () => unwrap(api.GET("/assets/{asset_id}", { params: { path: { asset_id: id! } } })),
    enabled: Boolean(id),
  });
}

export function useLineage(id: string | undefined) {
  return useQuery({
    queryKey: keys.lineage(id ?? ""),
    queryFn: () => unwrap(api.GET("/assets/{asset_id}/lineage", { params: { path: { asset_id: id! } } })),
    enabled: Boolean(id),
  });
}

export function useBudget() {
  return useQuery({ queryKey: keys.budget, queryFn: () => unwrap(api.GET("/budget")) });
}

/** What's left on each provider account (the server keeps free reads for a minute). */
export function useBalances() {
  return useQuery({ queryKey: ["balances"], queryFn: () => unwrap(api.GET("/budget/balances")), staleTime: 60_000 });
}

export function useHealth() {
  return useQuery({ queryKey: keys.health, queryFn: () => unwrap(api.GET("/health")), refetchInterval: 30_000 });
}

export type CreateBody = {
  model_id: string;
  mode: string;
  prompt: string;
  negative_prompt?: string | null;
  params: Record<string, unknown>;
  media: { role: string; asset_id: string }[];
  route_policy?: string;
  direction?: Record<string, unknown> | null;
  confirm_cost_usd?: number | null;
  group_id?: string | null;
};

/** One generation, polled until it settles (for tools that wait on a single job, like a selection). */
export function useGeneration(id: string | undefined) {
  return useQuery({
    queryKey: ["generation", id],
    queryFn: () => unwrap(api.GET("/generations/{generation_id}", { params: { path: { generation_id: id! } } })),
    enabled: Boolean(id),
    refetchInterval: (q) => (q.state.data && !isActive(q.state.data.status) ? false : 1000),
  });
}

export function useCreateGeneration() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: CreateBody) =>
      unwrap(
        api.POST("/generations", {
          body: { route_policy: "cheapest_healthy", ...body },
          headers: { "Idempotency-Key": crypto.randomUUID() },
        }),
      ),
    onSuccess: (gen) => {
      prependGeneration(qc, gen);
    },
  });
}

export function useCancelGeneration() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      unwrap(api.POST("/generations/{generation_id}/cancel", { params: { path: { generation_id: id } } })),
    onSuccess: (gen) => patchGeneration(qc, gen.id, gen),
  });
}

/** Shows the file in this computer's file manager (only when `/health` says `reveal_files`). */
export function useRevealAsset() {
  return useMutation({
    mutationFn: (id: string) => unwrap(api.POST("/assets/{asset_id}/reveal", { params: { path: { asset_id: id } } })),
  });
}

/** Removes a take from history and its files from disk; the answer lists outputs kept because something uses them. */
export function useDeleteGeneration() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      unwrap(api.DELETE("/generations/{generation_id}", { params: { path: { generation_id: id } } })),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["generations"] });
      qc.invalidateQueries({ queryKey: ["assets"] });
    },
  });
}

/** What deleting several at once did: how many went, what was kept and why, and what failed. */
export type Removal = { deleted: number; kept: string[]; failed: string[] };

function settle(results: PromiseSettledResult<string[]>[], keptOn409: boolean): Removal {
  const out: Removal = { deleted: 0, kept: [], failed: [] };
  for (const r of results) {
    if (r.status === "fulfilled") {
      out.deleted += 1;
      out.kept.push(...r.value);
    } else if (keptOn409 && r.reason instanceof ApiError && r.reason.status === 409) {
      out.kept.push(r.reason.message);
    } else {
      out.failed.push(r.reason instanceof Error ? r.reason.message : String(r.reason));
    }
  }
  return { ...out, kept: [...new Set(out.kept)] };
}

/** Deletes several takes; `kept` names what still uses outputs that stayed. */
export function useDeleteGenerations() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (ids: string[]) =>
      settle(await Promise.allSettled(ids.map((id) =>
        unwrap(api.DELETE("/generations/{generation_id}", { params: { path: { generation_id: id } } })).then((r) => r.kept),
      )), false),
    onSettled: () => {
      for (const key of ["generations", "assets", "takes"]) void qc.invalidateQueries({ queryKey: [key] });
    },
  });
}

/** Deletes several library files; `kept` has the reason for each one something still uses. */
export function useDeleteAssets() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (ids: string[]) =>
      settle(await Promise.allSettled(ids.map((id) =>
        unwrap(api.DELETE("/assets/{asset_id}", { params: { path: { asset_id: id } } })).then(() => []),
      )), true),
    onSettled: () => {
      for (const key of ["generations", "assets", "takes"]) void qc.invalidateQueries({ queryKey: [key] });
    },
  });
}

/** Deletes takes with their files, then single files, as one answer (leave out files of the takes given). */
export function useRemove() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ generations = [], assets = [] }: { generations?: string[]; assets?: string[] }) => {
      const takes = settle(await Promise.allSettled(generations.map((id) =>
        unwrap(api.DELETE("/generations/{generation_id}", { params: { path: { generation_id: id } } }))
          .then((r) => r.kept.map((what) => `Kept a file ${what} uses.`)),
      )), false);
      const files = settle(await Promise.allSettled(assets.map((id) =>
        unwrap(api.DELETE("/assets/{asset_id}", { params: { path: { asset_id: id } } })).then(() => []),
      )), true);
      return { deleted: takes.deleted + files.deleted, kept: [...takes.kept, ...files.kept],
               failed: [...takes.failed, ...files.failed] } satisfies Removal;
    },
    onSettled: () => {
      for (const key of ["generations", "assets", "takes"]) void qc.invalidateQueries({ queryKey: [key] });
    },
  });
}

/** Deletes films or 3D scenes. Their takes and files stay in history and the library. */
export function useDeleteProjects() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (ids: string[]) =>
      settle(await Promise.allSettled(ids.map((id) =>
        unwrap(api.DELETE("/projects/{project_id}", { params: { path: { project_id: id } } })).then(() => []),
      )), false),
    onSettled: () => void qc.invalidateQueries({ queryKey: ["projects"] }),
  });
}

export function useResolveUnknown() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      unwrap(
        api.POST("/generations/{generation_id}/resolve-unknown", { params: { path: { generation_id: id } } }),
      ),
    onSuccess: (gen) => patchGeneration(qc, gen.id, gen),
  });
}

export function usePatchAsset() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, ...body }: { id: string; favorite?: boolean; title?: string; add_tags?: string[] }) =>
      unwrap(api.PATCH("/assets/{asset_id}", { params: { path: { asset_id: id } }, body })),
    onSuccess: (asset) => {
      qc.setQueryData(keys.asset(asset.id), asset);
      qc.invalidateQueries({ queryKey: ["assets"] });
    },
  });
}

/** Save a frame of a video (default: the last one) as an image asset. Idempotent per clip and position. */
export function useGrabFrame() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, at = "last" }: { id: string; at?: "first" | "last" | number }) =>
      unwrap(api.POST("/assets/{asset_id}/frames", { params: { path: { asset_id: id } }, body: { at } })),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["assets"] }),
  });
}

/** Grab a clip's last frame and make it the start frame in Create video. */
export function useContinueFromLastFrame() {
  const grab = useGrabFrame();
  const addMedia = useDraft((s) => s.addMedia);
  const setDraft = useDraft((s) => s.set);
  return {
    isPending: grab.isPending,
    run: async (videoId: string) => {
      const f = await grab.mutateAsync({ id: videoId });
      addMedia("video", "start_image", { id: f.id, kind: "image", thumb: f.thumb_url, url: f.url, title: f.title }, 1);
      setDraft("video", { mode: "i2v" });
      return f;
    },
  };
}

export function useDeleteAsset() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      unwrap(api.DELETE("/assets/{asset_id}", { params: { path: { asset_id: id } } })),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["assets"] });
      qc.invalidateQueries({ queryKey: ["generations"] });
    },
  });
}

export function useUpdateBudget() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: {
      daily_cap_usd?: number;
      monthly_cap_usd?: number;
      confirm_over_usd?: number;
      live_enabled?: boolean;
      provider_mode?: "mock" | "mixed" | "live";
      llm_mode?: "mock" | "live";
    }) => unwrap(api.PATCH("/budget", { body })),
    onSuccess: (b, body) => {
      qc.setQueryData(keys.budget, b);
      // the mode switch changes which models can run and what the top bar says
      if (body.provider_mode || body.llm_mode) {
        void qc.invalidateQueries({ queryKey: keys.registry });
        void qc.invalidateQueries({ queryKey: keys.health });
      }
    },
  });
}

/** Multipart upload with progress (fetch has no upload progress, so this uses XHR). */
export function uploadFile(file: File, onProgress?: (fraction: number) => void): Promise<Asset> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `${API_URL}/uploads`);
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) onProgress?.(e.loaded / e.total);
    };
    xhr.onload = () => {
      let body: unknown = null;
      try {
        body = JSON.parse(xhr.responseText);
      } catch {
        /* non-JSON error page */
      }
      if (xhr.status >= 200 && xhr.status < 300) resolve(body as Asset);
      else reject(new ApiError(xhr.status, body));
    };
    xhr.onerror = () => reject(new ApiError(0, { message: "Upload failed: the API is unreachable." }));
    const form = new FormData();
    form.append("file", file);
    xhr.send(form);
  });
}

// ---------------------------------------------------------------- cache helpers

type Page = { items: Generation[]; next_cursor?: string | null };

/** Show a new take at once in every list it belongs to. Op-filtered lists (Edit, Motion) refetch instead,
 * since only the server knows which modes match their ops. */
export function prependGeneration(qc: ReturnType<typeof useQueryClient>, gen: Generation) {
  for (const [key, data] of qc.getQueriesData<Page>({ queryKey: ["generations"] })) {
    const [, type, ops] = key as [string, string, string?];
    if (type !== "all" && type !== gen.output_type) continue;
    if (ops) {
      void qc.invalidateQueries({ queryKey: key, exact: true });
      continue;
    }
    if (data && !data.items.some((g) => g.id === gen.id)) qc.setQueryData<Page>(key, { ...data, items: [gen, ...data.items] });
  }
}

export function patchGeneration(
  qc: ReturnType<typeof useQueryClient>,
  id: string,
  patch: Partial<Generation>,
): boolean {
  let found = false;
  for (const [key, data] of qc.getQueriesData<Page>({ queryKey: ["generations"] })) {
    if (!data) continue;
    if (!data.items.some((g) => g.id === id)) continue;
    found = true;
    qc.setQueryData<Page>(key, {
      ...data,
      items: data.items.map((g) => (g.id === id ? { ...g, ...patch } : g)),
    });
  }
  return found;
}
