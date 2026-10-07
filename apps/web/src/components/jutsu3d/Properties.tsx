"use client";

import { Copy, Trash2 } from "lucide-react";
import { type MutableRefObject, useState } from "react";
import {
  duplicateObject, JOINTS, KIND_LABEL, LIGHTS, patchObject, POSES, removeObject, type SceneDoc, type SceneObject,
  useScene, type Vec3,
} from "@/lib/jutsu3d";
import { Button, Field, Segmented, Slider } from "@/components/ui/controls";
import { MakeReal } from "./MakeReal";
import { RenderPanel } from "./RenderPanel";
import type { ViewportHandle } from "./Viewport";

const DEG = 180 / Math.PI;
const LENSES = [14, 18, 24, 28, 35, 50, 65, 85, 100, 135, 200];

/** A number that commits on Enter or when focus leaves, so typing is one undo step. */
function NumberBox({ label, value, step = 0.1, onCommit }: {
  label: string; value: number; step?: number; onCommit: (v: number) => void;
}) {
  const [draft, setDraft] = useState<string | null>(null);
  const commit = () => {
    if (draft === null) return;
    const v = Number(draft);
    if (Number.isFinite(v) && v !== value) onCommit(v);
    setDraft(null);
  };
  return (
    <label className="flex min-w-0 items-center gap-1 text-2xs text-faint">
      {label}
      <input type="number" step={step} aria-label={label} value={draft ?? String(Math.round(value * 1000) / 1000)}
             onChange={(e) => setDraft(e.target.value)} onBlur={commit} onKeyDown={(e) => e.key === "Enter" && commit()}
             className="tabular h-7 w-full min-w-0 rounded-[4px] border border-line-soft bg-bay px-1.5 text-xs text-chalk outline-none focus:border-tungsten/60" />
    </label>
  );
}

function VecRow({ label, value, step, scale = 1, onCommit }: {
  label: string; value: Vec3; step?: number; scale?: number; onCommit: (v: Vec3) => void;
}) {
  return (
    <div className="flex flex-col gap-1">
      <span className="text-2xs text-muted">{label}</span>
      <div className="grid grid-cols-3 gap-1.5">
        {(["X", "Y", "Z"] as const).map((axis, i) => (
          <NumberBox key={axis} label={`${label} ${axis}`} step={step} value={value[i] * scale}
                     onCommit={(v) => onCommit(value.map((x, j) => (j === i ? v / scale : x)) as Vec3)} />
        ))}
      </div>
    </div>
  );
}

export function Properties({ doc, object, onPose, handleRef, projectId }: {
  doc: SceneDoc; object: SceneObject | null; onPose: (id: string, patch: Partial<SceneObject>) => void;
  handleRef: MutableRefObject<ViewportHandle | null>; projectId: string;
}) {
  if (!object) return <SceneSettings doc={doc} handleRef={handleRef} projectId={projectId} />;
  const o = object;
  const set = (patch: Partial<SceneObject>) => onPose(o.id, patch);
  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center gap-2">
        <input value={o.name} onChange={(e) => patchObject(o.id, { name: e.target.value })} aria-label="Name"
               className="type-title min-w-0 flex-1 bg-transparent text-base outline-none" />
        <Button size="sm" tone="ghost" aria-label="Duplicate" onClick={() => duplicateObject(o.id)}><Copy className="size-4" aria-hidden /></Button>
        <Button size="sm" tone="ghost" aria-label="Delete" onClick={() => removeObject(o.id)}><Trash2 className="size-4" aria-hidden /></Button>
      </div>
      <p className="-mt-3 text-2xs text-faint">{KIND_LABEL[o.kind]}</p>
      <VecRow label="Position" value={o.transform.position} onCommit={(position) => set({ transform: { ...o.transform, position } })} />
      {o.kind !== "camera" && !LIGHTS.includes(o.kind) && (
        <>
          <VecRow label="Rotation" value={o.transform.rotation} step={5} scale={DEG}
                  onCommit={(rotation) => set({ transform: { ...o.transform, rotation } })} />
          <VecRow label="Scale" value={o.transform.scale} onCommit={(scale) => set({ transform: { ...o.transform, scale } })} />
        </>
      )}
      {o.kind !== "model" && (
        <Field label={LIGHTS.includes(o.kind) ? "Light color" : "Color"} htmlFor="obj-color">
          <input id="obj-color" type="color" value={o.color} onChange={(e) => patchObject(o.id, { color: e.target.value })}
                 className="h-8 w-16 cursor-pointer rounded-[4px] border border-line-soft bg-bay" />
        </Field>
      )}
      {LIGHTS.includes(o.kind) && (
        <Field label="Strength">
          <Slider value={o.intensity} min={0} max={o.kind === "sun" ? 10 : 100} step={0.1} onChange={(intensity) => patchObject(o.id, { intensity })} />
        </Field>
      )}
      {(o.kind === "camera" || o.kind === "spot_light") && (
        <VecRow label="Looks at" value={o.target} onCommit={(target) => set({ target })} />
      )}
      {o.kind === "camera" && (
        <>
          <Field label="Focal length">
            <Segmented label="Focal length" value={LENSES.reduce((a, b) => (Math.abs(b - o.focal_length) < Math.abs(a - o.focal_length) ? b : a))}
                       options={LENSES} render={(f) => `${f}`} onChange={(focal_length) => set({ focal_length })} />
          </Field>
          {doc.active_camera_id !== o.id ? (
            <Button size="sm" onClick={() => useScene.getState().change((d) => ({ ...d, active_camera_id: o.id }))}>
              Shoot through this camera
            </Button>
          ) : <p className="text-2xs text-tungsten">Renders look through this camera.</p>}
        </>
      )}
      {o.kind === "mannequin" && <PoseEditor o={o} />}
    </div>
  );
}

function PoseEditor({ o }: { o: SceneObject }) {
  const [joint, setJoint] = useState<(typeof JOINTS)[number]>("shoulder_r");
  const value = o.pose[joint] ?? [0, 0, 0];
  return (
    <div className="flex flex-col gap-3">
      <Field label="Pose">
        <div className="flex flex-wrap gap-1.5">
          {Object.entries(POSES).map(([key, p]) => (
            <button key={key} type="button" onClick={() => patchObject(o.id, { pose: p.pose })}
                    className="rounded-full border border-line-soft px-2.5 py-1 text-xs text-muted hover:border-tungsten/50 hover:text-chalk">
              {p.label}
            </button>
          ))}
        </div>
      </Field>
      <Field label="Bend a joint" htmlFor="joint">
        <select id="joint" value={joint} onChange={(e) => setJoint(e.target.value as (typeof JOINTS)[number])}
                className="h-8 rounded-[var(--radius-control)] border border-line-soft bg-bay px-2 text-xs text-chalk outline-none">
          {JOINTS.map((j) => <option key={j} value={j}>{j.replace("_l", " (left)").replace("_r", " (right)").replace("_", " ")}</option>)}
        </select>
      </Field>
      {(["Forward / back", "Twist", "Out / in"] as const).map((axis, i) => (
        <Field key={axis} label={axis}>
          <Slider value={Math.round(value[i] * DEG)} min={-180} max={180} step={1} format={(v) => `${v}°`}
                  onChange={(deg) => patchObject(o.id, { pose: { ...o.pose, [joint]: value.map((x, k) => (k === i ? deg / DEG : x)) as Vec3 } })} />
        </Field>
      ))}
    </div>
  );
}

function SceneSettings({ doc, handleRef, projectId }: { doc: SceneDoc; handleRef: MutableRefObject<ViewportHandle | null>; projectId: string }) {
  const change = useScene((s) => s.change);
  const s = doc.settings;
  return (
    <div className="flex flex-col gap-4">
      <h2 className="type-title text-base">Scene</h2>
      <Field label="Shot shape">
        <Segmented label="Shot shape" value={s.aspect_ratio} options={["16:9", "9:16", "1:1", "21:9", "4:3"] as const}
                   onChange={(aspect_ratio) => change((d) => ({ ...d, settings: { ...d.settings, aspect_ratio } }))} />
      </Field>
      <Field label="Background" htmlFor="scene-bg">
        <input id="scene-bg" type="color" value={s.background}
               onChange={(e) => change((d) => ({ ...d, settings: { ...d.settings, background: e.target.value } }))}
               className="h-8 w-16 cursor-pointer rounded-[4px] border border-line-soft bg-bay" />
      </Field>
      <Field label="Fill light">
        <Slider value={s.ambient} min={0} max={3} step={0.05} onChange={(ambient) => change((d) => ({ ...d, settings: { ...d.settings, ambient } }))} />
      </Field>
      <Field label="Length">
        <Slider value={doc.timeline.duration} min={1} max={60} step={1} format={(v) => `${v} s`}
                onChange={(duration) => change((d) => ({ ...d, timeline: { ...d.timeline, duration } }))} />
      </Field>
      <RenderPanel doc={doc} handleRef={handleRef} projectId={projectId} />
      <MakeReal doc={doc} handleRef={handleRef} projectId={projectId} />
    </div>
  );
}
