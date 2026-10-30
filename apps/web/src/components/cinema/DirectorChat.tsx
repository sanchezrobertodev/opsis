"use client";

import { ArrowUp, LoaderCircle, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { cn, usd } from "@/lib/cn";
import { type CinemaDoc, type DirectorAnswer, type DirectorTurn, type Scene, type StoryboardChange, useDirector } from "@/lib/cinema";
import { Button } from "@/components/ui/controls";
import { useDirectorLibrary } from "@/components/create/DirectorPanel";
import { useClaudeCost } from "@/components/llm/ClaudeCost";

type Message =
  | { role: "user"; text: string }
  | { role: "director"; text: string; answer: DirectorAnswer; basis: string; state: "open" | "applied" | "discarded" };

const FIELD: Record<string, string> = {
  action: "Action", shot_size: "Shot size", angle: "Angle", camera_moves: "Camera", duration: "Length",
  continuity: "Continuity", lines: "Lines", feelings: "Feelings", heading: "Heading", summary: "Summary",
};
const KIND: Record<StoryboardChange["kind"], string> = { added: "Added", removed: "Removed", moved: "Moved", changed: "Changed" };
const KIND_TONE: Record<StoryboardChange["kind"], string> = {
  added: "text-done", removed: "text-fault", moved: "text-tungsten", changed: "text-tungsten",
};

/** Ask the director for changes in words; each answer is a diff to apply to the storyboard or let go. */
export function DirectorChat({ projectId, doc, update, onClose }: {
  projectId: string; doc: CinemaDoc; update: (fn: (d: CinemaDoc) => CinemaDoc) => void; onClose: () => void;
}) {
  const ask = useDirector(projectId);
  const claude = useClaudeCost("cinema.director");
  const [messages, setMessages] = useState<Message[]>([]);
  const [draft, setDraft] = useState("");
  const end = useRef<HTMLDivElement>(null);
  const label = useLabels();

  useEffect(() => {
    end.current?.scrollIntoView({ block: "end" });
  }, [messages.length, ask.isPending]);

  async function send() {
    const text = draft.trim();
    if (!text || ask.isPending) return;
    const history: DirectorTurn[] = messages.map((m) => ({ role: m.role, text: m.text }));
    setMessages((ms) => [...ms, { role: "user", text }]);
    setDraft("");
    try {
      const answer = await ask.mutateAsync({ message: text, history, scenes: doc.scenes });
      setMessages((ms) => [...ms, { role: "director", text: answer.reply, answer, basis: JSON.stringify(doc.scenes),
                                    state: answer.changes.length ? "open" : "discarded" }]);
    } catch (err) {
      setMessages((ms) => ms.slice(0, -1));
      setDraft(text);
      toast.error(err instanceof Error ? err.message : "The director couldn't answer");
    }
  }

  function settle(i: number, state: "applied" | "discarded") {
    const m = messages[i];
    if (m.role !== "director") return;
    if (state === "applied") {
      if (JSON.stringify(doc.scenes) !== m.basis) {
        toast.error("The storyboard changed since you asked. Ask again to get changes for it as it is now.");
        return;
      }
      update((d) => ({ ...d, scenes: m.answer.scenes as unknown as Scene[] }));
    }
    setMessages((ms) => ms.map((x, j) => (j === i ? { ...m, state } : x)));
  }

  return (
    <aside aria-label="AI Director" className="flex h-full min-h-0 flex-col border-l border-line-soft bg-bay">
      <div className="flex items-center justify-between gap-3 border-b border-line-soft px-4 py-3">
        <div>
          <h2 className="type-title text-base">Director</h2>
          <p className="text-2xs text-faint">Ask for changes in words. Nothing changes until you apply it.</p>
        </div>
        <Button size="sm" tone="ghost" onClick={onClose} aria-label="Close the director">
          <X className="size-4" aria-hidden />
        </Button>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-4 scrollbar-quiet" aria-live="polite">
        {messages.length === 0 && (
          <ul className="flex flex-col gap-2 text-xs text-muted">
            <li>Try: &ldquo;Open on a wide of the harbour before shot 1&rdquo;</li>
            <li>&ldquo;Shot 3 should be a low-angle close-up, slower&rdquo;</li>
            <li>&ldquo;Cut the second scene down to three shots&rdquo;</li>
          </ul>
        )}
        <ol className="flex flex-col gap-4">
          {messages.map((m, i) => (
            <li key={i} className={cn("flex flex-col gap-2", m.role === "user" && "items-end")}>
              {m.role === "user" ? (
                <p className="max-w-[85%] whitespace-pre-wrap rounded-[var(--radius-panel)] bg-raised px-3 py-2 text-sm text-chalk">{m.text}</p>
              ) : (
                <Answer message={m} label={label} onApply={() => settle(i, "applied")} onDiscard={() => settle(i, "discarded")} />
              )}
            </li>
          ))}
          {ask.isPending && (
            <li className="flex items-center gap-2 text-xs text-muted">
              <LoaderCircle className="size-4 animate-spin" aria-hidden /> Thinking it through&hellip;
            </li>
          )}
        </ol>
        <div ref={end} />
      </div>

      <form className="border-t border-line-soft p-3" onSubmit={(e) => { e.preventDefault(); void send(); }}>
        <div className="flex items-end gap-2 rounded-[var(--radius-control)] border border-line-soft bg-panel p-2 focus-within:border-tungsten/60">
          <textarea value={draft} onChange={(e) => setDraft(e.target.value)} rows={2} aria-label="Message the director"
                    placeholder={doc.scenes.length ? "What should change?" : "Plan the script first, then ask for changes"}
                    onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); void send(); } }}
                    className="min-h-[2.5rem] flex-1 resize-none bg-transparent text-sm outline-none placeholder:text-faint" />
          <Button size="sm" tone="primary" type="submit" disabled={!draft.trim() || ask.isPending} aria-label="Send">
            <ArrowUp className="size-4" aria-hidden />
          </Button>
        </div>
        <p className="mt-1.5 text-2xs text-faint">Each message asks {claude.text}.</p>
      </form>
    </aside>
  );
}

function Answer({ message, label, onApply, onDiscard }: {
  message: Extract<Message, { role: "director" }>; label: (field: string, value: unknown) => string;
  onApply: () => void; onDiscard: () => void;
}) {
  const { answer, state } = message;
  return (
    <div className="flex w-full flex-col gap-2">
      <p className="whitespace-pre-wrap text-sm text-chalk">{message.text}</p>
      {answer.warnings.map((w) => <p key={w} className="text-2xs text-tungsten">{w}</p>)}
      {answer.changes.length > 0 && (
        <div className={cn("flex flex-col gap-2 rounded-[var(--radius-panel)] bg-panel p-3", state !== "open" && "opacity-60")}>
          <ul className="flex flex-col gap-2.5" aria-label="Proposed changes">
            {answer.changes.map((c, k) => (
              <li key={k} className="text-xs">
                <p>
                  <span className={cn("mr-1.5", KIND_TONE[c.kind])}>{KIND[c.kind]}</span>
                  <span className="text-chalk">{c.label}</span>
                </p>
                {c.fields.length > 0 && (
                  <dl className="mt-1 grid grid-cols-[5.5rem_minmax(0,1fr)] gap-x-2 gap-y-1">
                    {c.fields.map((f) => (
                      <div key={f.field} className="contents">
                        <dt className="text-faint">{FIELD[f.field] ?? f.field}</dt>
                        <dd className="min-w-0">
                          {f.before != null && c.kind !== "added" && (
                            <del className="block whitespace-pre-wrap text-muted decoration-fault/60">{label(f.field, f.before)}</del>
                          )}
                          <ins className="block whitespace-pre-wrap text-chalk no-underline">{f.after == null ? "none" : label(f.field, f.after)}</ins>
                        </dd>
                      </div>
                    ))}
                  </dl>
                )}
              </li>
            ))}
          </ul>
          <div className="flex items-center gap-2 pt-1">
            {state === "open" ? (
              <>
                <Button size="sm" tone="primary" onClick={onApply}>Apply changes</Button>
                <Button size="sm" tone="ghost" onClick={onDiscard}>Discard</Button>
              </>
            ) : (
              <span className="text-2xs text-faint">{state === "applied" ? "Applied" : "Discarded"}</span>
            )}
            <span className="tabular ml-auto text-2xs text-faint">{answer.mock ? "mock" : usd(answer.cost_usd)}</span>
          </div>
        </div>
      )}
    </div>
  );
}

/** Ids from the director's lists read as their labels ("close_up" -> "Close-up"). */
function useLabels() {
  const { data: lib } = useDirectorLibrary();
  return (field: string, value: unknown): string => {
    const text = String(value);
    if (field === "duration") return `${text} s`;
    if (field === "continuity") return text === "continue" ? "Continues the shot before" : "Cut";
    const list = field === "shot_size" ? lib?.shot_sizes : field === "angle" ? lib?.angles
      : field === "camera_moves" ? lib?.moves : field === "feelings" ? lib?.emotions : undefined;
    if (!list) return text;
    const name = (id: string) => list.find((x) => x.id === id)?.label ?? id.replaceAll("_", " ");
    if (field === "camera_moves") return text.split(" + ").map(name).join(" + ");
    if (field === "feelings") return text.split(", ").map((x) => { const [who, e] = x.split(" "); return `${who} ${name(e ?? "")}`; }).join(", ");
    return name(text);
  };
}
