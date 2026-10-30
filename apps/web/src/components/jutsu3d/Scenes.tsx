"use client";

import { Box, Plus } from "lucide-react";
import dynamic from "next/dynamic";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";
import { mediaUrl } from "@/lib/api";
import { cn } from "@/lib/cn";
import { useCreateScene, useScenes } from "@/lib/jutsu3d";
import { Button, Dialog } from "@/components/ui/controls";
import { ConfirmDelete, PickableLink, reportRemoval, SelectButton, SelectionBar, useSelection } from "@/components/ui/Selection";
import { useDeleteProjects } from "@/lib/queries";
import { WHEN } from "@/components/runs/Runs";
import { GLASS_CARD, Lit } from "@/components/ui/Glass";

/** The editor draws with WebGL, so it only ever renders in the browser. */
export const SceneEditorPage = dynamic(() => import("./SceneEditor").then((m) => m.SceneEditor), {
  ssr: false, loading: () => <div className="p-6 text-sm text-muted">Loading the scene…</div>,
});

/** Your 3D blocking scenes: open one, or start a new one. */
export function Scenes() {
  const router = useRouter();
  const { data: scenes, isLoading } = useScenes();
  const create = useCreateScene();
  const [naming, setNaming] = useState(false);
  const [title, setTitle] = useState("");
  const selection = useSelection();
  const [confirming, setConfirming] = useState(false);
  const removal = useDeleteProjects();
  const picked = (scenes ?? []).filter((p) => selection.picked.has(p.id)).map((p) => p.id);

  function removePicked() {
    removal.mutate(picked, {
      onSuccess: (r) => {
        setConfirming(false);
        selection.stop();
        reportRemoval(r, ["scene", "scenes"]);
      },
    });
  }

  async function start() {
    try {
      const p = await create.mutateAsync(title.trim());
      router.push(`/jutsu3d/${p.id}`);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Could not create the scene");
    }
  }

  return (
    <div className="h-full overflow-y-auto p-6 scrollbar-quiet">
      <div className="mb-6 flex max-w-5xl items-end justify-between gap-4">
        <p className="max-w-xl text-sm leading-relaxed text-muted">
          Block a shot in 3D: place figures, props and lights, pose them, frame the camera and move it over time. Render
          the camera&rsquo;s view as pictures, depth or normals to guide image and video models.
        </p>
        <span className="flex gap-2">
          {!!scenes?.length && !selection.selecting && <SelectButton onClick={selection.start} />}
          <Button tone="primary" onClick={() => { setTitle(""); setNaming(true); }}>
            <Plus className="size-4" aria-hidden />
            New scene
          </Button>
        </span>
      </div>
      {selection.selecting && !!scenes?.length && (
        <div className="max-w-5xl">
          <SelectionBar count={picked.length} selectable={scenes.length} onAll={() => selection.set(scenes.map((p) => p.id))}
                        onNone={() => selection.set([])} onDelete={() => setConfirming(true)} onDone={selection.stop} />
        </div>
      )}
      {!isLoading && !scenes?.length && (
        <div className="flex max-w-5xl flex-col items-start gap-3 rounded-[var(--radius-panel)] bg-panel p-8">
          <Box className="size-6 text-tungsten" aria-hidden />
          <p className="text-sm text-chalk">No scenes yet.</p>
          <p className="text-xs text-muted">A new scene starts with a camera, a figure and the sun.</p>
        </div>
      )}
      <ul className="grid max-w-5xl grid-cols-[repeat(auto-fill,minmax(240px,1fr))] gap-4">
        {scenes?.map((p) => (
          <li key={p.id}>
            <PickableLink href={`/jutsu3d/${p.id}`} selecting={selection.selecting} selected={selection.picked.has(p.id)}
                          onToggle={() => selection.toggle(p.id)} className={cn(GLASS_CARD, "flex flex-col")}>
              <Lit picture={p.cover ? mediaUrl(p.cover.thumb_url ?? p.cover.url) : undefined}>
                <span className="block aspect-video w-full overflow-hidden rounded-[var(--radius-frame)] bg-bay">
                  {p.cover && (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img src={mediaUrl(p.cover.thumb_url ?? p.cover.url)} alt="" className="size-full object-cover" />
                  )}
                </span>
              </Lit>
              <span className="flex flex-col gap-1 px-1 pt-2.5 pb-1">
                <span className="type-title text-sm">{p.title}</span>
                <span className="tabular text-2xs text-faint">
                  {p.objects} object{p.objects === 1 ? "" : "s"}. Edited {WHEN.format(new Date(p.updated_at))}
                </span>
              </span>
            </PickableLink>
          </li>
        ))}
      </ul>
      <ConfirmDelete open={confirming} count={picked.length} noun={["scene", "scenes"]} pending={removal.isPending}
                     onConfirm={removePicked} onClose={() => setConfirming(false)}>
        Deleted scenes leave this list. Their takes and files stay in your history and library, where you can delete
        them once no other scene uses them.
      </ConfirmDelete>
      <Dialog open={naming} onClose={() => setNaming(false)} title="New scene" footer={(
        <>
          <Button tone="ghost" onClick={() => setNaming(false)}>Cancel</Button>
          <Button tone="primary" onClick={() => void start()} disabled={!title.trim() || create.isPending}>Create</Button>
        </>
      )}>
        <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="What the shot is" aria-label="Title" autoFocus
               onKeyDown={(e) => e.key === "Enter" && title.trim() && void start()}
               className="h-10 w-full rounded-[var(--radius-control)] border border-line-soft bg-bay px-3 text-sm text-chalk outline-none focus:border-tungsten/60" />
      </Dialog>
    </div>
  );
}
