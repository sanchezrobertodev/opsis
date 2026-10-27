"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { LoaderCircle, RefreshCw } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { Button, Field } from "@/components/ui/controls";
import { api, unwrap } from "@/lib/api";

export function ModelStorage() {
  const qc = useQueryClient();
  const [draft, setDraft] = useState<string | null>(null);
  const query = useQuery({
    queryKey: ["model-storage"],
    queryFn: () => unwrap(api.GET("/local-engine/model-storage")),
    refetchInterval: 15_000,
  });
  const save = useMutation({
    mutationFn: (directory: string) => unwrap(api.PATCH("/local-engine/model-storage", { body: { directory } })),
    onSuccess: (data) => {
      qc.setQueryData(["model-storage"], data);
      setDraft(null);
      toast.success("Model folder saved. Restart ComfyUI to apply it.");
    },
  });
  const data = query.data;
  const directory = draft ?? data?.directory ?? "";

  return (
    <section className="space-y-5 lg:col-span-2" aria-labelledby="model-storage-title">
      <div className="flex items-center justify-between gap-3">
        <h2 id="model-storage-title" className="type-title text-xl">Local model storage</h2>
        <Button size="sm" tone="ghost" aria-label="Refresh model storage" disabled={query.isFetching}
          onClick={() => void query.refetch()}>
          <RefreshCw className="size-3.5" /> Refresh
        </Button>
      </div>
      <div className="space-y-4 rounded-[var(--radius-panel)] bg-panel p-4">
        <p className="text-xs leading-relaxed text-muted">
          Choose where ComfyUI stores new models on the computer running AI Media Studio’s server.
          Existing model libraries remain available when you change this folder.
        </p>
        {query.isPending && <p className="text-sm text-muted">Loading model storage…</p>}
        {query.error && <p role="alert" className="text-sm text-fault">{query.error.message}</p>}
        {data && !data.available && <p role="status" className="text-sm text-muted">{data.message}</p>}
        {data?.available && (
          <form className="space-y-4" onSubmit={(event) => { event.preventDefault(); save.mutate(directory.trim()); }}>
            <Field label="Model download folder" htmlFor="model-download-folder"
              hint="Enter a full folder path. Model-type subfolders are created automatically. Existing files are not moved.">
              <input id="model-download-folder" type="text" value={directory} spellCheck={false}
                autoComplete="off" placeholder="G:\AI\Models\ComfyUI" disabled={save.isPending}
                onChange={(event) => { setDraft(event.target.value); save.reset(); }}
                className="h-10 w-full rounded-[var(--radius-control)] border border-line bg-bay px-3 text-sm outline-none focus:border-tungsten" />
            </Field>
            <div className="flex flex-wrap items-center gap-4">
              <Button type="submit" tone="primary" disabled={save.isPending || !directory.trim() || directory.trim() === data.directory}>
                {save.isPending && <LoaderCircle className="size-4 animate-spin" />}
                Save model folder
              </Button>
              {data.free_bytes != null && <p className="tabular text-xs text-muted">
                {(data.free_bytes / 1024 ** 3).toFixed(1)} GB free on the saved folder’s drive
              </p>}
            </div>
            {save.error && <p role="alert" className="text-sm text-fault">{save.error.message}</p>}
            <p role="status" className="text-xs leading-relaxed text-muted">
              {data.restart_required === true
                ? "Saved. Restart ComfyUI to use this folder. Running jobs are unaffected."
                : data.restart_required === false
                  ? "ComfyUI is using this folder."
                  : "The active folder could not be checked. This setting applies when ComfyUI starts."}
            </p>
            {data.active_directory && data.restart_required && <p className="break-all text-xs text-muted">
              Currently active: {data.active_directory}
            </p>}
            {(data.libraries ?? []).length > 0 && <div className="text-xs text-muted">
              <p>Additional model libraries</p>
              <ul className="mt-2 space-y-1">{(data.libraries ?? []).map((path) => <li key={path} className="break-all">{path}</li>)}</ul>
            </div>}
            <p className="text-2xs leading-relaxed text-faint">
              Applies to downloads that use ComfyUI’s model folders. Browser downloads use your browser’s Save As location;
              some extensions manage their own caches.
            </p>
          </form>
        )}
      </div>
    </section>
  );
}
