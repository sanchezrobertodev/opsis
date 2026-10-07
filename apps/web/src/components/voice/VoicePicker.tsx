"use client";

import { LoaderCircle, Play, Square } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { api, isActive, mediaUrl, unwrap } from "@/lib/api";
import { useGeneration, useModel } from "@/lib/queries";
import { Field } from "@/components/ui/controls";

export const VOICE_MODEL = "eleven-v4";
const SAMPLE = "Hi, this is how I sound. The lamp has burned every night for forty years.";

/** The text-to-speech model's voices, as [id, label] pairs. */
export function useVoices(modelId = VOICE_MODEL): [string, string][] {
  const { data: model } = useModel(modelId);
  const param = model?.parameters.find((p) => p.name === "voice_id");
  if (param?.type !== "enum") return [];
  const labels = (param.labels ?? {}) as Record<string, string>;
  return param.options.map((o) => [String(o), labels[String(o)] ?? String(o)]);
}

/** Pick a voice and hear it. A sample is made once per voice (the same request returns the same take after that). */
export function VoicePicker({ value, onChange, label = "Voice", allowNone, noneLabel = "No voice set", modelId = VOICE_MODEL }: {
  value: string | null | undefined; onChange: (voiceId: string | null) => void; label?: string;
  allowNone?: boolean; noneLabel?: string; modelId?: string;
}) {
  const voices = useVoices(modelId);
  const [sampleOf, setSampleOf] = useState<{ voice: string; generation: string } | null>(null);
  const [playing, setPlaying] = useState(false);
  const audio = useRef<HTMLAudioElement>(null);
  const { data: sample } = useGeneration(sampleOf?.generation);
  const url = sample?.status === "succeeded" ? mediaUrl(sample.assets?.[0]?.url) : undefined;
  const making = Boolean(sampleOf) && (!sample || isActive(sample.status));

  useEffect(() => {
    if (url && audio.current && sampleOf?.voice === value) void audio.current.play().catch(() => undefined);
  }, [url, sampleOf, value]);

  async function hear() {
    if (!value) return;
    if (playing) {
      audio.current?.pause();
      return;
    }
    try {
      const gen = await unwrap(api.POST("/generations", {
        body: { model_id: modelId, mode: "tts", prompt: SAMPLE, params: { voice_id: value }, media: [], route_policy: "cheapest_healthy" },
        headers: { "Idempotency-Key": `voice-sample:${modelId}:${value}` },
      }));
      setSampleOf({ voice: value, generation: gen.id });
      if (gen.status === "succeeded" && audio.current) void audio.current.play().catch(() => undefined);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Couldn't make a sample");
    }
  }

  return (
    <Field label={label}>
      <div className="flex items-center gap-2">
        <select value={value ?? ""} aria-label={label} onChange={(e) => onChange(e.target.value || null)}
                className="h-9 min-w-0 flex-1 rounded-[var(--radius-control)] border border-line-soft bg-bay px-2 text-sm text-chalk outline-none focus:border-tungsten/60">
          {(allowNone || !value) && <option value="">{noneLabel}</option>}
          {voices.map(([id, name]) => <option key={id} value={id}>{name}</option>)}
        </select>
        <button type="button" onClick={() => void hear()} disabled={!value || making}
                aria-label={playing ? "Stop the sample" : "Hear this voice"}
                className="grid size-9 shrink-0 place-items-center rounded-full border border-line-soft text-muted hover:border-tungsten/50 hover:text-chalk disabled:opacity-40">
          {making ? <LoaderCircle className="size-4 animate-spin" aria-hidden />
            : playing ? <Square className="size-3.5" aria-hidden /> : <Play className="size-4" aria-hidden />}
        </button>
        <audio ref={audio} src={sampleOf?.voice === value ? url : undefined} onPlay={() => setPlaying(true)}
               onPause={() => setPlaying(false)} onEnded={() => setPlaying(false)} hidden />
      </div>
    </Field>
  );
}
