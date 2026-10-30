"use client";

import { Clapperboard, Plus } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";
import { mediaUrl } from "@/lib/api";
import { useCreateProject, useProjects } from "@/lib/cinema";
import { Button, Dialog } from "@/components/ui/controls";
import { ConfirmDelete, PickableLink, reportRemoval, SelectButton, SelectionBar, useSelection } from "@/components/ui/Selection";
import { useDeleteProjects } from "@/lib/queries";
import { WHEN } from "@/components/runs/Runs";

/** Your films: open one, or start a new one from a title. */
export function CinemaProjects() {
  const router = useRouter();
  const { data: projects, isLoading } = useProjects();
  const create = useCreateProject();
  const [naming, setNaming] = useState(false);
  const [title, setTitle] = useState("");
  const selection = useSelection();
  const [confirming, setConfirming] = useState(false);
  const removal = useDeleteProjects();
  const picked = (projects ?? []).filter((p) => selection.picked.has(p.id)).map((p) => p.id);

  function removePicked() {
    removal.mutate(picked, {
      onSuccess: (r) => {
        setConfirming(false);
        selection.stop();
        reportRemoval(r, ["film", "films"]);
      },
    });
  }

  async function start() {
    try {
      const p = await create.mutateAsync(title.trim());
      router.push(`/cinema/${p.id}`);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Could not create the film");
    }
  }

  return (
    <div className="h-full overflow-y-auto p-6 scrollbar-quiet">
      <div className="mb-6 flex max-w-5xl items-end justify-between gap-4">
        <p className="max-w-xl text-sm leading-relaxed text-muted">
          Write a script, let Claude break it into shots, pick an opening frame and the best take for each, and cut
          them into a film.
        </p>
        <span className="flex gap-2">
          {!!projects?.length && !selection.selecting && <SelectButton onClick={selection.start} />}
          <Button tone="primary" onClick={() => { setTitle(""); setNaming(true); }}>
            <Plus className="size-4" aria-hidden />
            New film
          </Button>
        </span>
      </div>
      {selection.selecting && !!projects?.length && (
        <div className="max-w-5xl">
          <SelectionBar count={picked.length} selectable={projects.length} onAll={() => selection.set(projects.map((p) => p.id))}
                        onNone={() => selection.set([])} onDelete={() => setConfirming(true)} onDone={selection.stop} />
        </div>
      )}
      {!isLoading && !projects?.length && (
        <div className="flex max-w-5xl flex-col items-start gap-3 rounded-[var(--radius-panel)] bg-panel p-8">
          <Clapperboard className="size-6 text-tungsten" aria-hidden />
          <p className="text-sm text-chalk">No films yet.</p>
          <p className="text-xs text-muted">Start one with a working title; you can change it later.</p>
        </div>
      )}
      <ul className="grid max-w-5xl grid-cols-[repeat(auto-fill,minmax(240px,1fr))] gap-4">
        {projects?.map((p) => (
          <li key={p.id}>
            <PickableLink href={`/cinema/${p.id}`} selecting={selection.selecting} selected={selection.picked.has(p.id)}
                          onToggle={() => selection.toggle(p.id)} className="group flex flex-col overflow-hidden rounded-[var(--radius-panel)] bg-panel hover:bg-raised">
              <span className="aspect-video w-full bg-bay">
                {p.cover && (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img src={mediaUrl(p.cover.thumb_url ?? p.cover.poster_url ?? p.cover.url)} alt="" className="size-full object-cover" />
                )}
              </span>
              <span className="flex flex-col gap-1 p-3">
                <span className="type-title text-sm">{p.title}</span>
                <span className="tabular text-2xs text-faint">
                  {p.scenes} scene{p.scenes === 1 ? "" : "s"}, {p.shots} shot{p.shots === 1 ? "" : "s"}
                  {p.shots > 0 && `, ${p.shots_with_takes} filmed`}. Edited {WHEN.format(new Date(p.updated_at))}
                </span>
              </span>
            </PickableLink>
          </li>
        ))}
      </ul>
      <ConfirmDelete open={confirming} count={picked.length} noun={["film", "films"]} pending={removal.isPending}
                     onConfirm={removePicked} onClose={() => setConfirming(false)}>
        Deleted films leave this list. Their takes and files stay in your history and library, where you can delete
        them once no other film uses them.
      </ConfirmDelete>
      <Dialog open={naming} onClose={() => setNaming(false)} title="New film" footer={(
        <>
          <Button tone="ghost" onClick={() => setNaming(false)}>Cancel</Button>
          <Button tone="primary" onClick={() => void start()} disabled={!title.trim() || create.isPending}>Create</Button>
        </>
      )}>
        <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Working title" aria-label="Title" autoFocus
               onKeyDown={(e) => e.key === "Enter" && title.trim() && void start()}
               className="h-10 w-full rounded-[var(--radius-control)] border border-line-soft bg-bay px-3 text-sm text-chalk outline-none focus:border-tungsten/60" />
      </Dialog>
    </div>
  );
}
