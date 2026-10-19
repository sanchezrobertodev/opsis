"use client";

import type { Schemas } from "@studio/api-client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, unwrap } from "./api";

export type Element = Schemas["ElementOut"];
export type ElementKind = Element["kind"];
export type ElementView = Schemas["ElementImageIn"]["view"];

export const KIND_LABEL: Record<ElementKind, string> = {
  character: "Character",
  location: "Location",
  prop: "Prop",
  style: "Style",
  voice: "Voice",
};

export function useElements(kind?: ElementKind, q?: string) {
  return useQuery({
    queryKey: ["elements", kind ?? "all", q ?? ""],
    queryFn: () => unwrap(api.GET("/elements", { params: { query: { kind, q: q || undefined } } })),
  });
}

function useInvalidate() {
  const qc = useQueryClient();
  return () => qc.invalidateQueries({ queryKey: ["elements"] });
}

export function useCreateElement() {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: (body: Schemas["ElementCreate"]) => unwrap(api.POST("/elements", { body })),
    onSuccess: invalidate,
  });
}

export function usePatchElement() {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: ({ id, ...body }: { id: string } & Schemas["ElementPatch"]) =>
      unwrap(api.PATCH("/elements/{element_id}", { params: { path: { element_id: id } }, body })),
    onSuccess: invalidate,
  });
}

/** Clone a voice from recordings (an ElevenLabs instant clone; a stand-in in mock mode). */
export function useCloneVoice() {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: (body: Schemas["CloneVoiceIn"]) => unwrap(api.POST("/elements/clone-voice", { body })),
    onSuccess: invalidate,
  });
}

/** Your cloned voices as [voice id, label] pairs, for any voice list. */
export function useClonedVoices(): [string, string][] {
  const { data } = useElements("voice");
  return (data ?? []).flatMap((e) => {
    const id = (e.voice as { params?: { voice_id?: string } } | null)?.params?.voice_id;
    return id ? [[id, `${e.name} (your voice)`] as [string, string]] : [];
  });
}

export function useDeleteElement() {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: (id: string) => unwrap(api.DELETE("/elements/{element_id}", { params: { path: { element_id: id } } })),
    onSuccess: invalidate,
  });
}

export function useAddElementImages() {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: ({ id, images }: { id: string; images: Schemas["ElementImageIn"][] }) =>
      unwrap(api.POST("/elements/{element_id}/images", { params: { path: { element_id: id } }, body: images })),
    onSuccess: invalidate,
  });
}

export function useRemoveElementImage() {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: ({ id, assetId }: { id: string; assetId: string }) =>
      unwrap(api.DELETE("/elements/{element_id}/images/{asset_id}", {
        params: { path: { element_id: id, asset_id: assetId } },
      })),
    onSuccess: invalidate,
  });
}
