"use client";

import { useQuery } from "@tanstack/react-query";
import { LoaderCircle } from "lucide-react";
import Link from "next/link";
import { useRef, useState } from "react";
import { toast } from "sonner";
import { api, ApiError, type MediaSlot, mediaUrl, unwrap } from "@/lib/api";
import { cn, usd } from "@/lib/cn";
import type { SlotAsset } from "@/lib/draft";
import { useDebounced } from "@/lib/hooks";
import { useRegistry } from "@/lib/queries";
import { type RunCreate, useAnswerCheckpoint, useEstimateRun, useStartRun } from "@/lib/runs";
import { MediaSlotField } from "@/components/create/MediaSlots";
import { Button, Field, Segmented } from "@/components/ui/controls";
import { VOICE_MODEL, VoicePicker } from "@/components/voice/VoicePicker";

const WHO: MediaSlot = { role: "character_image", kinds: ["image", "video"], label: "Who speaks: a picture or a clip",
                         min: 1, max: 1, required: true, auto: false };
const RECORDING: MediaSlot = { role: "input_audio", kinds: ["audio"], label: "Recorded speech", min: 1, max: 1,
                               required: true, auto: false };
const MAX_MODELS = 3;
const DEFAULT_VOICE = "JBFqnCBsd6RMkjVDRZzb";

function useLiveRun(id: string | null) {
  return useQuery({
    queryKey: ["run", id],
    queryFn: () => unwrap(api.GET("/pipelines/{run_id}", { params: { path: { run_id: id! } } })),
    enabled: Boolean(id),
    refetchInterval: (q) => (q.state.data && !["running", "waiting"].includes(q.state.data.status) ? false : 1500),
  });
}

/** Make a picture or a clip speak a script or a recording, on one model or several side by side. */
export function Lipsync() {
  const { data: registry } = useRegistry();
  const start = useStartRun();
  const [who, setWho] = useState<SlotAsset[]>([]);
  const [speech, setSpeech] = useState<"script" | "recording">("script");
  const [script, setScript] = useState("");
  const [recording, setRecording] = useState<SlotAsset[]>([]);
  const [voice, setVoice] = useState<string | null>(DEFAULT_VOICE);
  const [prompt, setPrompt] = useState("");
  const [chosen, setChosen] = useState<string[] | null>(null);
  const [confirm, setConfirm] = useState<number | null>(null);
  const [runId, setRunId] = useState<string | null>(null);

  const source = who[0];
  const isClip = source?.kind === "video";
  // pictures need a talking-portrait model, clips a lipsync model; both are picked by what their modes do
  const models = (registry?.models ?? []).filter((m) => m.output_type === "video" && m.modes.some(
    (x) => x.ops.includes("lipsync") && (isClip ? !x.ops.includes("avatar") : x.ops.includes("avatar"))));
  const picked = (chosen ?? models.slice(0, 1).map((m) => m.id)).filter((id) => models.some((m) => m.id === id));
  const hasSpeech = speech === "script" ? script.trim().length > 0 : recording.length > 0;

  const body: RunCreate | null = source && hasSpeech && picked.length ? {
    recipe: "talking_shot",
    input: {
      ...(isClip ? { video_asset_id: source.id } : { image_asset_id: source.id }),
      ...(speech === "script" ? { script: script.trim(), voice_model: VOICE_MODEL, voice: voice ? { voice_id: voice } : {} }
        : { audio_asset_id: recording[0].id }),
      prompt: prompt.trim(), models: picked.map((model_id) => ({ model_id })),
    },
  } : null;
  const estimate = useEstimateRun(useDebounced(body, 300));
  const problem = estimate.error instanceof ApiError
    ? ((estimate.error.body as { errors?: string[] })?.errors ?? [estimate.error.message]).join(" ") : null;

  async function go(confirmCost?: number) {
    if (!body) return;
    try {
      const run = await start.mutateAsync({ ...body, confirm_cost_usd: confirmCost ?? null });
      setRunId(run.id);
      setConfirm(null);
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        setConfirm((err.body as { estimate_usd: number }).estimate_usd);
        return;
      }
      toast.error(err instanceof Error ? err.message : "Could not start");
    }
  }

  const toggle = (id: string) => setChosen(picked.includes(id) ? picked.filter((m) => m !== id) : [...picked, id].slice(-MAX_MODELS));

  return (
    <div className="grid h-full grid-cols-1 lg:grid-cols-[minmax(0,1fr)_360px]">
      <div className="min-h-0 overflow-y-auto p-6 scrollbar-quiet">
        <div className="flex max-w-3xl flex-col gap-6">
          <MediaSlotField slot={WHO} value={who} onAdd={(a) => { setWho([a]); setChosen(null); setRunId(null); }}
                          onRemove={() => { setWho([]); setRunId(null); }} />
          <section className="flex flex-col gap-3">
            <Segmented label="Speech" value={speech} options={["script", "recording"] as const}
                       render={(v) => (v === "script" ? "Type a script" : "Use a recording")} onChange={setSpeech} />
            {speech === "script" ? (
              <Field label="What they say" htmlFor="lipsync-script" hint={`${script.trim().length} of 5000 characters`}>
                <textarea id="lipsync-script" value={script} onChange={(e) => setScript(e.target.value)} rows={5} maxLength={5000}
                          placeholder="The lamp has burned every night for forty years. Tonight it goes dark."
                          className="resize-y rounded-[var(--radius-control)] border border-line-soft bg-bay px-3 py-2 text-sm leading-relaxed outline-none placeholder:text-faint focus:border-tungsten/60" />
              </Field>
            ) : (
              <MediaSlotField slot={RECORDING} value={recording} onAdd={(a) => setRecording([a])} onRemove={() => setRecording([])} />
            )}
          </section>
          {runId ? <Result runId={runId} source={source} /> : (
            <p className="text-sm text-muted">
              {source ? "Pick a model on the right, then start. The take lands here next to the original."
                : "Add a portrait, a drawing or a clip with a visible face."}
            </p>
          )}
        </div>
      </div>

      <aside className="flex min-h-0 flex-col gap-5 overflow-y-auto border-t border-line-soft p-5 scrollbar-quiet lg:border-t-0 lg:border-l">
        <Field label={isClip ? "Lip sync with" : "Animate with"} hint={`Pick up to ${MAX_MODELS} to compare them.`}>
          {models.length ? (
            <ul className="flex flex-col gap-1.5">
              {models.map((m) => (
                <li key={m.id}>
                  <button type="button" onClick={() => toggle(m.id)} aria-pressed={picked.includes(m.id)}
                          className={cn("flex w-full flex-col rounded-[var(--radius-control)] border px-3 py-2 text-left",
                                        picked.includes(m.id) ? "border-tungsten/60 bg-raised" : "border-line-soft hover:border-line")}>
                    <span className="text-sm text-chalk">{m.name}</span>
                    <span className="line-clamp-2 text-2xs text-faint">{m.description}</span>
                  </button>
                </li>
              ))}
            </ul>
          ) : <p className="text-2xs text-faint">Add who speaks first.</p>}
        </Field>
        {speech === "script" && <VoicePicker value={voice} onChange={setVoice} />}
        {!isClip && (
          <Field label="How they move (optional)" htmlFor="lipsync-prompt">
            <textarea id="lipsync-prompt" value={prompt} onChange={(e) => setPrompt(e.target.value)} rows={2} maxLength={1000}
                      placeholder="Leans in and speaks quietly, small hand gestures"
                      className="resize-y rounded-[var(--radius-control)] border border-line-soft bg-bay px-3 py-2 text-sm outline-none placeholder:text-faint focus:border-tungsten/60" />
          </Field>
        )}
        <div className="mt-auto flex flex-col gap-2">
          <p className="text-2xs text-faint">
            {problem ?? (estimate.data ? (estimate.data.estimate_usd ? `About ${usd(estimate.data.estimate_usd)}` : "Free mock run")
              + (estimate.data.live_estimate_usd ? `; live it would cost about ${usd(estimate.data.live_estimate_usd)}` : "") : "")}
          </p>
          {confirm !== null ? (
            <Button tone="primary" onClick={() => void go(confirm)}>Start for {usd(confirm)}</Button>
          ) : (
            <Button tone="primary" onClick={() => void go()} disabled={!body || Boolean(problem) || start.isPending}>
              Make it speak
            </Button>
          )}
        </div>
      </aside>
    </div>
  );
}

function Result({ runId, source }: { runId: string; source: SlotAsset | undefined }) {
  const { data: run } = useLiveRun(runId);
  const answer = useAnswerCheckpoint();
  const [view, setView] = useState<"result" | "source" | "both">("result");
  const result = useRef<HTMLVideoElement>(null);
  const original = useRef<HTMLVideoElement>(null);
  if (!run) return null;
  const assets = new Map(run.steps.flatMap((s) => s.assets).map((a) => [a.id, a]));
  const take = assets.get(String(run.output.asset_id ?? ""));
  const pick = run.steps.find((s) => s.kind === "checkpoint" && s.status === "waiting");
  const isClip = source?.kind === "video";

  // the original clip follows the take, so the two play side by side in step
  const follow = (play: boolean) => {
    if (!original.current || !result.current || !isClip) return;
    original.current.currentTime = result.current.currentTime;
    void (play ? original.current.play() : Promise.resolve(original.current.pause())).catch(() => undefined);
  };

  if (run.status === "failed") return <p className="text-sm text-fault">{run.error}</p>;
  if (pick) {
    return (
      <section className="flex flex-col gap-3">
        <h2 className="text-sm text-chalk">Pick the take you like</h2>
        <ul className="grid gap-3 sm:grid-cols-2">
          {(pick.options ?? []).map((o) => {
            const a = assets.get(String(o.value));
            return (
              <li key={String(o.value)} className="flex flex-col gap-2">
                {a && <video src={mediaUrl(a.url)} poster={mediaUrl(a.poster_url)} controls playsInline className="aspect-video w-full rounded-[var(--radius-frame)] bg-black" />}
                <Button size="sm" onClick={() => answer.mutate({ id: run.id, key: pick.key, choice: String(o.value) })}>
                  Keep {String(o.label)}
                </Button>
              </li>
            );
          })}
        </ul>
      </section>
    );
  }
  if (!take) {
    const steps = run.steps.filter((s) => s.kind === "generate");
    return (
      <p className="flex items-center gap-2 text-sm text-muted">
        <LoaderCircle className="size-4 animate-spin text-tungsten" aria-hidden />
        {steps.find((s) => s.status !== "succeeded")?.label ?? "Starting"}
      </p>
    );
  }
  const sourcePane = source && (isClip
    ? <video ref={original} src={mediaUrl(source.url)} muted playsInline controls={view === "source"} className="aspect-video w-full rounded-[var(--radius-frame)] bg-black object-contain" />
    // eslint-disable-next-line @next/next/no-img-element
    : <img src={mediaUrl(source.url)} alt="The original picture" className="aspect-video w-full rounded-[var(--radius-frame)] bg-black object-contain" />);
  const takePane = (
    <video ref={result} src={mediaUrl(take.url)} poster={mediaUrl(take.poster_url)} controls playsInline
           onPlay={() => follow(true)} onPause={() => follow(false)} onSeeked={() => follow(!result.current?.paused)}
           aria-label="The talking take" className="aspect-video w-full rounded-[var(--radius-frame)] bg-black object-contain" />
  );
  return (
    <section className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-3">
        <Segmented label="Show" value={view} options={["result", "source", "both"] as const} onChange={setView}
                   render={(v) => (v === "result" ? "Take" : v === "source" ? "Original" : "Side by side")} />
        <Link href={`/runs/${run.id}`} className="text-2xs text-muted hover:text-chalk">See every step</Link>
      </div>
      {view === "both" ? <div className="grid gap-3 sm:grid-cols-2">{sourcePane}{takePane}</div>
        : view === "source" ? sourcePane : takePane}
    </section>
  );
}
