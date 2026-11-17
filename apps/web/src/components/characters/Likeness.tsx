"use client";

import { LoaderCircle, ScanFace } from "lucide-react";
import type { Asset } from "@/lib/api";
import { cn } from "@/lib/cn";
import { type Likeness, useCheckLikeness, useElements } from "@/lib/elements";
import { hasPictures } from "@/lib/mentions";
import { useClaudeCost } from "@/components/llm/ClaudeCost";

type Mention = { id?: string; look?: string | null };
type Kept = Pick<Likeness, "verdict" | "differences" | "summary" | "mock">;

const VERDICT: Record<Likeness["verdict"], [string, string]> = {
  match: ["looks like them", "border-done/25 bg-done/10 text-done"],
  close: ["close", "border-tungsten/30 bg-tungsten/10 text-tungsten"],
  different: ["looks different", "border-fault/30 bg-fault/10 text-fault"],
};

/**
 * "Check likeness" for each of your characters a take's prompt mentioned (`request.mentions`; a cast, a place or a
 * style isn't checked, nor a character with no pictures to compare with), priced, and each answer as a badge that
 * opens to Claude's summary and the differences. The answer is kept on the asset, so it shows again here and in the
 * library. Pictures and clips only.
 */
export function LikenessChecks({ asset, mentions, title }: { asset: Asset; mentions: unknown; title?: string }) {
  const { data: characters } = useElements("character");
  const who = (Array.isArray(mentions) ? (mentions as Mention[]) : []).flatMap((m) => {
    const c = characters?.find((e) => e.id === m.id);
    // compared with the look's pictures, else the main ones (the server's `_references`)
    return c && hasPictures(c, m.look ?? null)
      ? [{ id: c.id, name: c.name, look: m.look ? (c.looks[m.look]?.name ?? m.look) : null }] : [];
  });
  if (!who.length || (asset.kind !== "image" && asset.kind !== "video")) return null;
  const list = (
    <ul className="flex flex-col gap-1.5" aria-label={title ? undefined : "Likeness"}>
      {who.map((c) => <CheckOne key={`${asset.id}:${c.id}`} asset={asset} character={c} />)}
    </ul>
  );
  return title ? (
    <section>
      <h3 className="mb-1.5 text-xs text-muted">{title}</h3>
      {list}
    </section>
  ) : list;
}

function CheckOne({ asset, character }: { asset: Asset; character: { id: string; name: string; look: string | null } }) {
  const check = useCheckLikeness();
  const claude = useClaudeCost("character.check");
  const kept = (asset.metadata.consistency as Record<string, Kept> | undefined)?.[character.id];
  const result: Kept | undefined = check.data ?? kept;
  const named = character.look ? `${character.name} (${character.look})` : character.name;
  return (
    <li className="flex flex-col gap-1 text-2xs">
      {result && (
        <details className="group">
          <summary className={cn("inline-flex cursor-pointer list-none items-center gap-1.5 rounded-full border px-2 py-1",
                                 VERDICT[result.verdict][1])}>
            <span className="size-1.5 shrink-0 rounded-full bg-current" aria-hidden />
            {named}: {VERDICT[result.verdict][0]}
          </summary>
          <div className="mt-1.5 space-y-1 pl-1 leading-relaxed text-muted">
            <p className="text-chalk">{result.summary}</p>
            {result.differences.length > 0 && (
              <ul className="space-y-0.5">
                {result.differences.map((d, i) => (
                  <li key={i}><span className="capitalize text-faint">{d.aspect}:</span> {d.detail}</li>
                ))}
              </ul>
            )}
            {result.mock && <p className="text-faint">A stand-in answer: Claude is in mock mode.</p>}
          </div>
        </details>
      )}
      <button type="button" disabled={check.isPending}
              onClick={() => check.mutate({ id: character.id, assetId: asset.id })}
              title={`${claude.name} compares this with ${named}'s pictures and says what differs`}
              className="inline-flex items-center gap-1.5 self-start rounded-full px-1 py-0.5 text-muted hover:text-chalk disabled:opacity-60">
        {check.isPending ? <LoaderCircle className="size-3 animate-spin" aria-hidden /> : <ScanFace className="size-3" aria-hidden />}
        {check.isPending ? "Checking…" : result ? `Check ${character.name} again` : `Check likeness: ${character.name}`}
        {claude.price && <span className="text-faint">· {claude.price}</span>}
      </button>
      {check.error && <p role="alert" className="leading-relaxed text-fault">{check.error.message}</p>}
    </li>
  );
}
