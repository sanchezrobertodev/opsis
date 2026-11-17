"use client";

import { useMutation } from "@tanstack/react-query";
import { LoaderCircle, Sparkles } from "lucide-react";
import { useId, useRef, useState } from "react";
import { toast } from "sonner";
import { api, type Asset, type ModelDetail, mediaUrl, unwrap } from "@/lib/api";
import { cn, usd } from "@/lib/cn";
import { KIND_LABEL, useElements } from "@/lib/elements";
import { completeMention, hasPictures, typedMention } from "@/lib/mentions";
import { useClaudeCost } from "@/components/llm/ClaudeCost";

/** One entry of the mention list: what picking it writes after the @, and how it shows. */
type Suggestion = { key: string; text: string; name: string; handle: string; note: string; cover: Asset | null };

export function PromptBox({
  model,
  mode,
  direction,
  value,
  onChange,
  onSubmit,
  placeholder = "Describe the shot: subject, action, setting, camera, light and sound. Type @ to bring in a character.",
}: {
  model: ModelDetail;
  mode: string;
  /** Shot direction, so the rewrite leaves camera and look to the director. */
  direction: Record<string, unknown> | null;
  value: string;
  onChange: (v: string) => void;
  onSubmit: () => void;
  placeholder?: string;
}) {
  const ref = useRef<HTMLTextAreaElement>(null);
  const id = useId();
  const [query, setQuery] = useState<{ slug: string; look: string | null } | null>(null);
  const [active, setActive] = useState(0);
  const { data: elements } = useElements();
  const max = model.prompt.max_chars;
  const near = value.length > max * 0.9;
  const claude = useClaudeCost("enhance");
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

  /** The saved elements whose handle or name starts what's typed, or after `@handle:` that character's looks. */
  function suggestions(): Suggestion[] {
    if (query === null) return [];
    const { slug: typed, look: partial } = query;
    if (partial !== null) {
      const e = elements?.find((x) => x.slug === typed);
      return e ? Object.entries(e.looks)
        .filter(([slug, l]) => slug.startsWith(partial) || l.name.toLowerCase().includes(partial))
        .slice(0, 6)
        .map(([slug, l]) => ({ key: slug, text: `${e.slug}:${slug}`, name: `${e.name}, ${l.name}`,
                               handle: `${e.handle}:${slug}`, note: "look",
                               cover: e.images.find((i) => i.look === slug)?.asset ?? e.cover })) : [];
    }
    return (elements ?? [])
      .filter((e) => e.slug.startsWith(typed) || e.name.toLowerCase().includes(typed))
      .slice(0, 6)
      .map((e) => ({ key: e.id, text: e.slug, name: e.name, handle: e.handle,
                     note: KIND_LABEL[e.kind].toLowerCase() + (e.kind === "cast"
                       ? `, ${e.members.length} character${e.members.length === 1 ? "" : "s"}`
                       : hasPictures(e) ? "" : ", no images"),
                     cover: e.cover ?? e.members[0]?.cover ?? null }));
  }
  const matches = suggestions();
  const open = matches.length > 0;

  function sync(el: HTMLTextAreaElement) {
    const typed = el.selectionStart === el.selectionEnd ? typedMention(el.value.slice(0, el.selectionStart)) : null;
    if (typed?.slug !== query?.slug || typed?.look !== query?.look) {
      setQuery(typed && { slug: typed.slug, look: typed.look });
      setActive(0);
    }
  }

  /** Writes `@text` (a handle, or `handle:look`) over what's typed before the caret. */
  function pick(text: string) {
    const el = ref.current;
    if (!el) return;
    const next = completeMention(value, el.selectionStart, text);
    if (!next) return;
    onChange(next.value);
    setQuery(null);
    requestAnimationFrame(() => {
      el.focus();
      el.setSelectionRange(next.caret, next.caret);
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
            title={`Rewrite the prompt for this model with ${claude.name}. Your camera and look settings stay separate.`}
            className="inline-flex items-center gap-1 text-2xs text-muted hover:text-tungsten disabled:cursor-not-allowed disabled:opacity-45"
          >
            {enhance.isPending ? (
              <LoaderCircle className="size-3 animate-spin" aria-hidden />
            ) : (
              <Sparkles className="size-3" aria-hidden />
            )}
            {enhance.isPending ? "Enhancing" : "Enhance"}
          </button>
          <span className={cn("readout text-2xs", near ? "text-tungsten" : "text-faint")}>
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
              pick(matches[active].text);
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
        placeholder={placeholder}
        className="min-h-28 resize-y rounded-[var(--radius-control)] border border-line-soft bg-bay px-3 py-2.5 text-sm leading-relaxed text-chalk outline-none placeholder:text-faint focus:border-tungsten/60"
      />
      <p className="text-2xs text-faint">Enhance uses {claude.text}.</p>
      {open && (
        <ul
          id={`${id}-mentions`}
          role="listbox"
          aria-label={query?.look != null ? "Looks" : "Characters, locations and props"}
          className="absolute inset-x-0 top-full z-20 mt-1 overflow-hidden rounded-[var(--radius-control)] border border-line bg-sheet py-1 shadow-lg shadow-black/40"
        >
          {matches.map((s, i) => (
            <li
              key={s.key}
              id={`${id}-m${i}`}
              role="option"
              aria-selected={i === active}
              onMouseDown={(ev) => {
                ev.preventDefault(); // keep focus and caret in the textarea
                pick(s.text);
              }}
              onMouseEnter={() => setActive(i)}
              className={cn("flex cursor-pointer items-center gap-3 px-2 py-1.5", i === active && "bg-raised")}
            >
              <span className="size-8 shrink-0 overflow-hidden rounded-[4px] bg-bay">
                {s.cover && (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img src={mediaUrl(s.cover.thumb_url ?? s.cover.url)} alt="" className="size-full object-cover" />
                )}
              </span>
              <span className="min-w-0 flex-1">
                <span className="block truncate text-sm text-chalk">{s.name}</span>
                <span className="block truncate text-2xs text-faint">
                  <span className="text-tungsten">{s.handle}</span>, {s.note}
                </span>
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
