"use client";

import { LoaderCircle, Play, Square } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { api, isActive, mediaUrl, unwrap } from "@/lib/api";
import { useClonedVoices } from "@/lib/elements";
import { useGeneration, useModel } from "@/lib/queries";
import { Field } from "@/components/ui/controls";

export const VOICE_MODEL = "eleven-v4";
const SAMPLE = "Hi, this is how I sound. The lamp has burned every night for forty years.";

/** The model's voices as [id, label] pairs: your cloned voices first, where the model takes them, then its own. */
export function useVoices(modelId = VOICE_MODEL): [string, string][] {
  const { data: model } = useModel(modelId);
  const cloned = useClonedVoices();
  const param = model?.parameters.find((p) => p.name === "voice_id");
  if (param?.type !== "enum") return [];
  const labels = (param.labels ?? {}) as Record<string, string>;
  const own = param.options.map((o) => [String(o), labels[String(o)] ?? String(o)] as [string, string]);
  if (!param.open) return own;
  return [...cloned, ...own.filter(([id]) => !cloned.some(([c]) => c === id))]; // a voice added by its ID may be built in
}

/** Pick a voice and hear it. Built-in voices play a ready-made clip (`public/voices/<id>.mp3`); others get a sample
 * made once (the same request returns the same take after that). */
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
  const [noClip, setNoClip] = useState<string[]>([]);
  const clip = value && modelId === VOICE_MODEL && !noClip.includes(value) ? `/voices/${value}.mp3` : undefined;
  const wanted = useRef(false); // play was pressed: a missing clip falls back to making a sample

  useEffect(() => {
    if (url && audio.current && sampleOf?.voice === value) void audio.current.play().catch(() => undefined);
  }, [url, sampleOf, value]);

  async function hear() {
    if (!value) return;
    if (audio.current && !audio.current.paused) {
      audio.current.pause();
    } else if (clip) {
      wanted.current = true;
      void audio.current?.play().catch(() => undefined);
    } else {
      await makeSample(value);
    }
  }

  async function makeSample(voice: string) {
    const post = (key: string) => unwrap(api.POST("/generations", {
      body: { model_id: modelId, mode: "tts", prompt: SAMPLE, params: { voice_id: voice }, media: [], route_policy: "cheapest_healthy" },
      headers: { "Idempotency-Key": key },
    }));
    try {
      const key = `voice-sample:${modelId}:${voice}`;
      let gen = await post(key);
      // the sample made earlier was deleted: make one more, once
      if (gen.status === "succeeded" && !gen.assets?.length) gen = await post(`${key}:${gen.id}`);
      setSampleOf({ voice, generation: gen.id });
      if (gen.status === "succeeded" && audio.current) void audio.current.play().catch(() => undefined);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Couldn't make a sample");
    }
  }

  return (
    <Field label={label}>
      <div className="flex items-center gap-2">
        <select value={value ?? ""} aria-label={label} onChange={(e) => {
                  // a new source stops the old sound without a pause event, which left the button stuck on Stop
                  audio.current?.pause();
                  setPlaying(false);
                  wanted.current = false;
                  onChange(e.target.value || null);
                }}
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
        <audio ref={audio} src={clip ?? (sampleOf?.voice === value ? url : undefined)} preload="none"
               onError={() => {
                 if (!clip || !value) return;
                 setNoClip((n) => [...n, value]);
                 if (wanted.current) void makeSample(value);
                 wanted.current = false;
               }}
               onPlay={() => setPlaying(true)}
               onPause={() => setPlaying(false)} onEnded={() => setPlaying(false)} hidden />
      </div>
    </Field>
  );
}
