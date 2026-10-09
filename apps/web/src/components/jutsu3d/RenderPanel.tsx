"use client";

import { LoaderCircle } from "lucide-react";
import Link from "next/link";
import { type MutableRefObject, useState } from "react";
import { toast } from "sonner";
import * as THREE from "three";
import { API_URL, type Asset, mediaUrl } from "@/lib/api";
import { cn } from "@/lib/cn";
import { evaluate, fovFor, LIGHTS, type SceneDoc, trackOf, useScene, useSceneProject } from "@/lib/jutsu3d";
import { Button, Field, Segmented } from "@/components/ui/controls";
import { sparkIn } from "@/components/three/Splat";
import type { ViewportHandle } from "./Viewport";

export type Pass = "beauty" | "depth" | "normal";
export const PASS_LABEL: Record<Pass, string> = { beauty: "Picture", depth: "Depth", normal: "Normals" };
export const FRAME_SIZE: Record<SceneDoc["settings"]["aspect_ratio"], [number, number]> = {
  "16:9": [1280, 720], "9:16": [720, 1280], "1:1": [1024, 1024], "21:9": [1344, 576], "4:3": [1024, 768],
};
const BATCH = 12;

/**
 * Linear depth in view space, white at the near plane and black at the far one (what depth-guided models expect).
 * three.js's skinning chunks pose rigged models as the picture shows them (they're empty for other meshes).
 */
function depthMaterial() {
  return new THREE.ShaderMaterial({
    uniforms: { uNear: { value: 0.1 }, uFar: { value: 50 } },
    vertexShader: `#include <common>
#include <skinning_pars_vertex>
varying float vDepth;
void main() {
  #include <skinbase_vertex>
  #include <begin_vertex>
  #include <skinning_vertex>
  vec4 mv = modelViewMatrix * vec4(transformed, 1.0);
  vDepth = -mv.z;
  gl_Position = projectionMatrix * mv;
}`,
    fragmentShader: "uniform float uNear; uniform float uFar; varying float vDepth; void main() { float d = clamp((vDepth - uNear) / (uFar - uNear), 0.0, 1.0); gl_FragColor = vec4(vec3(1.0 - d), 1.0); }",
  });
}

function blobOf(canvas: HTMLCanvasElement, type: string): Promise<Blob> {
  return new Promise((resolve, reject) => canvas.toBlob((b) => (b ? resolve(b) : reject(new Error("couldn't read the frame"))), type, 0.92));
}

async function post(path: string, init: RequestInit) {
  const r = await fetch(`${API_URL}${path}`, init);
  if (!r.ok) throw new Error((await r.json().catch(() => null))?.detail ?? `render failed (${r.status})`);
  return r.status === 204 ? null : r.json();
}

/** The times to draw: every frame of the timeline, or one still at each of the shot camera's keys. */
export function renderTimes(doc: SceneDoc, what: "film" | "stills"): number[] {
  if (what === "film") return Array.from({ length: Math.round(doc.timeline.duration * doc.timeline.fps) },
                                         (_, i) => i / doc.timeline.fps);
  const keys = doc.active_camera_id ? trackOf(doc, doc.active_camera_id)?.keys.map((k) => k.t) ?? [] : [];
  return keys.length ? [...new Set(keys)] : [0];
}

/**
 * Draws the scene through the shot camera at each time, for each pass, straight on the editor's canvas (paused
 * meanwhile), and hands each frame to `onFrame`. Everything it moves or hides is put back afterwards.
 */
async function drawFrames(handleRef: ViewportHandle, doc: SceneDoc, times: number[], passes: Pass[],
                          onFrame: (pass: Pass, index: number, frame: Blob) => Promise<void>) {
  const { gl, scene } = handleRef;
  const shot = doc.objects.find((o) => o.id === doc.active_camera_id);
  if (!shot) throw new Error("Pick a camera to shoot through first.");
  const [w, h] = FRAME_SIZE[doc.settings.aspect_ratio];
  const camera = new THREE.PerspectiveCamera(35, w / h, 0.05, 500);
  const before = { size: gl.getSize(new THREE.Vector2()), ratio: gl.getPixelRatio(), tone: gl.toneMapping,
                   space: gl.outputColorSpace, background: scene.background, override: scene.overrideMaterial };
  const moved = new Map<string, [THREE.Vector3, THREE.Euler, THREE.Vector3]>();
  const hidden: THREE.Object3D[] = [];
  scene.traverse((n) => { if (n.userData.helper && n.visible) { hidden.push(n); n.visible = false; } });
  // splats are pictures without a surface: data passes draw a world's collision mesh instead
  const splats: THREE.Object3D[] = [], stands: THREE.Object3D[] = [];
  scene.traverse((n) => { if (n.userData.splat) splats.push(n); if (n.userData.depthOnly) stands.push(n); });
  const spark = sparkIn(scene);
  const depth = depthMaterial();
  const normals = new THREE.MeshNormalMaterial();
  handleRef.setFrameloop("never");
  try {
    gl.setPixelRatio(1);
    gl.setSize(w, h, false);
    for (const [i, t] of times.entries()) {
      for (const o of doc.objects) {
        const g = scene.getObjectByName(o.id);
        if (!g) continue;
        if (!moved.has(o.id)) moved.set(o.id, [g.position.clone(), g.rotation.clone(), g.scale.clone()]);
        const now = evaluate(o, trackOf(doc, o.id), t);
        g.position.set(...now.transform.position);
        g.rotation.set(...now.transform.rotation);
        g.scale.set(...now.transform.scale);
      }
      // rigged models playing a clip take their pose for this frame
      scene.traverse((n) => { (n.userData.seek as ((t: number) => void) | undefined)?.(t); });
      const cam = evaluate(shot, trackOf(doc, shot.id), t);
      camera.position.set(...cam.transform.position);
      camera.lookAt(...cam.target);
      camera.fov = fovFor(cam.focal_length);
      scene.updateMatrixWorld(true);
      for (const pass of passes) {
        const data = pass !== "beauty";
        // data passes skip tone mapping so their values stay true; depth spans the scene in front of the camera
        gl.toneMapping = data ? THREE.NoToneMapping : before.tone;
        gl.outputColorSpace = data ? THREE.LinearSRGBColorSpace : before.space;
        scene.overrideMaterial = pass === "depth" ? depth : pass === "normal" ? normals : before.override;
        splats.forEach((n) => { n.visible = !data; });
        stands.forEach((n) => { n.visible = data; });
        scene.background = pass === "depth" ? new THREE.Color("#000000") : pass === "normal" ? new THREE.Color("#8080ff")
          : before.background;
        if (pass === "depth") {
          // the range spans the scene's own objects (not the endless floor), so the subject gets the contrast
          const bounds = new THREE.Box3();
          doc.objects.forEach((o) => {
            const g = scene.getObjectByName(o.id);
            if (g && o.visible && o.kind !== "camera" && o.kind !== "world" && !LIGHTS.includes(o.kind)) bounds.expandByObject(g);
          });
          const sphere = bounds.isEmpty() ? new THREE.Sphere(new THREE.Vector3(), 5) : bounds.getBoundingSphere(new THREE.Sphere());
          // a world surrounds the camera: reach well past the figures so its walls and ground read too
          const world = doc.objects.some((o) => o.kind === "world" && o.visible) ? 40 : 0;
          camera.near = 0.1;
          camera.far = Math.max(2, world, camera.position.distanceTo(sphere.center) + sphere.radius * 1.5);
          depth.uniforms.uNear.value = Math.max(0.1, camera.position.distanceTo(sphere.center) - sphere.radius * 1.5);
          depth.uniforms.uFar.value = camera.far;
        } else {
          camera.near = 0.05;
          camera.far = 500;
        }
        camera.updateProjectionMatrix();
        if (spark && !data) await spark.update({ scene, camera }); // sort the splats for this exact view
        gl.render(scene, camera);
        await onFrame(pass, i, await blobOf(gl.domElement, pass === "beauty" ? "image/jpeg" : "image/png"));
      }
    }
  } finally {
    for (const [id, [p, r, s]] of moved) {
      const g = scene.getObjectByName(id);
      g?.position.copy(p); g?.rotation.copy(r); g?.scale.copy(s);
    }
    hidden.forEach((n) => { n.visible = true; });
    // and back to where the editor's playhead has them
    scene.traverse((n) => { (n.userData.seek as ((t: number | null) => void) | undefined)?.(n.userData.at ?? null); });
    splats.forEach((n) => { n.visible = true; });
    stands.forEach((n) => { n.visible = false; });
    Object.assign(scene, { background: before.background, overrideMaterial: before.override });
    gl.toneMapping = before.tone;
    gl.outputColorSpace = before.space;
    gl.setPixelRatio(before.ratio);
    gl.setSize(before.size.x, before.size.y, false);
    depth.dispose();
    normals.dispose();
    handleRef.setFrameloop("always");
  }
}

export type Rendered = { pass: Pass; asset: Asset }[];

/**
 * Renders passes through the shot camera, uploads the frames and returns the assets: one video per pass for the
 * timeline, or a still per pass at each camera key.
 */
export async function renderScene(handle: ViewportHandle, doc: SceneDoc, projectId: string, version: number,
                                  passes: Pass[], what: "film" | "stills", onProgress?: (fraction: number) => void,
                                  only?: number[]): Promise<Rendered> {
  const times = only ?? renderTimes(doc, what);
  const [width, height] = FRAME_SIZE[doc.settings.aspect_ratio];
  useScene.getState().select(null); // no gizmo in the shot
  const { render_id: rid } = await post(`/projects/${projectId}/renders`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ version, passes, fps: doc.timeline.fps, frames: times.length, width, height,
                           ...(what === "stills" ? { times } : {}) }),
  });
  const pending: Record<string, { start: number; blobs: Blob[] }> = {};
  const flush = async (pass: Pass) => {
    const batch = pending[pass];
    if (!batch?.blobs.length) return;
    const form = new FormData();
    batch.blobs.forEach((b, i) => form.append("files", b, `${batch.start + i}.${b.type === "image/png" ? "png" : "jpg"}`));
    await post(`/projects/${projectId}/renders/${rid}/frames?pass=${pass}&start=${batch.start}`, { method: "POST", body: form });
    pending[pass] = { start: batch.start + batch.blobs.length, blobs: [] };
  };
  let done = 0;
  await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r))); // let the gizmo go
  await drawFrames(handle, doc, times, passes, async (pass, index, frame) => {
    pending[pass] ??= { start: index, blobs: [] };
    pending[pass].blobs.push(frame);
    if (pending[pass].blobs.length >= BATCH) await flush(pass);
    onProgress?.(++done / (times.length * passes.length));
  });
  for (const pass of passes) await flush(pass);
  const out = await post(`/projects/${projectId}/renders/${rid}/finish`, { method: "POST" });
  const results = out as { assets: Record<Pass, Asset>; stills: Record<Pass, Asset[]> };
  return [...Object.entries(results.assets), ...Object.entries(results.stills).flatMap(([p, xs]) => xs.map((a) => [p, a] as const))]
    .map(([pass, asset]) => ({ pass: pass as Pass, asset }));
}

/** Render the shot camera's view into passes: a video of the timeline, or stills at the camera's keys. */
export function RenderPanel({ doc, handleRef, projectId }: {
  doc: SceneDoc; handleRef: MutableRefObject<ViewportHandle | null>; projectId: string;
}) {
  const { data: project } = useSceneProject(projectId);
  const [passes, setPasses] = useState<Pass[]>(["beauty", "depth"]);
  const [what, setWhat] = useState<"film" | "stills">("film");
  const [progress, setProgress] = useState<number | null>(null);
  const [made, setMade] = useState<Rendered | null>(null);
  const times = renderTimes(doc, what);

  async function render() {
    if (!handleRef.current || !project) return;
    setMade(null);
    setProgress(0);
    try {
      setMade(await renderScene(handleRef.current, doc, projectId, project.version, passes, what, setProgress));
      toast(`Rendered ${passes.length} pass${passes.length > 1 ? "es" : ""}`);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "The render failed");
    } finally {
      setProgress(null);
    }
  }

  const toggle = (p: Pass) => setPasses((cur) => (cur.includes(p) ? cur.filter((x) => x !== p) : [...cur, p]));
  return (
    <section className="flex flex-col gap-3 border-t border-line-soft pt-4">
      <h3 className="text-xs text-muted">Render through the shot camera</h3>
      <Field label="What">
        <Segmented label="What" value={what} options={["film", "stills"] as const} onChange={setWhat}
                   render={(w) => (w === "film" ? "The timeline" : "Stills at camera keys")} />
      </Field>
      <div className="flex flex-wrap gap-1.5" role="group" aria-label="Passes">
        {(["beauty", "depth", "normal"] as const).map((p) => (
          <button key={p} type="button" aria-pressed={passes.includes(p)} onClick={() => toggle(p)}
                  className={cn("rounded-full border px-2.5 py-1 text-xs",
                                passes.includes(p) ? "border-tungsten/60 bg-raised text-chalk" : "border-line-soft text-muted hover:text-chalk")}>
            {PASS_LABEL[p]}
          </button>
        ))}
      </div>
      <p className="text-2xs text-faint">
        {times.length} frame{times.length > 1 ? "s" : ""} of {FRAME_SIZE[doc.settings.aspect_ratio].join(" × ")}
        {what === "film" ? ` at ${doc.timeline.fps} fps` : ""}. Depth is white near the camera, as depth-guided video models expect.
      </p>
      <Button tone="primary" size="sm" onClick={() => void render()} disabled={progress !== null || !passes.length || !doc.active_camera_id}>
        {progress !== null ? <><LoaderCircle className="size-4 animate-spin" aria-hidden /> {Math.round(progress * 100)}%</> : "Render"}
      </Button>
      {made && (
        <ul className="grid grid-cols-2 gap-2">
          {made.map(({ pass, asset: a }) => (
            <li key={a.id} className="flex flex-col gap-1">
              {a.kind === "video"
                ? <video src={mediaUrl(a.url)} muted loop autoPlay playsInline className="aspect-video w-full rounded-[3px] bg-black object-contain" />
                // eslint-disable-next-line @next/next/no-img-element
                : <img src={mediaUrl(a.url)} alt={`${PASS_LABEL[pass]} still`} className="aspect-video w-full rounded-[3px] bg-black object-contain" />}
              <Link href={`/library?asset=${a.id}`} className="text-2xs text-muted hover:text-chalk">
                {PASS_LABEL[pass]}{typeof a.metadata?.time === "number" ? ` at ${a.metadata.time} s` : ""}
              </Link>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
