"use client";

import { useQueryClient } from "@tanstack/react-query";
import { Camera, Clapperboard, Globe2, LoaderCircle, MapPin, Orbit } from "lucide-react";
import dynamic from "next/dynamic";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useId, useRef, useState } from "react";
import { toast } from "sonner";
import { type Asset, isActive, mediaUrl } from "@/lib/api";
import { cn } from "@/lib/cn";
import type { SlotAsset } from "@/lib/draft";
import { useCreateElement } from "@/lib/elements";
import { uploadFile, useAssets, useGeneration, useRemove } from "@/lib/queries";
import { canvasPicture, saveThumbnail } from "@/lib/thumbs";
import { useFilmWorld, useWorldParts } from "@/lib/worlds";
import { QuickGenerate } from "@/components/create/QuickGenerate";
import { Button, Dialog, Field, Segmented } from "@/components/ui/controls";
import { ConfirmDelete, reportRemoval, SelectButton, SelectionBar, SelectMark, useSelection } from "@/components/ui/Selection";
import type { ViewerHandle } from "./Viewers";
import { ZoomButton } from "@/components/ui/PictureViewer";

// WebGL only exists in the browser
const WorldViewer = dynamic(() => import("./Viewers").then((m) => m.WorldViewer), { ssr: false });
const PanoViewer = dynamic(() => import("./Viewers").then((m) => m.PanoViewer), { ssr: false });

type Make = "world" | "panorama";
type Open = { kind: "world" | "pano"; asset: Asset };

const asSlot = (a: Asset): SlotAsset => ({ id: a.id, kind: a.kind, thumb: a.thumb_url, url: a.url, title: a.title });

/** World Creation: 360 panoramas and explorable splat worlds, views grabbed from them, and locations to reuse. */
export function Worlds() {
  const qc = useQueryClient();
  const [make, setMake] = useState<Make>("world");
  const [seed, setSeed] = useState<{ key: number; media: Record<string, SlotAsset[]> }>({ key: 0, media: {} });
  const [pending, setPending] = useState<string[]>([]);
  const [open, setOpen] = useState<Open | null>(null);
  const worlds = useAssets({ kind: "splat" });
  const panos = useAssets({ kind: "image", projection: "equirectangular" });
  const selection = useSelection();
  const [confirming, setConfirming] = useState(false);
  const removal = useRemove();
  const shown = [...(worlds.data?.items ?? []), ...(panos.data?.items ?? [])];
  const picked = shown.filter((a) => selection.picked.has(a.id));

  function removePicked() {
    // a world goes with what its take made (its panorama and collision mesh); a panorama alone
    const takes = picked.filter((a) => a.kind === "splat" && a.generation_id).map((a) => a.generation_id!);
    const files = picked.filter((a) => !(a.generation_id && takes.includes(a.generation_id))).map((a) => a.id);
    removal.mutate({ generations: takes, assets: files }, {
      onSuccess: (r) => {
        setConfirming(false);
        selection.stop();
        if (open && picked.some((a) => a.id === open.asset.id)) setOpen(null);
        reportRemoval(r, ["world or panorama", "worlds and panoramas"]);
      },
    });
  }

  const settled = useCallback((genId: string, made: Asset | null) => {
    setPending((p) => p.filter((id) => id !== genId));
    void qc.invalidateQueries({ queryKey: ["assets"] });
    if (made) setOpen({ kind: made.kind === "splat" ? "world" : "pano", asset: made });
  }, [qc]);

  function worldFrom(pano: Asset) {
    setMake("world");
    setSeed((s) => ({ key: s.key + 1, media: { input_image: [asSlot(pano)] } }));
  }

  return (
    <div className="grid h-full min-h-0 grid-cols-1 lg:grid-cols-[340px_minmax(0,1fr)]">
      <aside className="flex min-h-0 flex-col gap-6 overflow-y-auto border-b border-line-soft p-5 scrollbar-quiet lg:border-b-0 lg:border-r">
        <section className="flex flex-col gap-4" aria-label="Make">
          <Segmented label="Make" value={make} options={["world", "panorama"] as const} onChange={setMake}
                     render={(m) => (m === "world" ? "A world" : "A 360 panorama")} />
          <p className="text-2xs leading-relaxed text-faint">
            {make === "world"
              ? "An explorable 3D world from a description, a picture or panorama, several pictures, or a short video of a place. Marble takes about five minutes; Draft about one."
              : "A full 360 view of a place, from a picture of it and a description of what's around."}
          </p>
          <QuickGenerate key={`${make}-${seed.key}`} op={make === "world" ? "world" : "panorama"} media={seed.media}
                         submitLabel={make === "world" ? "Make the world" : "Make the panorama"}
                         promptPlaceholder={make === "world" ? "A quiet stone courtyard at dusk, lanterns on the walls, an old fig tree"
                           : "Around it: a misty harbour, fishing boats at the quay, a lighthouse on the breakwater"}
                         onStarted={(g) => setPending((p) => [g.id, ...p])} />
          {pending.map((id) => <Pending key={id} id={id} onSettled={settled} />)}
        </section>

        {shown.length > 0 && (selection.selecting ? (
          <SelectionBar count={picked.length} selectable={shown.length} onAll={() => selection.set(shown.map((a) => a.id))}
                        onNone={() => selection.set([])} onDelete={() => setConfirming(true)} onDone={selection.stop} />
        ) : (
          <div><SelectButton onClick={selection.start} /></div>
        ))}
        <Shelf title="Worlds" icon={Globe2} items={worlds.data?.items ?? []} loading={worlds.isLoading} open={open}
               kind="world" onOpen={setOpen} empty="Your worlds appear here." selection={selection} />
        <Shelf title="Panoramas" icon={Orbit} items={panos.data?.items ?? []} loading={panos.isLoading} open={open}
               kind="pano" onOpen={setOpen} empty="Your 360 panoramas appear here." selection={selection} />
        <ConfirmDelete open={confirming} count={picked.length} noun={["world or panorama", "worlds and panoramas"]}
                       pending={removal.isPending} onConfirm={removePicked} onClose={() => setConfirming(false)}>
          They&apos;re deleted from disk; this can&apos;t be undone. A world goes with its panorama and collision mesh.
          Anything a location, a film or a 3D scene uses is kept.
        </ConfirmDelete>
      </aside>

      <main className="flex min-h-0 flex-col">
        {open ? <Stage key={open.asset.id} open={open} onWorldFrom={worldFrom} /> : (
          <div className="grid flex-1 place-items-center p-8">
            <div className="flex max-w-md flex-col items-start gap-3 text-sm text-muted">
              <Globe2 className="size-6 text-tungsten" aria-hidden />
              <p className="text-chalk">Make a place once, shoot it from anywhere.</p>
              <p>
                Open a world to walk around it and grab views as start frames, save it as a location for @mentions, or
                set a 3D blocking scene in it to move a camera through it.
              </p>
            </div>
          </div>
        )}
      </main>
    </div>
  );
}

function Pending({ id, onSettled }: { id: string; onSettled: (genId: string, made: Asset | null) => void }) {
  const { data: gen } = useGeneration(id);
  const done = gen && !isActive(gen.status);
  useEffect(() => {
    if (!gen || isActive(gen.status)) return;
    if (gen.status !== "succeeded") toast.error(gen.error_message ?? "It wasn't made.");
    onSettled(id, gen.assets.find((a) => a.kind === "splat") ?? gen.assets.find((a) => a.kind === "image") ?? null);
  }, [gen, id, onSettled]);
  if (done) return null;
  return (
    <p className="flex items-center gap-2 text-xs text-muted" aria-live="polite">
      <LoaderCircle className="size-3.5 animate-spin text-tungsten" aria-hidden />
      Making {gen?.output_type === "image" ? "the panorama" : "the world"}
      {gen?.progress ? ` (${Math.round(gen.progress * 100)}%)` : "…"}
    </p>
  );
}

function Shelf({ title, icon: Icon, items, loading, open, kind, onOpen, empty, selection }: {
  title: string; icon: typeof Globe2; items: Asset[]; loading: boolean; open: Open | null; kind: Open["kind"];
  onOpen: (o: Open) => void; empty: string; selection: ReturnType<typeof useSelection>;
}) {
  return (
    <section className="flex flex-col gap-2" aria-label={title}>
      <h2 className="text-xs text-muted">{title}</h2>
      {!loading && !items.length && <p className="text-2xs text-faint">{empty}</p>}
      <ul className="grid grid-cols-2 gap-2">
        {items.map((a) => (
          <li key={a.id}>
            <button type="button" aria-pressed={selection.selecting ? selection.picked.has(a.id) : open?.asset.id === a.id}
                    onClick={() => (selection.selecting ? selection.toggle(a.id) : onOpen({ kind, asset: a }))}
                    className={cn("relative flex w-full flex-col gap-1 rounded-[var(--radius-control)] border p-1 text-left",
                                  (selection.selecting ? selection.picked.has(a.id) : open?.asset.id === a.id)
                                    ? "border-tungsten/70 bg-raised" : "border-line-soft hover:border-tungsten/40")}>
              {selection.selecting && <span className="absolute left-2 top-2"><SelectMark selected={selection.picked.has(a.id)} /></span>}
              {a.thumb_url
                // eslint-disable-next-line @next/next/no-img-element
                ? <img src={mediaUrl(a.thumb_url)} alt="" className="aspect-video w-full rounded-[3px] bg-black object-cover" />
                : <span className="grid aspect-video w-full place-items-center rounded-[3px] bg-black/40"><Icon className="size-5 text-faint" aria-hidden /></span>}
              <span className="truncate px-0.5 text-2xs text-muted">{a.title || (kind === "world" ? "World" : "Panorama")}</span>
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}

/** The open world or panorama, with what you can do from where you're looking. */
function Stage({ open, onWorldFrom }: { open: Open; onWorldFrom: (pano: Asset) => void }) {
  const router = useRouter();
  const qc = useQueryClient();
  const handle = useRef<ViewerHandle | null>(null);
  const { data: parts } = useWorldParts(open.kind === "world" ? open.asset : null);
  const film = useFilmWorld();
  const [grabs, setGrabs] = useState<Asset[]>([]);
  const [grabbing, setGrabbing] = useState(false);
  const [ready, setReady] = useState(false); // the viewer is drawing, so there's a view to grab
  const [naming, setNaming] = useState(false);
  const name = open.asset.title || (open.kind === "world" ? "World" : "Panorama");
  const url = mediaUrl(open.asset.url) ?? open.asset.url;
  const pano = open.kind === "pano" ? open.asset : parts?.pano ?? null;

  // the library can't draw a splat, so the first look at a world becomes its picture
  const asset = open.asset;
  const onReady = useCallback(() => {
    setReady(true);
    if (open.kind !== "world" || asset.thumb_url) return;
    // splats stream in and sort over a few frames; wait, then draw a fresh frame to keep
    window.setTimeout(() => {
      const h = handle.current;
      if (!h) return;
      h.render();
      void canvasPicture(h.canvas, 640, "image/webp").then((b) => saveThumbnail(asset.id, b))
        .then(() => qc.invalidateQueries({ queryKey: ["assets"] })).catch(() => undefined);
    }, 2500);
  }, [asset, open.kind, qc]);

  async function grab(): Promise<Asset | null> {
    const h = handle.current;
    if (!h) return null;
    setGrabbing(true);
    try {
      h.render();
      const picture = await canvasPicture(h.canvas);
      const made = await uploadFile(new File([picture], `${name} view ${grabs.length + 1}.png`, { type: "image/png" }));
      setGrabs((g) => [made, ...g]);
      void qc.invalidateQueries({ queryKey: ["assets"] });
      return made;
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Couldn't grab the view");
      return null;
    } finally {
      setGrabbing(false);
    }
  }

  async function shoot() {
    try {
      const p = await film.mutateAsync({ splat: open.asset, collider: parts?.collider ?? null, title: `In ${name}` });
      router.push(`/jutsu3d/${p.id}`);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Couldn't make the scene");
    }
  }

  return (
    <>
      <div className="flex flex-wrap items-center gap-2 border-b border-line-soft px-4 py-2.5">
        <h1 className="type-title mr-auto min-w-0 truncate text-lg">{name}</h1>
        <Button size="sm" onClick={() => void grab()} disabled={grabbing || !ready}>
          {grabbing ? <LoaderCircle className="size-4 animate-spin" aria-hidden /> : <Camera className="size-4" aria-hidden />} Grab this view
        </Button>
        <Button size="sm" onClick={() => setNaming(true)} disabled={!ready}><MapPin className="size-4" aria-hidden /> Save as a location</Button>
        {open.kind === "world" ? (
          <Button size="sm" tone="primary" onClick={() => void shoot()} disabled={film.isPending}>
            <Clapperboard className="size-4" aria-hidden /> Film it in 3D blocking
          </Button>
        ) : (
          <Button size="sm" tone="primary" onClick={() => onWorldFrom(open.asset)}>
            <Globe2 className="size-4" aria-hidden /> Make a world from it
          </Button>
        )}
      </div>
      <div className="relative min-h-0 flex-1 bg-black">
        {open.kind === "world"
          ? <WorldViewer url={url} handleRef={handle} onReady={onReady} />
          : <PanoViewer url={url} handleRef={handle} onReady={onReady} />}
        <p className="pointer-events-none absolute bottom-3 left-4 text-2xs text-faint">
          Drag to look around{open.kind === "world" ? ", right-drag to move" : ""}, scroll to zoom.
        </p>
      </div>
      {grabs.length > 0 && (
        <ul className="flex gap-2 overflow-x-auto border-t border-line-soft p-3 scrollbar-quiet" aria-label="Grabbed views">
          {grabs.map((g) => (
            <li key={g.id} className="group relative w-40 shrink-0">
              <ZoomButton src={mediaUrl(g.url)} title={g.title ?? "A grabbed view"} className="right-1 top-1" />
              <Link href={`/library?asset=${g.id}`} className="flex flex-col gap-1 text-2xs text-muted hover:text-chalk">
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img src={mediaUrl(g.url)} alt={g.title ?? "A grabbed view"} className="aspect-video w-full rounded-[3px] bg-black object-cover" />
                {g.title}
              </Link>
            </li>
          ))}
        </ul>
      )}
      <SaveLocation open={naming} onClose={() => setNaming(false)} defaultName={name} grabs={grabs} pano={pano} grab={grab} />
    </>
  );
}

function SaveLocation({ open, onClose, defaultName, grabs, pano, grab }: {
  open: boolean; onClose: () => void; defaultName: string; grabs: Asset[]; pano: Asset | null;
  grab: () => Promise<Asset | null>;
}) {
  const create = useCreateElement();
  const ids = { name: useId(), about: useId() };
  // a location's name is short, like its @mention: the first phrase of the world's description
  const [name, setName] = useState(() => defaultName.split(/[,.;:]/)[0].trim().slice(0, 60) || defaultName.slice(0, 60));
  const [description, setDescription] = useState("");
  async function save() {
    // the views you grabbed show the place as a camera sees it; with none, the current view stands in
    const views = grabs.length ? grabs : [await grab()].filter((a): a is Asset => Boolean(a));
    const images = [...views.slice(0, 5).map((a) => ({ asset_id: a.id, view: "wide" as const })),
                    ...(pano ? [{ asset_id: pano.id, view: "detail" as const }] : [])];
    try {
      const el = await create.mutateAsync({ kind: "location", name: name.trim(), description: description.trim(), attributes: {},
                                            images });
      toast(`Saved. Mention it as @${el.slug} in any prompt.`);
      onClose();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Couldn't save the location");
    }
  }
  return (
    <Dialog open={open} onClose={onClose} title="Save as a location"
            footer={<>
              <Button onClick={onClose}>Cancel</Button>
              <Button tone="primary" onClick={() => void save()} disabled={!name.trim() || create.isPending}>Save location</Button>
            </>}>
      <div className="flex flex-col gap-4">
        <p>
          {grabs.length ? `The ${grabs.length} view${grabs.length > 1 ? "s" : ""} you grabbed` : "The current view"}
          {pano ? " and the 360 panorama" : ""} become the location&apos;s pictures.
        </p>
        <Field label="Name" htmlFor={ids.name}>
          <input id={ids.name} value={name} onChange={(e) => setName(e.target.value)} maxLength={80}
                 className="h-10 rounded-[var(--radius-control)] border border-line-soft bg-bay px-3 text-sm text-chalk outline-none focus:border-tungsten/60" />
        </Field>
        <Field label="What it's like (optional)" htmlFor={ids.about}>
          <textarea id={ids.about} value={description} onChange={(e) => setDescription(e.target.value)} rows={2} maxLength={600}
                    placeholder="A stone courtyard at dusk, lanterns, an old fig tree"
                    className="resize-y rounded-[var(--radius-control)] border border-line-soft bg-bay px-3 py-2 text-sm text-chalk outline-none placeholder:text-faint focus:border-tungsten/60" />
        </Field>
      </div>
    </Dialog>
  );
}
