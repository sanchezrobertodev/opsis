"use client";

import { useQuery } from "@tanstack/react-query";
import { CheckCircle2, CircleDashed } from "lucide-react";
import { api, unwrap } from "@/lib/api";

const SETUP = "uv run python scripts/setup_local.py";

/** Which free models your own ComfyUI can run now, and what each of the others still needs. */
export function LocalModels({ online }: { online: boolean }) {
  const query = useQuery({
    queryKey: ["local-models"],
    queryFn: () => unwrap(api.GET("/local-engine/models")),
    refetchInterval: 30_000,
    enabled: online,
  });
  const ready = query.data?.filter((m) => m.ready).length ?? 0;

  return (
    <section className="space-y-5 lg:col-span-2" aria-labelledby="local-models-title">
      <h2 id="local-models-title" className="type-title text-xl">Free models on your GPU</h2>
      <div className="space-y-4 rounded-[var(--radius-panel)] bg-panel p-4">
        <p className="text-xs leading-relaxed text-muted">
          These run on your own ComfyUI at no cost, and the forms start on one once it&apos;s ready. To set them up,
          run this in the Opsis folder on the computer with ComfyUI, while ComfyUI is running. It lists what fits your
          GPU and asks before installing anything.
        </p>
        <code className="block overflow-x-auto whitespace-nowrap rounded-[var(--radius-control)] bg-bay px-3 py-2 text-xs">
          {SETUP}
        </code>
        {!online && <p role="status" className="text-sm text-muted">Start ComfyUI to see which ones are ready.</p>}
        {online && query.isPending && <p className="text-sm text-muted">Asking ComfyUI…</p>}
        {query.error && <p role="alert" className="text-sm text-fault">{query.error.message}</p>}
        {online && query.data && (
          <>
            <p role="status" className="tabular text-xs text-muted">{ready} of {query.data.length} ready</p>
            <ul className="divide-y divide-line-soft">
              {query.data.map((m) => (
                <li key={m.id} className="flex items-start gap-3 py-2.5">
                  {m.ready
                    ? <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-done" aria-hidden />
                    : <CircleDashed className="mt-0.5 size-4 shrink-0 text-faint" aria-hidden />}
                  <div className="min-w-0">
                    <p className="text-sm">{m.name}</p>
                    <p className="break-words text-xs text-muted">
                      {m.ready ? "Ready" : m.reason ? m.reason.charAt(0).toUpperCase() + m.reason.slice(1) : "Not set up"}
                    </p>
                  </div>
                </li>
              ))}
            </ul>
          </>
        )}
      </div>
    </section>
  );
}
