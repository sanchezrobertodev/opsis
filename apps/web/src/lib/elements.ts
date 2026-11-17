"use client";

import type { Schemas } from "@opsis/api-client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, unwrap } from "./api";

export type Element = Schemas["ElementOut"];
export type ElementKind = Element["kind"];
export type ElementView = Schemas["ElementImageIn"]["view"];
export type Likeness = Schemas["LikenessOut"];

export const KIND_LABEL: Record<ElementKind, string> = {
  character: "Character",
  cast: "Cast", // a saved group of characters, not a film's cast in Cinema Studio
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

/** Claude names and describes the look one to four pictures share, for a saved style (a canned answer in mock mode).
 * Nothing is saved. */
export function useDescribeStyle() {
  return useMutation({
    mutationFn: (assetIds: string[]) => unwrap(api.POST("/llm/describe-style", { body: { asset_ids: assetIds } })),
  });
}

/** Claude drafts a character's profile from its saved name, description, traits and looks (a canned draft in mock
 * mode). Nothing is saved. */
export function useDraftProfile() {
  return useMutation({
    mutationFn: (id: string) =>
      unwrap(api.POST("/elements/{element_id}/profile/draft", { params: { path: { element_id: id } } })),
  });
}

/** Claude compares a picture or clip with a character's pictures (a canned answer in mock mode). The answer is kept on
 * the asset (`metadata.consistency[<character id>]`), so the take cards and the viewer read it again. */
export function useCheckLikeness() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, assetId }: { id: string; assetId: string }) =>
      unwrap(api.POST("/elements/{element_id}/check", { params: { path: { element_id: id } }, body: { asset_id: assetId } })),
    onSuccess: (_, { assetId }) => {
      void qc.invalidateQueries({ queryKey: ["generations"] });
      void qc.invalidateQueries({ queryKey: ["asset", assetId] });
    },
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

/** Make or rename one of a character's looks (`@handle:look`). */
export function usePutLook() {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: ({ id, look, ...body }: { id: string; look: string } & Schemas["ElementLook"]) =>
      unwrap(api.PUT("/elements/{element_id}/looks/{look}", { params: { path: { element_id: id, look } }, body })),
    onSuccess: invalidate,
  });
}

/** Forget a look: its pictures leave the character and stay in the library. */
export function useDeleteLook() {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: ({ id, look }: { id: string; look: string }) =>
      unwrap(api.DELETE("/elements/{element_id}/looks/{look}", { params: { path: { element_id: id, look } } })),
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
