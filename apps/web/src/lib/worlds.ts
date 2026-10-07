"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type Asset, unwrap } from "./api";
import { newObject, type SceneDoc } from "./jutsu3d";

/** What a Marble world came with: its 360 panorama and its collision mesh (same generation, other outputs). */
export type WorldParts = { pano: Asset | null; collider: Asset | null };

export function useWorldParts(splat: Asset | null | undefined) {
  const genId = splat?.generation_id;
  return useQuery({
    queryKey: ["world-parts", genId ?? splat?.id ?? ""],
    enabled: Boolean(splat),
    queryFn: async (): Promise<WorldParts> => {
      if (!genId) return { pano: null, collider: null };
      const gen = await unwrap(api.GET("/generations/{generation_id}", { params: { path: { generation_id: genId } } }));
      return {
        pano: gen.assets.find((a) => a.kind === "image" && a.metadata?.projection === "equirectangular") ?? null,
        collider: gen.assets.find((a) => a.kind === "model3d" && a.metadata?.part === "collider") ?? null,
      };
    },
    staleTime: Infinity,
  });
}

/** A world object for a 3D scene, standing on the floor at the origin. */
export function worldObject(splat: Asset, collider: Asset | null) {
  return newObject("world", { asset_id: splat.id, collider_asset_id: collider?.id ?? null,
                              name: splat.title || "World", color: "#8f9196" });
}

/** A new 3D Jutsu scene set in a world: the world, a figure to block with, and a camera. */
export function useFilmWorld() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ splat, collider, title }: { splat: Asset; collider: Asset | null; title: string }) => {
      const p = await unwrap(api.POST("/projects", { body: { kind: "jutsu3d", title, document: {} } }));
      const doc = p.document as unknown as SceneDoc;
      const withWorld: SceneDoc = { ...doc, objects: [worldObject(splat, collider), ...doc.objects] };
      return unwrap(api.PUT("/projects/{project_id}", { params: { path: { project_id: p.id } },
                                                        body: { version: p.version, note: "Set in a world", document: withWorld as never } }));
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["projects"] }),
  });
}
