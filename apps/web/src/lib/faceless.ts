"use client";

import type { Schemas } from "@studio/api-client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, unwrap } from "./api";

export type FacelessCatalog = Schemas["CatalogOut"];
export type FacelessStyle = Schemas["StyleOut"];
export type Channel = Schemas["ChannelOut"];
export type TopicIdea = Schemas["TopicOut"];

/** The episode settings a channel can preset (the faceless recipe's input, minus topic, script and title). */
export type EpisodeSettings = {
  format: "stills" | "motion";
  minutes: number;
  aspect_ratio: "16:9" | "9:16";
  resolution: "720p" | "1080p";
  language: string;
  notes: string;
  voice_model: string;
  voice: Record<string, unknown>;
  image_model: string;
  image_params: Record<string, unknown>;
  video_model: string;
  video_params: Record<string, unknown>;
  music: boolean;
  music_model: string;
  captions: boolean;
  thumbnail: boolean;
  review_script: boolean;
};

export type CaptionLook = {
  font: string;
  size: number;
  bold: boolean;
  color: string;
  outline: string;
  highlight: string;
  uppercase: boolean;
};

const channelsKey = ["faceless", "channels"] as const;

export function useFacelessCatalog() {
  return useQuery({ queryKey: ["faceless", "catalog"], queryFn: () => unwrap(api.GET("/faceless/catalog")),
                    staleTime: 5 * 60_000 });
}

export function useChannels() {
  return useQuery({ queryKey: channelsKey, queryFn: () => unwrap(api.GET("/faceless/channels")) });
}

export function useChannelEpisodes(channelId: string | undefined) {
  return useQuery({
    queryKey: ["runs", "channel", channelId],
    queryFn: () => unwrap(api.GET("/pipelines", { params: { query: { channel_id: channelId!, limit: 6 } } })),
    enabled: Boolean(channelId),
  });
}

export function useSuggestTopics() {
  return useMutation({
    mutationFn: (body: { theme: string; channel_id?: string | null; notes?: string }) =>
      unwrap(api.POST("/faceless/topics", { body: { notes: "", ...body } })),
  });
}

export function useSaveChannel() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, ...body }: { id?: string; name: string; theme: string; style: string; notes: string;
                                     defaults: Partial<EpisodeSettings> }) =>
      id
        ? unwrap(api.PATCH("/faceless/channels/{channel_id}", { params: { path: { channel_id: id } }, body }))
        : unwrap(api.POST("/faceless/channels", { body })),
    onSuccess: () => qc.invalidateQueries({ queryKey: channelsKey }),
  });
}
