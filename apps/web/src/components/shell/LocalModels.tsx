"use client";

import { useQuery } from "@tanstack/react-query";
import { CheckCircle2, CircleDashed } from "lucide-react";
import { api, unwrap } from "@/lib/api";
import { useHealth } from "@/lib/queries";
import { ModelStorage } from "@/components/shell/ModelStorage";

const SETUP = "uv run python scripts/setup_local.py";

/** Settings › Local models: your own ComfyUI, the free models it can run, and where it keeps them. */
export function LocalSettings() {
  const engine = useHealth().data?.local_engine;
  return (
    <>
      {!engine ? null : !engine.configured ? (
        <section className="space-y-4" aria-labelledby="local-models-title">
          <h2 id="local-models-title" className="type-title text-xl">Free models on your GPU</h2>
          <p className="rounded-[var(--radius-panel)] bg-panel p-4 text-xs leading-relaxed text-muted">
            Open models can run free on your own ComfyUI. Set COMFYUI_URL in .env to its address
            (http://127.0.0.1:8188 on this computer) and restart Opsis; the README&rsquo;s &ldquo;Open models on your
            GPU&rdquo; walks through it.
          </p>
        </section>
      ) : (
        <LocalModels online={engine.online} gpu={engine.gpu?.replace(/^cuda:\d+\s*/, "").split(" : ")[0] ?? null} />
      )}
      <ModelStorage />
    </>
  );
}

/** Which free models your own ComfyUI can run now, and what each of the others still needs. */
function LocalModels({ online, gpu }: { online: boolean; gpu: string | null }) {
  const query = useQuery({
    queryKey: ["local-models"],
    queryFn: () => unwrap(api.GET("/local-engine/models")),
    refetchInterval: 30_000,
    enabled: online,
  });
  const ready = query.data?.filter((m) => m.ready).length ?? 0;

  return (
    <section className="space-y-4" aria-labelledby="local-models-title">
      <div>
        <h2 id="local-models-title" className="type-title text-xl">Free models on your GPU</h2>
        <p className="mt-1 text-xs text-muted">
          {online ? `ComfyUI is running${gpu ? ` on ${gpu}` : ""}.` : "ComfyUI isn't running."}
        </p>
      </div>
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
