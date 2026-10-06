"use client";

import { create } from "zustand";
import { createJSONStorage, persist } from "zustand/middleware";

export type SlotAsset = { id: string; kind: string; thumb?: string | null; url: string; title?: string | null };

type Draft = {
  modelId?: string;
  mode?: string;
  prompt: string;
  negativePrompt: string;
  /** Params remembered per model, so switching models doesn't lose settings. */
  params: Record<string, Record<string, unknown>>;
  /** Media chosen per role. Roles the current mode lacks are kept but not sent. */
  media: Record<string, SlotAsset[]>;
  takes: number;
};

type DraftState = Record<"video" | "image", Draft> & {
  set: (studio: "video" | "image", patch: Partial<Draft>) => void;
  setParam: (studio: "video" | "image", modelId: string, name: string, value: unknown) => void;
  addMedia: (studio: "video" | "image", role: string, asset: SlotAsset, max: number) => void;
  removeMedia: (studio: "video" | "image", role: string, id: string) => void;
};

const empty: Draft = { prompt: "", negativePrompt: "", params: {}, media: {}, takes: 1 };

export const useDraft = create<DraftState>()(
  persist(
    (set) => ({
      video: empty,
      image: empty,
      set: (studio, patch) => set((s) => ({ [studio]: { ...s[studio], ...patch } }) as Partial<DraftState>),
      setParam: (studio, modelId, name, value) =>
        set((s) => {
          const d = s[studio];
          return {
            [studio]: { ...d, params: { ...d.params, [modelId]: { ...d.params[modelId], [name]: value } } },
          } as Partial<DraftState>;
        }),
      addMedia: (studio, role, asset, max) =>
        set((s) => {
          const d = s[studio];
          const current = (d.media[role] ?? []).filter((a) => a.id !== asset.id);
          const next = max <= 1 ? [asset] : [...current, asset].slice(-max);
          return { [studio]: { ...d, media: { ...d.media, [role]: next } } } as Partial<DraftState>;
        }),
      removeMedia: (studio, role, id) =>
        set((s) => {
          const d = s[studio];
          return {
            [studio]: { ...d, media: { ...d.media, [role]: (d.media[role] ?? []).filter((a) => a.id !== id) } },
          } as Partial<DraftState>;
        }),
    }),
    {
      name: "studio-drafts",
      version: 1,
      storage: createJSONStorage(() => localStorage),
      partialize: (s) => ({ video: s.video, image: s.image }),
    },
  ),
);
