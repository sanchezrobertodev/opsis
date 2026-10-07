import type { ModelSummary } from "./api";

/** The creative studios that share the Create form. Each picks the model modes it shows by their ops. */
export type StudioKind = "video" | "image" | "edit" | "motion";

export type EditOp = {
  op: string;
  label: string;
  summary: string;
};

/** Edit Video operations, in the order the picker shows them. Ops with no model in the registry are hidden. */
export const EDIT_OPS: EditOp[] = [
  { op: "instruction_edit", label: "Change with words", summary: "Describe the change: add snow, swap the jacket, make it night." },
  { op: "restyle", label: "Restyle", summary: "Keep the motion and timing, give it a whole new look." },
  { op: "relight", label: "Relight", summary: "New light and grade on the same footage." },
  { op: "extend", label: "Extend", summary: "Keep the clip going from where it ends." },
  { op: "reframe", label: "Reframe", summary: "Recompose for another shape, like vertical for shorts." },
  { op: "upscale", label: "Upscale", summary: "More resolution and detail." },
  { op: "interpolate", label: "Smooth motion", summary: "A higher frame rate, or slow motion without stutter." },
  { op: "bg_remove", label: "Remove background", summary: "Cut the subject out of its surroundings." },
  { op: "add_sound", label: "Add sound", summary: "Sound effects and ambience that match the picture." },
  { op: "stabilize", label: "Stabilize", summary: "Steady shaky footage." },
  { op: "trim", label: "Trim", summary: "Keep just the part you want." },
  { op: "retime", label: "Change speed", summary: "Speed up or slow down, sound included." },
  { op: "reverse", label: "Reverse", summary: "Play it backwards." },
  { op: "loop", label: "Loop", summary: "Repeat it, or play it forward and back." },
];

export const KINDS: Record<StudioKind, { output: "video" | "image"; ops: string[]; preferred: string }> = {
  video: { output: "video", ops: ["t2v", "i2v", "flf2v", "multi_ref", "multi_shot"], preferred: "veo-3.1" },
  image: { output: "image", ops: ["t2i", "i2i", "multi_ref"], preferred: "nano-banana-2" },
  edit: { output: "video", ops: EDIT_OPS.map((o) => o.op), preferred: "video-tools" },
  motion: { output: "video", ops: ["motion_transfer"], preferred: "kling-3.0-motion-control" },
};

export function modeMatches(mode: { ops: string[] }, ops: string[]) {
  return mode.ops.some((o) => ops.includes(o));
}

/** Models with at least one mode doing one of `ops`, for this studio's output type. */
export function modelsFor(models: ModelSummary[], kind: StudioKind, ops: string[] = KINDS[kind].ops) {
  return models.filter(
    (m) => m.output_type === KINDS[kind].output && m.status !== "shutdown" && m.modes.some((mode) => modeMatches(mode, ops)),
  );
}
