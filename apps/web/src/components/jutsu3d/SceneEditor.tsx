"use client";

import { Box, Camera, Eye, EyeOff, Globe, History, Pause, Play, Redo2, Undo2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { useStore } from "zustand";
import { ApiError } from "@/lib/api";
import { cn } from "@/lib/cn";
import {
  addObject, duplicateObject, evaluate, KIND_LABEL, keyObject, loadScene, newObject, type ObjectKind, patchObject, redo,
  removeObject, type SceneDoc, type SceneObject, trackOf, type Transform, undo, useSaveScene, useScene, useSceneProject,
} from "@/lib/jutsu3d";
import { Button, Segmented } from "@/components/ui/controls";
import { HistoryDialog } from "@/components/cinema/Editor";
import { AddModel, AddWorld } from "./AddModel";
import { Properties } from "./Properties";
import { Timeline } from "./Timeline";
import { type GizmoMode, Viewport, type ViewportHandle } from "./Viewport";

const SAVE_AFTER_MS = 1200;
const ADD: ObjectKind[] = ["box", "sphere", "cylinder", "cone", "plane", "mannequin", "sun", "point_light", "spot_light", "camera"];
const ASPECT: Record<SceneDoc["settings"]["aspect_ratio"], string> = {
  "16:9": "16 / 9", "9:16": "9 / 16", "1:1": "1 / 1", "21:9": "21 / 9", "4:3": "4 / 3",
};

/** The 3D blocking editor: a scene of primitives, figures, lights and cameras, keyframed over time. */
export function SceneEditor({ id }: { id: string }) {
  const { data: project, isLoading, refetch } = useSceneProject(id);
  const save = useSaveScene(id);
  const doc = useScene((s) => s.doc);
  const selected = useScene((s) => s.selected);
  const select = useScene((s) => s.select);
  const canUndo = useStore(useScene.temporal, (s) => s.pastStates.length > 0);
  const canRedo = useStore(useScene.temporal, (s) => s.futureStates.length > 0);
  const [title, setTitle] = useState<string | null>(null);
  const [mode, setMode] = useState<GizmoMode>("translate");
  const [look, setLook] = useState<"free" | "camera">("free");
  const [time, setTime] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [history, setHistory] = useState(false);
  const [adding, setAdding] = useState<"model" | "world" | null>(null);
  const loadedFor = useRef<string | null>(null);
  const saved = useRef<SceneDoc | null>(null);
  const handle = useRef<ViewportHandle | null>(null);

  // the server's document becomes the scene once per open; after that the editor owns it
  useEffect(() => {
    if (project && loadedFor.current !== project.id) {
      loadedFor.current = project.id;
      saved.current = project.document as SceneDoc;
      loadScene(project.document as SceneDoc);
    }
  }, [project]);

  const dirty = Boolean(doc && saved.current && doc !== saved.current) || (title !== null && title !== project?.title);
  useEffect(() => {
    if (!dirty || !project || !doc || save.isPending) return;
    const timer = setTimeout(() => {
      const sent = doc;
      save.mutate({ version: project.version, document: sent, title: title || project.title }, {
        onSuccess: () => { saved.current = sent; },
        onError: (err) => {
          if (err instanceof ApiError && err.status === 409) {
            toast.error("This scene was saved somewhere else. Reloaded the latest version.");
            loadedFor.current = null;
            void refetch();
          } else {
            toast.error(err instanceof Error ? err.message : "Couldn't save the scene");
          }
        },
      });
    }, SAVE_AFTER_MS);
    return () => clearTimeout(timer);
  }, [dirty, doc, title, project, save, refetch]);

  // playback
  useEffect(() => {
    if (!playing || !doc) return;
    let last = performance.now();
    let raf = requestAnimationFrame(function tick(now) {
      setTime((t) => { const next = t + (now - last) / 1000; return next >= doc.timeline.duration ? 0 : next; });
      last = now;
      raf = requestAnimationFrame(tick);
    });
    return () => cancelAnimationFrame(raf);
  }, [playing, doc]);

  // keyboard shortcuts
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      const el = e.target as HTMLElement | null;
      if (el && (el.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName))) return;
      const ctrl = e.ctrlKey || e.metaKey;
      const sel = useScene.getState().selected;
      if (ctrl && e.key.toLowerCase() === "z") { e.preventDefault(); if (e.shiftKey) redo(); else undo(); return; }
      if (ctrl && e.key.toLowerCase() === "y") { e.preventDefault(); redo(); return; }
      if (ctrl && e.key.toLowerCase() === "d" && sel) { e.preventDefault(); duplicateObject(sel); return; }
      if (ctrl) return;
      const k = e.key.toLowerCase();
      if (k === "w") setMode("translate");
      else if (k === "e") setMode("rotate");
      else if (k === "r") setMode("scale");
      else if (k === "c") setLook((v) => (v === "free" ? "camera" : "free"));
      else if (k === " ") { e.preventDefault(); setPlaying((p) => !p); }
      else if (k === "k" && sel) keyAt(sel);
      else if ((k === "delete" || k === "backspace") && sel) removeObject(sel);
      else if (k === "escape") useScene.getState().select(null);
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  if (isLoading) return null;
  if (!project) return <div className="p-6 text-sm text-muted">This scene doesn&rsquo;t exist or was removed.</div>;
  if (!doc) return null;

  /** Edits to a keyframed object land on its track at the playhead; others change its resting state. */
  function setPose(objId: string, patch: Partial<SceneObject>) {
    const d = useScene.getState().doc;
    const o = d?.objects.find((x) => x.id === objId);
    if (!d || !o) return;
    const track = trackOf(d, objId);
    const keyed = Boolean(track?.keys.length) && ("transform" in patch || "target" in patch || "focal_length" in patch);
    if (keyed) {
      useScene.getState().change((cur) => keyObject(cur, { ...evaluate(o, track, time), ...patch }, time));
    } else {
      patchObject(objId, patch);
    }
  }
  function keyAt(objId: string) {
    const d = useScene.getState().doc;
    const o = d?.objects.find((x) => x.id === objId);
    if (d && o) useScene.getState().change((cur) => keyObject(cur, evaluate(o, trackOf(cur, objId), time), time));
  }

  const status = save.isPending ? "Saving…" : dirty ? "Unsaved changes" : `Saved, version ${project.version}`;
  const current = doc.objects.find((o) => o.id === selected) ?? null;
  return (
    <div className="flex h-full flex-col">
      <div className="flex flex-wrap items-center gap-3 border-b border-line-soft px-5 py-2.5">
        <input value={title ?? project.title} onChange={(e) => setTitle(e.target.value)} aria-label="Scene title"
               className="type-title min-w-0 flex-1 bg-transparent text-lg outline-none" />
        <span className={cn("tabular text-2xs", dirty ? "text-tungsten" : "text-faint")} aria-live="polite">{status}</span>
        <Button size="sm" tone="ghost" aria-label="Undo" onClick={undo} disabled={!canUndo}><Undo2 className="size-4" aria-hidden /></Button>
        <Button size="sm" tone="ghost" aria-label="Redo" onClick={redo} disabled={!canRedo}><Redo2 className="size-4" aria-hidden /></Button>
        <div className="w-60">
          <Segmented label="Gizmo" value={mode} options={["translate", "rotate", "scale"] as const} onChange={setMode}
                     render={(m) => (m === "translate" ? "Move" : m === "rotate" ? "Rotate" : "Scale")} />
        </div>
        <Button size="sm" tone={look === "camera" ? "primary" : "ghost"} onClick={() => setLook((v) => (v === "free" ? "camera" : "free"))}
                aria-pressed={look === "camera"} disabled={!doc.active_camera_id}>
          <Camera className="size-4" aria-hidden /> Through the camera
        </Button>
        <Button size="sm" tone="ghost" onClick={() => setHistory(true)}><History className="size-4" aria-hidden /> History</Button>
      </div>

      <div className="grid min-h-0 flex-1 grid-cols-1 lg:grid-cols-[210px_minmax(0,1fr)_300px]">
        <aside className="flex min-h-0 flex-col gap-4 overflow-y-auto border-b border-line-soft p-4 scrollbar-quiet lg:border-b-0 lg:border-r">
          <section aria-label="Add" className="grid grid-cols-2 gap-1.5">
            {ADD.map((k) => (
              <button key={k} type="button" onClick={() => addObject(newObject(k))}
                      className="rounded-[var(--radius-control)] border border-line-soft px-2 py-1.5 text-left text-xs text-muted hover:border-tungsten/50 hover:text-chalk">
                + {KIND_LABEL[k]}
              </button>
            ))}
            <button type="button" onClick={() => setAdding("model")}
                    className="flex items-center gap-1.5 rounded-[var(--radius-control)] border border-line-soft px-2 py-1.5 text-left text-xs text-muted hover:border-tungsten/50 hover:text-chalk">
              <Box className="size-3.5" aria-hidden /> Model…
            </button>
            <button type="button" onClick={() => setAdding("world")}
                    className="flex items-center gap-1.5 rounded-[var(--radius-control)] border border-line-soft px-2 py-1.5 text-left text-xs text-muted hover:border-tungsten/50 hover:text-chalk">
              <Globe className="size-3.5" aria-hidden /> World…
            </button>
          </section>
          <section aria-label="Outliner" className="flex flex-col gap-0.5">
            <h2 className="mb-1 text-2xs text-faint">In the scene</h2>
            {doc.objects.map((o) => (
              <div key={o.id} className={cn("group flex items-center gap-1 rounded-[4px] px-1.5",
                                            o.id === selected ? "bg-raised text-chalk" : "text-muted hover:bg-raised/60")}>
                <button type="button" onClick={() => select(o.id)} aria-pressed={o.id === selected}
                        className="min-w-0 flex-1 truncate py-1 text-left text-xs">
                  {o.name || KIND_LABEL[o.kind]}
                  {o.id === doc.active_camera_id && <span className="ml-1 text-2xs text-tungsten">shot</span>}
                  {trackOf(doc, o.id)?.keys.length ? <span className="ml-1 text-2xs text-faint">◆</span> : null}
                </button>
                <button type="button" aria-label={`${o.visible ? "Hide" : "Show"} ${o.name}`}
                        onClick={() => patchObject(o.id, { visible: !o.visible })}
                        className="grid size-6 place-items-center rounded-full text-faint hover:text-chalk">
                  {o.visible ? <Eye className="size-3.5" aria-hidden /> : <EyeOff className="size-3.5" aria-hidden />}
                </button>
              </div>
            ))}
          </section>
          <p className="mt-auto text-2xs leading-relaxed text-faint">
            W move, E rotate, R scale, C camera view, K key at the playhead, Space play, Ctrl+Z undo, Ctrl+D duplicate,
            Delete remove.
          </p>
        </aside>

        <div className="flex min-h-0 flex-col">
          <div className="relative grid min-h-0 min-w-0 flex-1 place-items-center overflow-hidden bg-black/40 [container-type:size]">
            {/* through the camera: the largest box of the shot's shape that fits (width capped by height x ratio) */}
            <div className="relative h-full w-full"
                 style={look === "camera" ? { aspectRatio: ASPECT[doc.settings.aspect_ratio], height: "auto",
                                              width: `min(100cqw, calc(100cqh * ${ASPECT[doc.settings.aspect_ratio]}))` } : undefined}>
              <Viewport doc={doc} selected={selected} time={time} mode={mode} look={look} onSelect={select} handleRef={handle}
                        onCommit={(objId, t: Transform) => setPose(objId, { transform: t })} />
            </div>
          </div>
          <div className="border-t border-line-soft">
            <div className="flex items-center gap-2 px-4 pt-2">
              <Button size="sm" tone="ghost" aria-label={playing ? "Pause" : "Play"} onClick={() => setPlaying((p) => !p)}>
                {playing ? <Pause className="size-4" aria-hidden /> : <Play className="size-4" aria-hidden />}
              </Button>
              <Button size="sm" tone="ghost" onClick={() => current && keyAt(current.id)} disabled={!current}>
                Key {current ? current.name : "the selection"}
              </Button>
            </div>
            <Timeline doc={doc} time={time} selected={selected} onTime={(t) => { setPlaying(false); setTime(t); }} />
          </div>
        </div>

        <aside className="min-h-0 overflow-y-auto border-t border-line-soft p-4 scrollbar-quiet lg:border-t-0 lg:border-l">
          <Properties doc={doc} object={current ? evaluate(current, trackOf(doc, current.id), time) : null}
                      onPose={setPose} handleRef={handle} projectId={id} />
        </aside>
      </div>
      <AddModel open={adding === "model"} onClose={() => setAdding(null)} />
      <AddWorld open={adding === "world"} onClose={() => setAdding(null)} />
      <HistoryDialog id={id} open={history} onClose={() => setHistory(false)}
                     onRestored={() => { loadedFor.current = null; void refetch(); }} />
    </div>
  );
}
