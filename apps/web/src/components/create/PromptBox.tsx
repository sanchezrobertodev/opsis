"use client";

import { useMutation } from "@tanstack/react-query";
import { LoaderCircle, Sparkles } from "lucide-react";
import { useId, useRef, useState } from "react";
import { toast } from "sonner";
import { api, type ModelDetail, mediaUrl, unwrap } from "@/lib/api";
import { cn, usd } from "@/lib/cn";
import { KIND_LABEL, useElements } from "@/lib/elements";

/** An `@partial` right before the caret (same rule as the server's mention parser). */
const TRIGGER = /(?:^|[^\w@])@([A-Za-z0-9_-]{0,40})$/;

export function PromptBox({
  model,
  mode,
  direction,
  value,
  onChange,
  onSubmit,
}: {
  model: ModelDetail;
  mode: string;
  /** Shot direction, so the rewrite leaves camera and look to the director. */
  direction: Record<string, unknown> | null;
  value: string;
  onChange: (v: string) => void;
  onSubmit: () => void;
}) {
  const ref = useRef<HTMLTextAreaElement>(null);
  const id = useId();
  const [query, setQuery] = useState<string | null>(null);
  const [active, setActive] = useState(0);
  const { data: elements } = useElements();
  const max = model.prompt.max_chars;
  const near = value.length > max * 0.9;
  const enhance = useMutation({
    mutationFn: () =>
      unwrap(api.POST("/llm/enhance", { body: { model_id: model.id, mode, prompt: value, direction: direction as never } })),
  });

  async function improve() {
    const before = value;
    try {
      const r = await enhance.mutateAsync();
      onChange(r.prompt);
      toast(r.mock ? "Enhanced with the mock writer" : `Enhanced with Claude for ${usd(r.cost_usd)}`, {
        description: [...r.changes, ...r.warnings].join(". ") || undefined,
        action: { label: "Undo", onClick: () => onChange(before) },
        duration: 10_000,
      });
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Could not enhance the prompt");
    }
  }

  const matches =
    query === null
      ? []
      : (elements ?? [])
          .filter((e) => e.slug.startsWith(query) || e.name.toLowerCase().includes(query))
          .slice(0, 6);
  const open = matches.length > 0;

  function sync(el: HTMLTextAreaElement) {
    const m = TRIGGER.exec(el.value.slice(0, el.selectionStart));
    const next = m && el.selectionStart === el.selectionEnd ? m[1].toLowerCase() : null;
    if (next !== query) {
      setQuery(next);
      setActive(0);
    }
  }

  function pick(slug: string) {
    const el = ref.current;
    if (!el) return;
    const caret = el.selectionStart;
    const m = TRIGGER.exec(value.slice(0, caret));
    if (!m) return;
    const start = caret - m[1].length - 1;
    const rest = value.slice(caret).replace(/^[A-Za-z0-9_-]*\s?/, "");
    onChange(`${value.slice(0, start)}@${slug} ${rest}`);
    setQuery(null);
    const pos = start + slug.length + 2;
    requestAnimationFrame(() => {
      el.focus();
      el.setSelectionRange(pos, pos);
    });
  }

  return (
    <div className="relative flex flex-col gap-2">
      <span className="flex items-baseline justify-between text-xs text-muted">
        <label htmlFor={`${id}-prompt`}>Prompt</label>
        <span className="flex items-baseline gap-3">
          <button
            type="button"
            onClick={() => void improve()}
            disabled={!value.trim() || enhance.isPending}
            title="Rewrite the prompt for this model with Claude. Your camera and look settings stay separate."
            className="inline-flex items-center gap-1 text-2xs text-muted hover:text-tungsten disabled:cursor-not-allowed disabled:opacity-45"
          >
            {enhance.isPending ? (
              <LoaderCircle className="size-3 animate-spin" aria-hidden />
            ) : (
              <Sparkles className="size-3" aria-hidden />
            )}
            {enhance.isPending ? "Enhancing" : "Enhance"}
          </button>
          <span className={cn("tabular text-2xs", near ? "text-tungsten" : "text-faint")}>
            {value.length} / {max}
          </span>
        </span>
      </span>
      <textarea
        ref={ref}
        id={`${id}-prompt`}
        value={value}
        maxLength={max}
        rows={5}
        aria-autocomplete="list"
        aria-controls={open ? `${id}-mentions` : undefined}
        aria-activedescendant={open ? `${id}-m${active}` : undefined}
        onChange={(e) => {
          onChange(e.target.value);
          sync(e.target);
        }}
        onSelect={(e) => sync(e.currentTarget)}
        onBlur={() => setQuery(null)}
        onKeyDown={(e) => {
          if (open) {
            if (e.key === "ArrowDown" || e.key === "ArrowUp") {
              e.preventDefault();
              const step = e.key === "ArrowDown" ? 1 : -1;
              setActive((a) => (a + step + matches.length) % matches.length);
              return;
            }
            if (e.key === "Enter" || e.key === "Tab") {
              e.preventDefault();
              pick(matches[active].slug);
              return;
            }
            if (e.key === "Escape") {
              e.preventDefault();
              setQuery(null);
              return;
            }
          }
          if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
            e.preventDefault();
            onSubmit();
          }
        }}
        placeholder="Describe the shot: subject, action, setting, camera, light and sound. Type @ to bring in a character."
        className="min-h-28 resize-y rounded-[var(--radius-control)] border border-line-soft bg-bay px-3 py-2.5 text-sm leading-relaxed text-chalk outline-none placeholder:text-faint focus:border-tungsten/60"
      />
      {open && (
        <ul
          id={`${id}-mentions`}
          role="listbox"
          aria-label="Characters, locations and props"
          className="absolute inset-x-0 top-full z-20 mt-1 overflow-hidden rounded-[var(--radius-control)] border border-line bg-panel py-1 shadow-lg shadow-black/40"
        >
          {matches.map((e, i) => (
            <li
              key={e.id}
              id={`${id}-m${i}`}
              role="option"
              aria-selected={i === active}
              onMouseDown={(ev) => {
                ev.preventDefault(); // keep focus and caret in the textarea
                pick(e.slug);
              }}
              onMouseEnter={() => setActive(i)}
              className={cn("flex cursor-pointer items-center gap-3 px-2 py-1.5", i === active && "bg-raised")}
            >
              <span className="size-8 shrink-0 overflow-hidden rounded-[4px] bg-bay">
                {e.cover && (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img src={mediaUrl(e.cover.thumb_url ?? e.cover.url)} alt="" className="size-full object-cover" />
                )}
              </span>
              <span className="min-w-0 flex-1">
                <span className="block truncate text-sm text-chalk">{e.name}</span>
                <span className="block truncate text-2xs text-faint">
                  <span className="text-tungsten">{e.handle}</span>, {KIND_LABEL[e.kind].toLowerCase()}
                  {e.images.length ? "" : ", no images"}
                </span>
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
