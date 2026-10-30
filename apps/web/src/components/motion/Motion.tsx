"use client";

import { ArrowRight, X } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";
import { cn } from "@/lib/cn";
import { useDraft } from "@/lib/draft";
import { CATEGORIES, type Direction, glyphClass, MAX_MOVES, type Move, useDirectorLibrary } from "@/components/create/DirectorPanel";
import { Button, Segmented } from "@/components/ui/controls";

type Speed = "slow" | "normal" | "fast";
type Picked = { move: Move; speed: Speed };

/** The camera-move library: browse, stack up to three, then shoot it in Create video. */
export function Motion() {
  const router = useRouter();
  const { data: library } = useDirectorLibrary();
  const draft = useDraft((s) => s.video);
  const setDraft = useDraft((s) => s.set);
  const [stack, setStack] = useState<Picked[]>([]);
  const [subject, setSubject] = useState("");
  const [query, setQuery] = useState("");
  const moves = (library?.moves ?? []).filter(
    (m) => !query || `${m.label} ${m.description}`.toLowerCase().includes(query.toLowerCase()),
  );

  function add(move: Move) {
    if (stack.length >= MAX_MOVES) {
      toast(`Up to ${MAX_MOVES} moves per shot. Remove one first.`);
      return;
    }
    setStack((s) => [...s, { move, speed: "normal" }]);
  }

  function shoot() {
    const direction = (draft.direction ?? {}) as Direction;
    setDraft("video", {
      direction: {
        ...direction,
        camera_moves: stack.map((p) => ({ id: p.move.id, speed: p.speed })),
        subject: subject.trim() || direction.subject || null,
      } as Record<string, unknown>,
    });
    router.push("/create/video");
  }

  return (
    <div className="grid h-full grid-cols-1 lg:grid-cols-[1fr_320px]">
      <div className="min-h-0 overflow-y-auto p-6 scrollbar-quiet">
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search 65 moves, e.g. orbit, zoom, drone"
          aria-label="Search camera moves"
          className="mb-6 h-9 w-full max-w-sm rounded-[var(--radius-control)] border border-line-soft bg-bay px-3 text-sm outline-none placeholder:text-faint focus:border-tungsten/60"
        />
        {CATEGORIES.filter((c) => c.id !== "all").map((c) => {
          const inCategory = moves.filter((m) => m.category === c.id);
          if (inCategory.length === 0) return null;
          return (
            <section key={c.id} className="mb-8">
              <h2 className="type-title mb-3 text-[15px]">{c.label}</h2>
              <ul className="grid grid-cols-[repeat(auto-fill,minmax(190px,1fr))] gap-3">
                {inCategory.map((m) => {
                  const used = stack.filter((p) => p.move.id === m.id).length;
                  return (
                    <li key={m.id}>
                      <button
                        type="button"
                        onClick={() => add(m)}
                        className={cn(
                          "group flex h-full w-full flex-col rounded-[var(--radius-frame)] bg-panel p-3 text-left transition-colors hover:bg-raised",
                          used > 0 && "ring-1 ring-tungsten/70",
                        )}
                      >
                        <span aria-hidden className="mb-3 grid h-20 place-items-center rounded-[4px] bg-bay">
                          <span className={cn("block size-2 rounded-full bg-tungsten", glyphClass(m))} />
                        </span>
                        <span className="text-sm text-chalk">{m.label}</span>
                        <span className="mt-0.5 line-clamp-2 text-2xs leading-relaxed text-faint">{m.description}</span>
                      </button>
                    </li>
                  );
                })}
              </ul>
            </section>
          );
        })}
      </div>

      <aside className="flex min-h-0 flex-col gap-4 border-t border-line-soft p-5 lg:border-t-0 lg:border-l">
        <div>
          <h2 className="type-title text-base">Your shot</h2>
          <p className="mt-1 text-xs leading-relaxed text-muted">
            Pick up to {MAX_MOVES} moves; they play in order. Every video model gets them in the wording it
            understands best.
          </p>
        </div>
        <ol className="flex flex-col gap-2">
          {stack.map((p, i) => (
            <li key={`${p.move.id}-${i}`} className="rounded-[var(--radius-control)] bg-panel p-3">
              <div className="flex items-center gap-2">
                <span className="tabular text-2xs text-faint">{i + 1}</span>
                <span className="flex-1 text-sm text-chalk">{p.move.label}</span>
                <button
                  type="button"
                  onClick={() => setStack((s) => s.filter((_, j) => j !== i))}
                  aria-label={`Remove ${p.move.label}`}
                  className="grid size-7 place-items-center rounded-full text-muted hover:bg-raised hover:text-chalk"
                >
                  <X className="size-3.5" aria-hidden />
                </button>
              </div>
              {p.move.speeds && (
                <div className="mt-2">
                  <Segmented
                    label={`${p.move.label} speed`}
                    value={p.speed}
                    options={["slow", "normal", "fast"] as const}
                    render={(s) => s.charAt(0).toUpperCase() + s.slice(1)}
                    onChange={(speed) => setStack((s) => s.map((x, j) => (j === i ? { ...x, speed } : x)))}
                  />
                </div>
              )}
            </li>
          ))}
          {stack.length === 0 && (
            <li className="rounded-[var(--radius-control)] border border-dashed border-line p-4 text-xs text-faint">
              Click a move to add it.
            </li>
          )}
        </ol>
        <label className="flex flex-col gap-1.5 text-xs text-muted">
          Moves relative to (optional)
          <input
            value={subject}
            onChange={(e) => setSubject(e.target.value)}
            placeholder="e.g. the dancer, @maya"
            className="h-9 rounded-[var(--radius-control)] border border-line-soft bg-bay px-3 text-sm text-chalk outline-none placeholder:text-faint focus:border-tungsten/60"
          />
        </label>
        <Button tone="primary" onClick={shoot} disabled={stack.length === 0} className="mt-auto">
          Shoot this in Create video
          <ArrowRight className="size-4" aria-hidden />
        </Button>
      </aside>
    </div>
  );
}
