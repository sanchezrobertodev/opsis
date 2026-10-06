"use client";

import { useQuery } from "@tanstack/react-query";
import { ChevronDown, Plus, X } from "lucide-react";
import { useMemo, useState } from "react";
import type { Schemas } from "@studio/api-client";
import { api, unwrap } from "@/lib/api";
import { cn } from "@/lib/cn";
import { Dialog, Segmented } from "@/components/ui/controls";

export type Direction = Schemas["ShotDirection"];
type Library = Schemas["DirectorLibraryOut"];
export type Move = Library["moves"][number];

export const CATEGORIES: { id: Move["category"] | "all"; label: string }[] = [
  { id: "all", label: "All" },
  { id: "push", label: "Push and zoom" },
  { id: "orbit", label: "Orbit" },
  { id: "lateral", label: "Pan and track" },
  { id: "vertical", label: "Crane and tilt" },
  { id: "handheld", label: "Handheld" },
  { id: "vehicle", label: "Vehicles and flight" },
  { id: "time", label: "Time" },
  { id: "lens", label: "Lens" },
  { id: "static", label: "Static" },
];

const LOOKS: { key: keyof Direction; section: keyof Library; label: string }[] = [
  { key: "shot_size", section: "shot_sizes", label: "Shot size" },
  { key: "angle", section: "angles", label: "Angle" },
  { key: "lens", section: "lenses", label: "Lens" },
  { key: "aperture", section: "apertures", label: "Aperture" },
  { key: "camera_body", section: "camera_bodies", label: "Camera" },
  { key: "era", section: "eras", label: "Era" },
  { key: "lighting", section: "lighting", label: "Lighting" },
  { key: "palette", section: "palettes", label: "Color grade" },
  { key: "genre", section: "genres", label: "Genre" },
  { key: "pacing", section: "pacing", label: "Pacing" },
  { key: "emotion", section: "emotions", label: "Emotion" },
];

export const MAX_MOVES = 3;

export function useDirectorLibrary() {
  return useQuery({
    queryKey: ["director-library"],
    queryFn: () => unwrap(api.GET("/director/library")),
    staleTime: Infinity,
  });
}

export function DirectorPanel({
  direction,
  onChange,
  stills,
}: {
  direction: Direction;
  onChange: (d: Direction) => void;
  /** Image models: camera moves don't apply. */
  stills: boolean;
}) {
  const { data: lib } = useDirectorLibrary();
  const [picking, setPicking] = useState(false);
  const [lookOpen, setLookOpen] = useState(false);
  const moves = direction.camera_moves ?? [];
  const chosenLooks = LOOKS.filter((l) => direction[l.key]);

  if (!lib) return null;
  const moveById = new Map(lib.moves.map((m) => [m.id, m]));

  return (
    <div className="space-y-5">
      {!stills && (
        <div className="flex flex-col gap-2">
          <div className="flex items-baseline justify-between">
            <span className="text-xs text-muted">Camera</span>
            <span className="tabular text-2xs text-faint">
              {moves.length} of {MAX_MOVES}, in order
            </span>
          </div>
          {moves.length > 0 && (
            <ol className="space-y-1.5">
              {moves.map((choice, i) => {
                const move = moveById.get(choice.id);
                return (
                  <li key={`${choice.id}-${i}`} className="flex items-center gap-2 rounded-[var(--radius-control)] bg-bay px-2.5 py-1.5">
                    <MoveGlyph category={move?.category ?? "static"} small />
                    <span className="min-w-0 flex-1 truncate text-sm">{move?.label ?? choice.id}</span>
                    {move?.speeds && (
                      <Segmented
                        label={`${move.label} speed`}
                        value={choice.speed ?? "normal"}
                        options={["slow", "normal", "fast"] as const}
                        render={(s) => s.charAt(0).toUpperCase() + s.slice(1)}
                        onChange={(speed) =>
                          onChange({ ...direction, camera_moves: moves.map((m, j) => (j === i ? { ...m, speed } : m)) })
                        }
                      />
                    )}
                    <button
                      type="button"
                      aria-label={`Remove ${move?.label ?? "move"}`}
                      onClick={() => onChange({ ...direction, camera_moves: moves.filter((_, j) => j !== i) })}
                      className="grid size-7 shrink-0 place-items-center rounded-full text-faint hover:bg-raised hover:text-chalk"
                    >
                      <X className="size-3.5" aria-hidden />
                    </button>
                  </li>
                );
              })}
            </ol>
          )}
          {moves.length < MAX_MOVES && (
            <button
              type="button"
              onClick={() => setPicking(true)}
              className="flex h-10 items-center justify-center gap-2 rounded-[var(--radius-control)] border border-dashed border-line text-xs text-muted hover:border-tungsten/50 hover:text-chalk"
            >
              <Plus className="size-3.5" aria-hidden />
              {moves.length === 0 ? "Add a camera move" : "Add another move"}
            </button>
          )}
          {moves.length > 0 && (
            <label className="flex items-center gap-2 text-xs text-muted">
              Moves relative to
              <input
                value={direction.subject ?? ""}
                onChange={(e) => onChange({ ...direction, subject: e.target.value || null })}
                placeholder="the subject"
                className="h-8 min-w-0 flex-1 rounded-[var(--radius-control)] border border-line-soft bg-bay px-2 text-sm text-chalk outline-none placeholder:text-faint focus:border-tungsten/60"
              />
            </label>
          )}
        </div>
      )}

      <div>
        <button
          type="button"
          onClick={() => setLookOpen((v) => !v)}
          aria-expanded={lookOpen}
          className="flex w-full items-center gap-1.5 text-left text-xs text-muted hover:text-chalk"
        >
          <ChevronDown className={cn("size-3.5 transition-transform", lookOpen && "rotate-180")} aria-hidden />
          Cinema look
          {chosenLooks.length > 0 && (
            <span className="ml-auto truncate text-2xs text-faint">
              {chosenLooks
                .map((l) => (lib[l.section] as { id: string; label: string }[]).find((i) => i.id === direction[l.key])?.label)
                .filter(Boolean)
                .join(", ")}
            </span>
          )}
        </button>
        {lookOpen && (
          <div className="mt-3 grid grid-cols-2 gap-x-3 gap-y-3">
            {LOOKS.map((l) => {
              const items = lib[l.section] as { id: string; label: string }[];
              return (
                <label key={l.key} className="flex flex-col gap-1 text-2xs text-faint">
                  {l.label}
                  <select
                    value={(direction[l.key] as string | null) ?? ""}
                    onChange={(e) => onChange({ ...direction, [l.key]: e.target.value || null })}
                    className="h-9 rounded-[var(--radius-control)] border border-line-soft bg-bay px-2 text-xs text-chalk outline-none focus:border-tungsten/60"
                  >
                    <option value="">Any</option>
                    {items.map((i) => (
                      <option key={i.id} value={i.id}>
                        {i.label}
                      </option>
                    ))}
                  </select>
                </label>
              );
            })}
          </div>
        )}
      </div>

      <MovePicker
        open={picking}
        moves={lib.moves}
        onClose={() => setPicking(false)}
        onPick={(m) => {
          onChange({ ...direction, camera_moves: [...moves, { id: m.id, speed: "normal" as const }].slice(0, MAX_MOVES) });
          setPicking(false);
        }}
      />
    </div>
  );
}

function MovePicker({
  open,
  moves,
  onClose,
  onPick,
}: {
  open: boolean;
  moves: Move[];
  onClose: () => void;
  onPick: (m: Move) => void;
}) {
  const [category, setCategory] = useState<(typeof CATEGORIES)[number]["id"]>("all");
  const [query, setQuery] = useState("");
  const shown = useMemo(
    () =>
      moves.filter(
        (m) =>
          (category === "all" || m.category === category) &&
          (!query || `${m.label} ${m.description}`.toLowerCase().includes(query.toLowerCase())),
      ),
    [moves, category, query],
  );
  return (
    <Dialog open={open} onClose={onClose} title="Camera moves" wide>
      <div className="flex flex-col gap-3">
        <input
          autoFocus
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search moves, e.g. orbit, zoom, drone"
          className="h-9 rounded-[var(--radius-control)] border border-line-soft bg-bay px-3 text-sm text-chalk outline-none placeholder:text-faint focus:border-tungsten/60"
        />
        <div className="flex flex-wrap gap-1">
          {CATEGORIES.map((c) => (
            <button
              key={c.id}
              type="button"
              aria-pressed={category === c.id}
              onClick={() => setCategory(c.id)}
              className={cn(
                "rounded-full px-3 py-1 text-xs",
                category === c.id ? "bg-raised text-chalk" : "text-muted hover:text-chalk",
              )}
            >
              {c.label}
            </button>
          ))}
        </div>
        <ul className="grid max-h-[55vh] grid-cols-2 gap-2 overflow-y-auto pr-1 scrollbar-quiet sm:grid-cols-3">
          {shown.map((m) => (
            <li key={m.id}>
              <button
                type="button"
                onClick={() => onPick(m)}
                className="group flex h-full w-full items-start gap-3 rounded-[var(--radius-control)] border border-line-soft p-3 text-left hover:border-tungsten/50 hover:bg-raised/60"
              >
                <MoveGlyph category={m.category} />
                <span className="min-w-0">
                  <span className="block text-sm text-chalk">{m.label}</span>
                  <span className="mt-0.5 block text-2xs leading-relaxed text-faint">{m.description}</span>
                </span>
              </button>
            </li>
          ))}
          {shown.length === 0 && <li className="col-span-full text-sm text-muted">No moves match that.</li>}
        </ul>
      </div>
    </Dialog>
  );
}

/** A tiny frame with a mark that moves the way the category moves (hover to play). */
export function MoveGlyph({ category, small }: { category: Move["category"]; small?: boolean }) {
  return (
    <span
      aria-hidden
      className={cn(
        "relative grid shrink-0 place-items-center overflow-hidden rounded-[3px] border border-line bg-bay",
        small ? "h-5 w-7" : "h-8 w-11",
      )}
    >
      <span className={cn("block size-1.5 rounded-full bg-tungsten", `glyph-${category}`)} />
    </span>
  );
}
