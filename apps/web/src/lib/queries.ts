"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useDraft } from "./draft";
import { api, API_URL, ApiError, unwrap, type Asset, type Generation } from "./api";

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
  kind?: "video" | "image" | "audio";
  source?: "upload" | "generation";
  favorite?: boolean;
  tag?: string;
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
    }) => unwrap(api.PATCH("/budget", { body })),
    onSuccess: (b) => qc.setQueryData(keys.budget, b),
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
