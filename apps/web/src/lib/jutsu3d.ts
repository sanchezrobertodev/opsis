"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { temporal } from "zundo";
import { create } from "zustand";
import { api, unwrap } from "./api";

/** Mirrors studio/jutsu3d/document.py. Metres, XYZ Euler radians, y up. */
export type Vec3 = [number, number, number];
export type ObjectKind = "box" | "sphere" | "cylinder" | "cone" | "plane" | "mannequin" | "model" | "world" | "sun"
  | "point_light" | "spot_light" | "camera";
export type Transform = { position: Vec3; rotation: Vec3; scale: Vec3 };
export type SceneObject = {
  id: string; kind: ObjectKind; name: string; transform: Transform; color: string; visible: boolean;
  asset_id: string | null; collider_asset_id?: string | null; pose: Record<string, Vec3>; intensity: number;
  target: Vec3; focal_length: number; animation?: Animation | null;
};
/** A rigged model playing a clip (by name) from its own GLB or another on the same skeleton, on the timeline. */
export type Animation = {
  clip: string; source_asset_id?: string | null; start: number; offset: number; speed: number; loop: boolean;
};
export type Keyframe = { t: number; position?: Vec3 | null; rotation?: Vec3 | null; scale?: Vec3 | null;
                         target?: Vec3 | null; focal_length?: number | null };
export type Track = { object_id: string; keys: Keyframe[] };
export type SceneDoc = {
  objects: SceneObject[];
  active_camera_id: string | null;
  timeline: { duration: number; fps: number; tracks: Track[] };
  settings: { background: string; aspect_ratio: "16:9" | "9:16" | "1:1" | "21:9" | "4:3"; ambient: number };
  cover_asset_id: string | null;
};

export const JOINTS = ["spine", "neck", "head", "shoulder_l", "elbow_l", "shoulder_r", "elbow_r", "hip_l", "knee_l",
                       "hip_r", "knee_r"] as const;
export const LIGHTS: ObjectKind[] = ["sun", "point_light", "spot_light"];
export const KIND_LABEL: Record<ObjectKind, string> = {
  box: "Box", sphere: "Sphere", cylinder: "Cylinder", cone: "Cone", plane: "Plane", mannequin: "Figure",
  model: "Model", world: "World", sun: "Sun", point_light: "Point light", spot_light: "Spot light", camera: "Camera",
};

export function newId(prefix = "o") {
  const bytes = crypto.getRandomValues(new Uint8Array(3));
  return prefix + Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}

export function newObject(kind: ObjectKind, partial: Partial<SceneObject> = {}): SceneObject {
  const lift = kind === "plane" || kind === "mannequin" || kind === "model" || kind === "world" ? 0 : kind === "camera" ? 1.6
    : LIGHTS.includes(kind) ? 3 : 0.5;
  return {
    id: newId(kind === "camera" ? "c" : LIGHTS.includes(kind) ? "l" : "o"), kind, name: KIND_LABEL[kind],
    transform: { position: [0, lift, kind === "camera" ? 5 : 0], rotation: [0, 0, 0], scale: [1, 1, 1] },
    color: LIGHTS.includes(kind) ? "#fff4e0" : kind === "mannequin" ? "#d9cbb8" : "#b8b8b8", visible: true,
    asset_id: null, pose: {}, intensity: kind === "sun" ? 2.5 : kind === "camera" ? 1 : 20, target: [0, 1, 0],
    focal_length: 35, ...partial,
  };
}

/** Vertical field of view for a focal length on a full-frame sensor (24 mm tall). */
export function fovFor(focal: number) {
  return (2 * Math.atan(24 / (2 * focal)) * 180) / Math.PI;
}

// ---------------------------------------------------------------- the editing store (undo with zundo)

type EditorState = {
  doc: SceneDoc | null;
  selected: string | null;
  /** A fresh document from the server: replaces the scene and forgets the undo history. */
  load: (doc: SceneDoc) => void;
  /** One undoable edit. */
  change: (fn: (d: SceneDoc) => SceneDoc) => void;
  select: (id: string | null) => void;
};

export const useScene = create<EditorState>()(temporal((set) => ({
  doc: null,
  selected: null,
  load: (doc) => set({ doc, selected: null }),
  change: (fn) => set((s) => (s.doc ? { doc: fn(s.doc) } : s)),
  select: (selected) => set({ selected }),
}), { partialize: (s) => ({ doc: s.doc }), equality: (a, b) => a.doc === b.doc, limit: 200 }));

export function loadScene(doc: SceneDoc) {
  useScene.getState().load(doc);
  useScene.temporal.getState().clear();
}

export const undo = () => useScene.temporal.getState().undo();
export const redo = () => useScene.temporal.getState().redo();

export function patchObject(id: string, patch: Partial<SceneObject>) {
  useScene.getState().change((d) => ({ ...d, objects: d.objects.map((o) => (o.id === id ? { ...o, ...patch } : o)) }));
}

export function addObject(obj: SceneObject) {
  useScene.getState().change((d) => ({ ...d, objects: [...d.objects, obj],
    active_camera_id: d.active_camera_id ?? (obj.kind === "camera" ? obj.id : null) }));
  useScene.getState().select(obj.id);
}

export function removeObject(id: string) {
  useScene.getState().change((d) => {
    const objects = d.objects.filter((o) => o.id !== id);
    return { ...d, objects, timeline: { ...d.timeline, tracks: d.timeline.tracks.filter((t) => t.object_id !== id) },
             active_camera_id: d.active_camera_id === id ? objects.find((o) => o.kind === "camera")?.id ?? null
               : d.active_camera_id };
  });
  useScene.getState().select(null);
}

export function duplicateObject(id: string) {
  const o = useScene.getState().doc?.objects.find((x) => x.id === id);
  if (!o) return;
  const [x, y, z] = o.transform.position;
  addObject({ ...structuredClone(o), id: newId(o.id[0]), name: `${o.name} copy`,
              transform: { ...o.transform, position: [x + 0.5, y, z + 0.5] } });
}

// ---------------------------------------------------------------- API

const sceneKey = (id: string) => ["project", id] as const;

export function useScenes() {
  return useQuery({ queryKey: ["projects", "jutsu3d"],
                    queryFn: () => unwrap(api.GET("/projects", { params: { query: { kind: "jutsu3d" } } })) });
}

export function useSceneProject(id: string) {
  return useQuery({
    queryKey: sceneKey(id),
    queryFn: () => unwrap(api.GET("/projects/{project_id}", { params: { path: { project_id: id } } })),
    staleTime: Infinity,
  });
}

export function useCreateScene() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (title: string) => unwrap(api.POST("/projects", { body: { kind: "jutsu3d", title, document: {} } })),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["projects"] }),
  });
}

export function useSaveScene(id: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { version: number; title?: string; document: SceneDoc }) =>
      unwrap(api.PUT("/projects/{project_id}", { params: { path: { project_id: id } },
                                                 body: { note: "", ...body, document: body.document as never } })),
    onSuccess: (p) => {
      qc.setQueryData(sceneKey(id), p);
      qc.invalidateQueries({ queryKey: ["projects"] });
    },
  });
}

// ---------------------------------------------------------------- the timeline

type Keyed = "position" | "rotation" | "scale" | "target" | "focal_length";

function sample<T extends Vec3 | number>(keys: Keyframe[], field: Keyed, t: number): T | undefined {
  const defined = keys.filter((k) => k[field] != null);
  if (!defined.length) return undefined;
  if (t <= defined[0].t) return defined[0][field] as T;
  const last = defined[defined.length - 1];
  if (t >= last.t) return last[field] as T;
  const i = defined.findIndex((k) => k.t > t);
  const a = defined[i - 1], b = defined[i];
  const u = (t - a.t) / Math.max(1e-6, b.t - a.t);
  const s = u * u * (3 - 2 * u); // ease in and out between keys
  const va = a[field] as T, vb = b[field] as T;
  if (typeof va === "number") return (va + ((vb as number) - va) * s) as T;
  return (va as Vec3).map((x, j) => x + ((vb as Vec3)[j] - x) * s) as T;
}

/** An object as it is at time `t`: its keyframed values on top of its resting state. */
export function evaluate(o: SceneObject, track: Track | undefined, t: number | null): SceneObject {
  if (t == null || !track?.keys.length) return o;
  return {
    ...o,
    transform: {
      position: sample<Vec3>(track.keys, "position", t) ?? o.transform.position,
      rotation: sample<Vec3>(track.keys, "rotation", t) ?? o.transform.rotation,
      scale: sample<Vec3>(track.keys, "scale", t) ?? o.transform.scale,
    },
    target: sample<Vec3>(track.keys, "target", t) ?? o.target,
    focal_length: sample<number>(track.keys, "focal_length", t) ?? o.focal_length,
  };
}

/** Record an object's state at `t` as a key on its track (replacing a key within half a frame). */
export function keyObject(d: SceneDoc, o: SceneObject, t: number): SceneDoc {
  const key: Keyframe = { t: Math.round(t * 1000) / 1000, position: o.transform.position, rotation: o.transform.rotation,
                          scale: o.transform.scale, ...(o.kind === "camera" || o.kind === "spot_light"
                            ? { target: o.target, focal_length: o.kind === "camera" ? o.focal_length : null } : {}) };
  const near = 0.5 / d.timeline.fps;
  const tracks = d.timeline.tracks.some((tr) => tr.object_id === o.id) ? d.timeline.tracks
    : [...d.timeline.tracks, { object_id: o.id, keys: [] }];
  return { ...d, timeline: { ...d.timeline, tracks: tracks.map((tr) => tr.object_id !== o.id ? tr : {
    ...tr, keys: [...tr.keys.filter((k) => Math.abs(k.t - key.t) > near), key].sort((a, b) => a.t - b.t) }) } };
}

export function trackOf(d: SceneDoc, id: string): Track | undefined {
  return d.timeline.tracks.find((t) => t.object_id === id);
}

// ---------------------------------------------------------------- figure poses

export const POSES: Record<string, { label: string; pose: Record<string, Vec3> }> = {
  stand: { label: "Stand", pose: {} },
  walk: { label: "Walk", pose: { hip_l: [-0.45, 0, 0], knee_l: [0.15, 0, 0], hip_r: [0.35, 0, 0], knee_r: [0.55, 0, 0],
                                 shoulder_l: [0.35, 0, 0.06], shoulder_r: [-0.35, 0, -0.06], elbow_l: [-0.2, 0, 0],
                                 elbow_r: [-0.45, 0, 0] } },
  run: { label: "Run", pose: { spine: [0.25, 0, 0], hip_l: [-1.0, 0, 0], knee_l: [0.6, 0, 0], hip_r: [0.6, 0, 0],
                               knee_r: [1.3, 0, 0], shoulder_l: [0.8, 0, 0.1], shoulder_r: [-0.9, 0, -0.1],
                               elbow_l: [-1.2, 0, 0], elbow_r: [-1.4, 0, 0] } },
  sit: { label: "Sit", pose: { hip_l: [-1.55, 0, 0.08], knee_l: [1.55, 0, 0], hip_r: [-1.55, 0, -0.08], knee_r: [1.55, 0, 0],
                               shoulder_l: [-0.25, 0, 0.05], shoulder_r: [-0.25, 0, -0.05], elbow_l: [-0.6, 0, 0],
                               elbow_r: [-0.6, 0, 0] } },
  point: { label: "Point", pose: { shoulder_r: [-1.5, 0, -0.1], head: [0, -0.3, 0] } },
  arms_up: { label: "Arms up", pose: { shoulder_l: [0, 0, 2.8], shoulder_r: [0, 0, -2.8], head: [-0.25, 0, 0] } },
  wave: { label: "Wave", pose: { shoulder_r: [0, 0, -2.3], elbow_r: [0, 0, -0.9], head: [0, -0.2, 0.1] } },
  t_pose: { label: "T-pose", pose: { shoulder_l: [0, 0, 1.57], shoulder_r: [0, 0, -1.57] } },
};

/** Where a clip is at timeline time `t`: its first pose (from `offset`) until `start`, then playing at `speed`,
 *  looping or holding its last pose. */
export function clipTime(a: Animation, t: number, length: number): number {
  if (length <= 0) return 0;
  const local = a.offset + Math.max(0, t - a.start) * a.speed;
  return a.loop ? local % length : Math.min(local, length);
}

/** LTX-2.3 render to real takes about 6 s at 720p or 15 s at 480p (at 24 fps; the budget is frames x size). */
const REAL_FRAMES = { "720p": 145, "480p": 361 } as const;

/** What render-to-real is asked for to cover a render of `seconds` at `fps`: 720p while it fits, else 480p, and as
 *  many frames as the render has, up to that size's budget (LTX makes 8k+1 frames). `cut` is true when the render is
 *  longer than the model can make. */
export function realParams(seconds: number, fps: number) {
  const want = Math.round(seconds * fps) + 1;
  const resolution: keyof typeof REAL_FRAMES = want <= REAL_FRAMES["720p"] ? "720p" : "480p";
  const frames = Math.max(9, Math.floor((Math.min(want, REAL_FRAMES[resolution]) - 1) / 8) * 8 + 1);
  return { params: { resolution, num_frames: frames, frames_per_second: fps }, cut: want > REAL_FRAMES[resolution] };
}
