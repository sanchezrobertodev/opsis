"use client";

import type { Schemas } from "@studio/api-client";
import { useMutation, useQuery } from "@tanstack/react-query";
import { api, unwrap } from "./api";

export type Preset = Schemas["PresetOut"];
export type PresetCategory = "camera" | "effects" | "look" | "character" | "product" | "social" | "edit";

export const CATEGORY_LABEL: Record<PresetCategory, string> = {
  camera: "Camera",
  effects: "Effects",
  look: "Looks",
  character: "Character",
  product: "Product",
  social: "Social",
  edit: "Edits",
};

export function usePresets() {
  return useQuery({ queryKey: ["presets"], queryFn: () => unwrap(api.GET("/presets")), staleTime: 60_000 });
}

export function useRenderPreset() {
  return useMutation({
    mutationFn: ({ id, ...body }: { id: string } & Schemas["PresetRenderIn"]) =>
      unwrap(api.POST("/presets/{preset_id}/render", { params: { path: { preset_id: id } }, body })),
  });
}
