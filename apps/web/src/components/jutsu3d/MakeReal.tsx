"use client";

import { LoaderCircle } from "lucide-react";
import Link from "next/link";
import { type MutableRefObject, useId, useState } from "react";
import { toast } from "sonner";
import { ApiError, type Asset, mediaUrl } from "@/lib/api";
import { cn, usd } from "@/lib/cn";
import { useDebounced } from "@/lib/hooks";
import { type SceneDoc, useSceneProject } from "@/lib/jutsu3d";
import { useRegistry } from "@/lib/queries";
import { type RunCreate, useEstimateRun, useProjectRuns, useStartRun } from "@/lib/runs";
import { Button, Field, Segmented, Select } from "@/components/ui/controls";
import { renderScene, renderTimes } from "./RenderPanel";
import type { ViewportHandle } from "./Viewport";

type Path = "angles" | "depth";
type Inputs = { path: Path; stills: Asset[]; depth: Asset | null; version: number };
const PREFERRED: Record<string, string> = { i2v: "kling-3.0", flf2v: "kling-3.0", depth_to_video: "wan-vace-depth" };

/**
 * Make the blocked scene real. Angles: stills at the camera's keys are redrawn as finished frames and animated
 * from one to the next. Depth: the depth pass drives a depth-to-video model. Renders the inputs first, so the
 * estimate is for what will actually be sent.
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
  const [confirm, setConfirm] = useState<number | null>(null);
  const start = useStartRun();
  const ids = { prompt: useId(), model: useId() };

  const keys = renderTimes(doc, "stills");
  const ready = inputs?.path === path ? inputs : null;
  const stillCount = path === "angles" ? keys.length : opening ? 1 : 0;
  const op = path === "depth" ? "depth_to_video" : stillCount > 1 ? "flf2v" : "i2v";
  const models = (registry?.models ?? []).filter((m) => m.output_type === "video" && m.kind !== "virtual"
    && m.status !== "shutdown" && m.modes.some((x) => x.ops.includes(op)));
  const modelId = models.some((m) => m.id === chosen) ? chosen! : (models.find((m) => m.id === PREFERRED[op]) ?? models[0])?.id;

  const body: RunCreate | null = ready && modelId && prompt.trim() ? {
    recipe: "jutsu3d",
    input: {
      project_id: projectId, scene_version: ready.version, path, model_id: modelId, prompt: prompt.trim(),
      params: { aspect_ratio: doc.settings.aspect_ratio },
      stills: ready.stills.map((a) => ({ asset_id: a.id, time: Number(a.metadata?.time ?? 0) })),
      ...(ready.depth ? { depth_asset_id: ready.depth.id } : {}),
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
        setInputs({ path, stills: made.map((m) => m.asset), depth: null, version: v });
      } else {
        const film = await renderScene(handleRef.current, doc, projectId, v, ["depth"], "film",
                                       (f) => setRendering(opening ? f * 0.9 : f));
        const first = opening ? await renderScene(handleRef.current, doc, projectId, v, ["beauty"], "stills", undefined, [0]) : [];
        setInputs({ path, stills: first.map((m) => m.asset), depth: film[0]?.asset ?? null, version: v });
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
  return (
    <section className="flex flex-col gap-3 border-t border-line-soft pt-4" aria-label="Make it real">
      <h3 className="text-xs text-muted">Make it real</h3>
      <Segmented label="How" value={path} options={["angles", "depth"] as const} onChange={(p) => { setPath(p); setConfirm(null); }}
                 render={(p) => (p === "angles" ? "Angles" : "Depth")} />
      <p className="text-2xs leading-relaxed text-faint">
        {path === "angles"
          ? `${keys.length} still${keys.length > 1 ? "s" : ""} at the shot camera's keys, each redrawn as a finished frame, then animated ${keys.length > 1 ? "from one to the next" : "from it"}. Key the camera at more moments for more angles.`
          : "The depth pass of the whole timeline steers the model, so motion and shapes follow your blocking exactly."}
      </p>
      {path === "depth" && (
        <label className="flex items-center gap-2 text-xs text-muted">
          <input type="checkbox" checked={opening} onChange={(e) => setOpening(e.target.checked)} className="accent-[var(--color-tungsten)]" />
          Redraw the first frame and open on it
        </label>
      )}
      <Button size="sm" onClick={() => void renderInputs()} disabled={rendering !== null || !doc.active_camera_id}>
        {rendering !== null ? <><LoaderCircle className="size-4 animate-spin" aria-hidden /> {Math.round(rendering * 100)}%</>
          : ready ? "Render again" : path === "angles" ? "Render the angles" : "Render the depth pass"}
      </Button>
      {ready && (
        <ul className="grid grid-cols-3 gap-1.5" aria-label="Inputs">
          {ready.depth && (
            <li className="col-span-3">
              <video src={mediaUrl(ready.depth.url)} muted loop autoPlay playsInline aria-label="Depth pass"
                     className="aspect-video w-full rounded-[3px] bg-black object-contain" />
            </li>
          )}
          {ready.stills.map((a) => (
            // eslint-disable-next-line @next/next/no-img-element
            <li key={a.id}><img src={mediaUrl(a.url)} alt={`Angle at ${a.metadata?.time ?? 0} s`}
                                className="aspect-video w-full rounded-[3px] bg-black object-cover" /></li>
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
      <p className="text-2xs text-muted" aria-live="polite">
        {!ready ? "Render the inputs to see the cost." : !prompt.trim() ? "Describe what it should look like."
          : problem ?? (estimate.data ? (estimate.data.estimate_usd ? `About ${usd(estimate.data.estimate_usd)}` : "Free mock run")
            + (live ? `; live about ${usd(live)}` : "") : "")}
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
