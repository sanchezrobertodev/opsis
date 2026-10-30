"use client";

import { LoaderCircle } from "lucide-react";
import Link from "next/link";
import { type MutableRefObject, useId, useState } from "react";
import { toast } from "sonner";
import { ApiError, type Asset, mediaUrl } from "@/lib/api";
import { cn, usd } from "@/lib/cn";
import { useDebounced } from "@/lib/hooks";
import { realParams, type SceneDoc, useSceneProject } from "@/lib/jutsu3d";
import { useRegistry } from "@/lib/queries";
import { type RunCreate, useEstimateRun, useProjectRuns, useStartRun } from "@/lib/runs";
import { Button, Field, Segmented, Select } from "@/components/ui/controls";
import { useModelsFor } from "@/components/create/QuickGenerate";
import { ModelSelect } from "@/components/runs/RunQuote";
import { renderScene, renderTimes } from "./RenderPanel";
import type { ViewportHandle } from "./Viewport";
import { OpensFullSize } from "@/components/ui/PictureViewer";

type Path = "angles" | "depth" | "real";
/** `film`: the pass of the whole timeline a driven path sends (depth, or the picture for real) */
type Inputs = { path: Path; stills: Asset[]; film: Asset | null; version: number };
const PREFERRED: Record<string, string> = {
  i2v: "kling-3.0", flf2v: "kling-3.0", depth_to_video: "wan-vace-depth", render_to_real: "ltx-2.3-render-to-real",
};
const PATH_LABEL: Record<Path, string> = { angles: "Angles", depth: "Depth", real: "Real" };
const FRAME_MODEL = "nano-banana-2"; // the jutsu3d recipe's own default

/**
 * Make the blocked scene real. Angles: stills at the camera's keys are redrawn as finished frames and animated
 * from one to the next. Depth: the depth pass drives a depth-to-video model. Real: the picture pass is re-rendered as
 * live action. Renders the inputs first, so the estimate is for what will actually be sent.
 */
export function MakeReal({ doc, handleRef, projectId }: {
  doc: SceneDoc; handleRef: MutableRefObject<ViewportHandle | null>; projectId: string;
}) {
  const { data: project } = useSceneProject(projectId);
  const { data: registry } = useRegistry();
  const [path, setPath] = useState<Path>("angles");
  const [inputs, setInputs] = useState<Inputs | null>(null);
  const [rendering, setRendering] = useState<number | null>(null);
  const [prompt, setPrompt] = useState("");
  const [opening, setOpening] = useState(true);
  const [chosen, setChosen] = useState<string>();
  const imageModels = useModelsFor("multi_ref").filter((m) => m.output_type === "image");
  const [imageModel, setImageModel] = useState(FRAME_MODEL);
  const [confirm, setConfirm] = useState<number | null>(null);
  const start = useStartRun();
  const ids = { prompt: useId(), model: useId(), image: useId() };

  const keys = renderTimes(doc, "stills");
  const ready = inputs?.path === path ? inputs : null;
  const stillCount = path === "angles" ? keys.length : opening ? 1 : 0;
  const op = path === "depth" ? "depth_to_video" : path === "real" ? "render_to_real" : stillCount > 1 ? "flf2v" : "i2v";
  const real = realParams(doc.timeline.duration, doc.timeline.fps);
  const models = (registry?.models ?? []).filter((m) => m.output_type === "video" && m.kind !== "virtual"
    && m.status !== "shutdown" && m.modes.some((x) => x.ops.includes(op)));
  const modelId = models.some((m) => m.id === chosen) ? chosen! : (models.find((m) => m.id === PREFERRED[op]) ?? models[0])?.id;

  const body: RunCreate | null = ready && modelId && prompt.trim() ? {
    recipe: "jutsu3d",
    input: {
      project_id: projectId, scene_version: ready.version, path, model_id: modelId, image_model_id: imageModel,
      prompt: prompt.trim(),
      // the scene's shape also sizes the redrawn opening frame; the recipe sends each model only what it takes
      params: { ...(path === "real" ? real.params : {}), aspect_ratio: doc.settings.aspect_ratio },
      stills: ready.stills.map((a) => ({ asset_id: a.id, time: Number(a.metadata?.time ?? 0) })),
      ...(ready.film ? { [path === "real" ? "render_asset_id" : "depth_asset_id"]: ready.film.id } : {}),
    },
  } : null;
  const estimate = useEstimateRun(useDebounced(body, 300));
  const problem = estimate.error instanceof ApiError
    ? ((estimate.error.body as { errors?: string[] })?.errors ?? [estimate.error.message]).join(" ") : null;

  async function renderInputs() {
    if (!handleRef.current || !project) return;
    setRendering(0);
    try {
      const v = project.version;
      if (path === "angles") {
        const made = await renderScene(handleRef.current, doc, projectId, v, ["beauty"], "stills", setRendering);
        setInputs({ path, stills: made.map((m) => m.asset), film: null, version: v });
      } else {
        // a long timeline is rendered only as far as render-to-real will go (its clip slot takes 20 s)
        const filmDoc = path === "real" && real.cut ? { ...doc, timeline: { ...doc.timeline, duration: real.seconds } } : doc;
        const film = await renderScene(handleRef.current, filmDoc, projectId, v, [path === "real" ? "beauty" : "depth"], "film",
                                       (f) => setRendering(opening ? f * 0.9 : f));
        const first = opening ? await renderScene(handleRef.current, doc, projectId, v, ["beauty"], "stills", undefined, [0]) : [];
        setInputs({ path, stills: first.map((m) => m.asset), film: film[0]?.asset ?? null, version: v });
      }
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "The render failed");
    } finally {
      setRendering(null);
    }
  }

  async function go(confirmCost?: number) {
    if (!body) return;
    try {
      await start.mutateAsync({ ...body, confirm_cost_usd: confirmCost ?? null });
      setConfirm(null);
      toast("Started. The video appears below when it's done.");
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        setConfirm((err.body as { estimate_usd: number }).estimate_usd);
        return;
      }
      toast.error(err instanceof Error ? err.message : "Could not start");
    }
  }

  const live = estimate.data?.live_estimate_usd;
  const frames = ready ? ready.stills.length : stillCount;
  const clips = path === "angles" && frames > 1 ? frames - 1 : 1;
  const videoName = models.find((m) => m.id === modelId)?.name ?? modelId;
  const imageName = imageModels.find((m) => m.id === imageModel)?.name ?? imageModel;
  const makes = (frames ? `${frames} frame${frames === 1 ? "" : "s"} redrawn on ${imageName}, then ` : "")
    + `${clips} clip${clips === 1 ? "" : "s"} on ${videoName}`;
  return (
    <section className="flex flex-col gap-3 border-t border-line-soft pt-4" aria-label="Make it real">
      <h3 className="text-xs text-muted">Make it real</h3>
      <Segmented label="How" value={path} options={["angles", "depth", "real"] as const}
                 onChange={(p) => { setPath(p); setConfirm(null); }} render={(p) => PATH_LABEL[p]} />
      <p className="text-2xs leading-relaxed text-faint">
        {path === "angles"
          ? `${keys.length} still${keys.length > 1 ? "s" : ""} at the shot camera's keys, each redrawn as a finished frame, then animated ${keys.length > 1 ? "from one to the next" : "from it"}. Key the camera at more moments for more angles.`
          : path === "depth" ? "The depth pass of the whole timeline steers the model, so motion and shapes follow your blocking exactly."
          : `The picture pass of the whole timeline is re-rendered as live action, keeping its framing and motion${
              real.cut ? `; the model makes the first ${real.seconds.toFixed(0)} s` : ""}.`}
      </p>
      {path !== "angles" && (
        <label className="flex items-center gap-2 text-xs text-muted">
          <input type="checkbox" checked={opening} onChange={(e) => setOpening(e.target.checked)} className="accent-[var(--color-tungsten)]" />
          Redraw the first frame and open on it
        </label>
      )}
      <Button size="sm" onClick={() => void renderInputs()} disabled={rendering !== null || !doc.active_camera_id}>
        {rendering !== null ? <><LoaderCircle className="size-4 animate-spin" aria-hidden /> {Math.round(rendering * 100)}%</>
          : ready ? "Render again" : path === "angles" ? "Render the angles" : path === "depth" ? "Render the depth pass"
            : "Render the picture pass"}
      </Button>
      {ready && (
        <ul className="grid grid-cols-3 gap-1.5" aria-label="Inputs">
          {ready.film && (
            <li className="col-span-3">
              <video src={mediaUrl(ready.film.url)} muted loop autoPlay playsInline
                     aria-label={path === "real" ? "Picture pass" : "Depth pass"}
                     className="aspect-video w-full rounded-[3px] bg-black object-contain" />
            </li>
          )}
          {ready.stills.map((a) => (
            <li key={a.id}>
              <OpensFullSize src={mediaUrl(a.url)!} title={`Angle at ${a.metadata?.time ?? 0} s`} className="w-full">
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img src={mediaUrl(a.url)} alt="" className="aspect-video w-full rounded-[3px] bg-black object-cover" />
              </OpensFullSize>
            </li>
          ))}
        </ul>
      )}
      <Field label="What it looks like" htmlFor={ids.prompt}>
        <textarea id={ids.prompt} value={prompt} onChange={(e) => setPrompt(e.target.value)} rows={3} maxLength={2000}
                  placeholder="A rain-soaked rooftop at night, @courier in a yellow jacket, neon signs below"
                  className="resize-y rounded-[var(--radius-control)] border border-line-soft bg-bay px-3 py-2 text-sm leading-relaxed outline-none placeholder:text-faint focus:border-tungsten/60" />
      </Field>
      {models.length > 0 && modelId && (
        <Field label="Video model" htmlFor={ids.model}>
          <Select id={ids.model} value={modelId} onChange={setChosen} options={models.map((m) => ({ value: m.id, label: m.name }))} />
        </Field>
      )}
      {frames > 0 && (
        <ModelSelect id={ids.image} label="Frames redrawn with" models={imageModels} recommended={FRAME_MODEL}
                     value={imageModel} onChange={(id) => { setImageModel(id); setConfirm(null); }}
                     tradeoff="Nano Banana 2 keeps the angle and poses at a low price; others differ in look and cost." />
      )}
      <p className="text-2xs text-muted" aria-live="polite">
        {!ready ? "Render the inputs to see the cost." : !prompt.trim() ? "Describe what it should look like."
          : problem ?? (estimate.data ? `${makes}: ` + (estimate.data.estimate_usd ? `about ${usd(estimate.data.estimate_usd)}` : "free mock run")
            + (live && !estimate.data.estimate_usd ? `; live about ${usd(live)}` : "") : "")}
      </p>
      {confirm !== null ? (
        <Button tone="primary" size="sm" onClick={() => void go(confirm)} disabled={start.isPending}>Start for {usd(confirm)}</Button>
      ) : (
        <Button tone="primary" size="sm" onClick={() => void go()} disabled={!body || Boolean(problem) || start.isPending}>Make it real</Button>
      )}
      <SceneRuns projectId={projectId} />
    </section>
  );
}

function SceneRuns({ projectId }: { projectId: string }) {
  const { data: runs } = useProjectRuns(projectId, "jutsu3d");
  if (!runs?.length) return null;
  return (
    <ul className="flex flex-col gap-2 pt-1" aria-label="Made from this scene">
      {runs.map((r) => (
        <li key={r.id} className="flex flex-col gap-1">
          {r.output_asset?.kind === "video" && (
            <video src={mediaUrl(r.output_asset.url)} poster={mediaUrl(r.output_asset.poster_url)} controls playsInline
                   className="aspect-video w-full rounded-[3px] bg-black object-contain" />
          )}
          <Link href={`/runs/${r.id}`} className={cn("flex items-center gap-1.5 text-2xs hover:text-chalk",
                                                     r.status === "failed" ? "text-fault" : "text-muted")}>
            {["running", "waiting"].includes(r.status) && <LoaderCircle className="size-3 animate-spin text-tungsten" aria-hidden />}
            {r.status === "failed" ? `Failed: ${r.error ?? "see the steps"}` : r.status === "succeeded" ? r.title
              : `${r.steps_done} of ${r.steps_total} steps`}
          </Link>
        </li>
      ))}
    </ul>
  );
}
