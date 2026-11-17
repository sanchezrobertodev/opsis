"use client";

import type { Schemas } from "@opsis/api-client";
import { useMutation, useQuery } from "@tanstack/react-query";
import { api, unwrap } from "./api";

export type StarterCharacter = Schemas["StarterCharacter"];

/** The character designer's vocabulary (presentations, a person's and a creature's traits) and the starter characters,
 * from `registry/characters/`. The helpers in `designer.ts` take it as their `vocab`. */
export function useCharacterCatalog() {
  return useQuery({ queryKey: ["characters", "catalog"], queryFn: () => unwrap(api.GET("/characters/catalog")),
                    staleTime: 5 * 60_000 });
}

/** Claude's picks for the designer from a picture of yours or a sentence: traits, a name and a catalog style (a canned
 * draft in mock mode). Nothing is saved. */
export function useDraftCharacter() {
  return useMutation({
    mutationFn: (body: Schemas["CharacterDraftIn"]) => unwrap(api.POST("/characters/draft", { body })),
  });
}
