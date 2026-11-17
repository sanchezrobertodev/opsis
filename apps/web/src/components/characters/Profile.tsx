"use client";

import { LoaderCircle, Plus, Sparkles, UserRoundPen, X } from "lucide-react";
import { useState } from "react";
import { type Element, useDraftProfile } from "@/lib/elements";
import { draftOver, type Profile, written } from "@/lib/profile";
import { useClaudeCost } from "@/components/llm/ClaudeCost";
import { Button } from "@/components/ui/controls";

const FIELD = "rounded-[var(--radius-control)] border border-line-soft bg-bay px-3 text-sm text-chalk outline-none placeholder:text-faint focus:border-tungsten/60 disabled:opacity-60";
const CATCHPHRASES = 5; // the server's limits (ElementProfile)

/** A character's profile: who they are beyond their look, for the lines a vlog, a film or a Faceless episode gives
 * them. `element` is the saved character Claude drafts from (none yet: no draft). Saved with the rest of the form. */
export function ProfileSection({ element, value, onChange }: {
  element: Element | null; value: Profile; onChange: (p: Profile) => void;
}) {
  const draft = useDraftProfile();
  const claude = useClaudeCost("character.profile");
  // saved or written in here, so a draft leaves them (a draft's own text isn't: another draft replaces it)
  const [yours, setYours] = useState<(keyof Profile)[]>(() => written(value));
  const set = <K extends keyof Profile>(key: K, v: Profile[K]) => {
    setYours((y) => (y.includes(key) ? y : [...y, key]));
    onChange({ ...value, [key]: v });
  };
  const phrases = value.catchphrases;
  const busy = draft.isPending; // the fields wait for the draft, so it lands on what was there when asked

  return (
    <section aria-labelledby="profile-title" className="space-y-3 rounded-[var(--radius-control)] border border-line-soft p-4">
      <div className="flex flex-wrap items-center gap-2">
        <h3 id="profile-title" className="mr-auto flex items-center gap-2 text-sm text-chalk">
          <UserRoundPen className="size-4 text-tungsten" aria-hidden />
          Profile
        </h3>
        <Button type="button" size="sm" disabled={!element || busy}
                onClick={() => element && draft.mutate(element.id, { onSuccess: (d) => onChange(draftOver(value, d.profile, yours)) })}
                title={element ? `${claude.name} drafts it from the saved name, description, traits and looks`
                  : "Create the character first: Claude drafts from what's saved"}>
          {busy ? <LoaderCircle className="size-3.5 animate-spin" aria-hidden /> : <Sparkles className="size-3.5" aria-hidden />}
          {busy ? "Drafting…" : "Draft with Claude"}
          {claude.price && <span className="font-normal text-muted">· {claude.price}</span>}
        </Button>
      </div>
      <p className="text-2xs leading-relaxed text-faint">
        How they behave and talk, for the lines a vlog, a film or a Faceless episode writes for them; never sent to
        picture or video models. A draft fills each field you haven&rsquo;t written in, and nothing is kept until you
        save.
      </p>
      {draft.error && <p role="alert" className="text-xs leading-relaxed text-fault">{draft.error.message}</p>}
      <label className="flex flex-col gap-1.5 text-xs text-muted">
        Personality
        <textarea value={value.personality} onChange={(e) => set("personality", e.target.value)} maxLength={300} rows={2}
                  disabled={busy} placeholder="e.g. curious and restless, always asking one more question"
                  className={`resize-y py-2 leading-relaxed ${FIELD}`} />
      </label>
      <label className="flex flex-col gap-1.5 text-xs text-muted">
        How they talk
        <input value={value.speech} onChange={(e) => set("speech", e.target.value)} maxLength={200} disabled={busy}
               placeholder="e.g. fast, jumps between ideas, ends on a question" className={`h-10 ${FIELD}`} />
      </label>
      <fieldset className="flex flex-col gap-1.5">
        <legend className="mb-1.5 text-xs text-muted">
          Catchphrases <span className="text-faint">· up to {CATCHPHRASES}, used now and then</span>
        </legend>
        {phrases.map((c, i) => (
          // by position: they're only typed in or taken out, never moved
          <div key={i} className="flex items-center gap-1">
            <input value={c} maxLength={120} disabled={busy} aria-label={`Catchphrase ${i + 1}`}
                   onChange={(e) => set("catchphrases", phrases.map((x, j) => (j === i ? e.target.value : x)))}
                   className={`h-9 min-w-0 flex-1 ${FIELD}`} />
            <button type="button" aria-label={`Remove catchphrase ${i + 1}`} disabled={busy}
                    onClick={() => set("catchphrases", phrases.filter((_, j) => j !== i))}
                    className="grid size-7 shrink-0 place-items-center rounded-full text-muted hover:bg-raised hover:text-chalk">
              <X className="size-3.5" aria-hidden />
            </button>
          </div>
        ))}
        {phrases.length < CATCHPHRASES && (
          <button type="button" disabled={busy} onClick={() => set("catchphrases", [...phrases, ""])}
                  className="inline-flex h-8 items-center gap-1 self-start rounded-full border border-line-soft px-3 text-xs text-muted transition-colors hover:text-chalk">
            <Plus className="size-3.5" aria-hidden />
            Add a catchphrase
          </button>
        )}
      </fieldset>
      <label className="flex flex-col gap-1.5 text-xs text-muted">
        Backstory
        <textarea value={value.backstory} onChange={(e) => set("backstory", e.target.value)} maxLength={600} rows={3}
                  disabled={busy} placeholder="Where they come from and what they want."
                  className={`resize-y py-2 leading-relaxed ${FIELD}`} />
        <span className="text-2xs text-faint">It stays here: writers get the fields above, so their prompts stay short.</span>
      </label>
    </section>
  );
}
