"use client";

import { Copy, Plus, Sparkles, Trash2 } from "lucide-react";
import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";
import { toast } from "sonner";
import { type MediaSlot, mediaUrl } from "@/lib/api";
import { cn } from "@/lib/cn";
import type { SlotAsset } from "@/lib/draft";
import { useDraft } from "@/lib/draft";
import {
  type Element,
  type ElementKind,
  KIND_LABEL,
  useAddElementImages,
  useCreateElement,
  useDeleteElement,
  useElements,
  usePatchElement,
  useRemoveElementImage,
} from "@/lib/elements";
import { MediaSlotField } from "@/components/create/MediaSlots";
import { Button, Dialog, Segmented } from "@/components/ui/controls";

const FILTERS = ["all", "character", "location", "prop", "style"] as const;
type Filter = (typeof FILTERS)[number];

const REF_SLOT: MediaSlot = { role: "image_references", kinds: ["image"], label: "Reference images", max: 8, min: 0, required: false };

export function Characters() {
  const [filter, setFilter] = useState<Filter>("all");
  const [q, setQ] = useState("");
  const [editing, setEditing] = useState<Element | "new" | null>(null);
  const [building, setBuilding] = useState(false);
  const { data, isLoading } = useElements(filter === "all" ? undefined : filter, q);
  const items = data ?? [];

  return (
    <div className="h-full overflow-y-auto p-6 scrollbar-quiet">
      <div className="mb-6 flex flex-wrap items-center gap-3">
        <div className="max-w-lg flex-1">
          <Segmented
            label="Show"
            value={filter}
            options={FILTERS}
            render={(f) => (f === "all" ? "Everything" : `${KIND_LABEL[f]}s`)}
            onChange={setFilter}
          />
        </div>
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Search by name"
          aria-label="Search characters"
          className="h-9 w-56 rounded-[var(--radius-control)] border border-line-soft bg-bay px-3 text-sm outline-none placeholder:text-faint focus:border-tungsten/60"
        />
        <div className="ml-auto flex gap-2">
          <Button tone="quiet" size="sm" onClick={() => setBuilding(true)}>
            <Sparkles className="size-4" aria-hidden />
            Design a character
          </Button>
          <Button tone="primary" size="sm" onClick={() => setEditing("new")}>
            <Plus className="size-4" aria-hidden />
            New from images
          </Button>
        </div>
      </div>

      {isLoading ? null : items.length === 0 ? (
        <div className="mt-20 max-w-md">
          <p className="type-title text-xl">{q ? "No matches" : "No characters yet"}</p>
          <p className="mt-2 text-sm leading-relaxed text-muted">
            Save a person, place or prop once, then write <span className="text-chalk">@name</span> in any prompt.
            Models that take reference images get its pictures automatically; others get its description.
          </p>
        </div>
      ) : (
        <ul className="grid grid-cols-[repeat(auto-fill,minmax(190px,1fr))] gap-4">
          {items.map((e) => (
            <li key={e.id}>
              <button
                type="button"
                onClick={() => setEditing(e)}
                className="group block w-full text-left"
                aria-label={`${e.name}, ${KIND_LABEL[e.kind]}`}
              >
                <div className="aspect-[3/4] overflow-hidden rounded-[var(--radius-frame)] bg-raised">
                  {e.cover ? (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img src={mediaUrl(e.cover.thumb_url ?? e.cover.url)} alt="" className="size-full object-cover transition-transform group-hover:scale-[1.02]" />
                  ) : (
                    <span className="grid size-full place-items-center text-xs text-faint">No images yet</span>
                  )}
                </div>
                <p className="type-title mt-2 truncate text-[15px]">{e.name}</p>
                <p className="truncate text-xs text-faint">
                  <span className="text-tungsten">{e.handle}</span>, {KIND_LABEL[e.kind].toLowerCase()}, {e.images.length} image
                  {e.images.length === 1 ? "" : "s"}
                </p>
              </button>
            </li>
          ))}
        </ul>
      )}

      <ElementDialog element={editing} onClose={() => setEditing(null)} />
      <TraitBuilder open={building} onClose={() => setBuilding(false)} />
    </div>
  );
}

function ElementDialog({ element, onClose }: { element: Element | "new" | null; onClose: () => void }) {
  const isNew = element === "new";
  const existing = element && element !== "new" ? element : null;
  return (
    <Dialog open={element !== null} onClose={onClose} title={isNew ? "New character, location or prop" : existing?.name ?? ""} wide>
      {element !== null && <ElementForm key={existing?.id ?? "new"} existing={existing} onDone={onClose} />}
    </Dialog>
  );
}

function ElementForm({ existing, onDone }: { existing: Element | null; onDone: () => void }) {
  const router = useRouter();
  const setDraft = useDraft((s) => s.set);
  const create = useCreateElement();
  const patch = usePatchElement();
  const remove = useDeleteElement();
  const addImages = useAddElementImages();
  const removeImage = useRemoveElementImage();
  const [name, setName] = useState(existing?.name ?? "");
  const [kind, setKind] = useState<ElementKind>(existing?.kind ?? "character");
  const [description, setDescription] = useState(existing?.description ?? "");
  const [pending, setPending] = useState<SlotAsset[]>([]);

  const images: SlotAsset[] = useMemo(
    () => [
      ...(existing?.images ?? []).map((i) => ({ id: i.asset.id, kind: "image", thumb: i.asset.thumb_url, url: i.asset.url })),
      ...pending,
    ],
    [existing, pending],
  );

  async function save() {
    try {
      if (existing) {
        await patch.mutateAsync({ id: existing.id, name, kind, description });
        if (pending.length) {
          await addImages.mutateAsync({ id: existing.id, images: pending.map((p) => ({ asset_id: p.id, view: "front" as const })) });
        }
        toast(`Saved ${name}`);
      } else {
        const e = await create.mutateAsync({
          name, kind, description, attributes: {},
          images: pending.map((p) => ({ asset_id: p.id, view: "front" as const })),
        });
        toast(`Created ${e.handle}. Use it in any prompt.`);
      }
      onDone();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Could not save");
    }
  }

  function characterSheet() {
    if (!existing) return;
    setDraft("image", {
      modelId: "nano-banana-2",
      mode: "edit",
      prompt:
        `Character reference sheet of @${existing.slug}: front, three-quarter, side and back views plus a close-up of ` +
        "the face, full body, neutral standing pose, plain light grey studio background, same outfit and features in every view",
      takes: 1,
    });
    onDone();
    router.push("/create/image");
  }

  return (
    <div className="grid gap-6 md:grid-cols-[1fr_1.2fr]">
      <div className="space-y-4">
        <label className="flex flex-col gap-1.5 text-xs text-muted">
          Name
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            maxLength={80}
            placeholder="e.g. Maya Ortiz"
            className="h-10 rounded-[var(--radius-control)] border border-line-soft bg-bay px-3 text-sm text-chalk outline-none placeholder:text-faint focus:border-tungsten/60"
          />
        </label>
        <div className="flex flex-col gap-1.5 text-xs text-muted">
          Type
          <Segmented label="Type" value={kind} options={["character", "location", "prop", "style"] as const}
                     render={(k) => KIND_LABEL[k]} onChange={setKind} />
        </div>
        <label className="flex flex-col gap-1.5 text-xs text-muted">
          Description
          <textarea
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            maxLength={600}
            rows={4}
            placeholder="What makes them recognisable: age, build, hair, outfit, signature details."
            className="resize-y rounded-[var(--radius-control)] border border-line-soft bg-bay px-3 py-2 text-sm leading-relaxed text-chalk outline-none placeholder:text-faint focus:border-tungsten/60"
          />
          <span className="text-2xs text-faint">Models that can’t take images get this text instead. Keep it short.</span>
        </label>
        {existing && (
          <p className="flex items-center gap-2 text-xs text-muted">
            Write <span className="text-tungsten">{existing.handle}</span> in a prompt to use it.
            <button
              type="button"
              onClick={() => void navigator.clipboard.writeText(existing.handle).then(() => toast("Copied"))}
              aria-label="Copy handle"
              className="grid size-7 place-items-center rounded-full hover:bg-raised"
            >
              <Copy className="size-3.5" aria-hidden />
            </button>
          </p>
        )}
      </div>

      <div className="space-y-4">
        <MediaSlotField
          slot={REF_SLOT}
          value={images}
          onAdd={(a) => setPending((p) => (p.some((x) => x.id === a.id) || images.some((x) => x.id === a.id) ? p : [...p, a]))}
          onRemove={(id) => {
            if (pending.some((p) => p.id === id)) setPending((p) => p.filter((x) => x.id !== id));
            else if (existing) removeImage.mutate({ id: existing.id, assetId: id });
          }}
        />
        <p className="text-2xs text-faint">
          Clear, well-lit images work best: a front view first, then other angles. The first image is the cover.
        </p>
      </div>

      <div className="flex flex-wrap items-center gap-2 border-t border-line-soft pt-4 md:col-span-2">
        {existing && existing.kind === "character" && (
          <Button type="button" size="sm" onClick={characterSheet} disabled={!existing.images.length}>
            Make a character sheet
          </Button>
        )}
        {existing && (
          <Button
            type="button"
            size="sm"
            tone="danger"
            onClick={() => remove.mutate(existing.id, { onSuccess: () => { toast(`Deleted ${existing.name}`); onDone(); } })}
          >
            <Trash2 className="size-4" aria-hidden />
            Delete
          </Button>
        )}
        <Button type="button" tone="primary" className="ml-auto" onClick={() => void save()}
                disabled={!name.trim() || create.isPending || patch.isPending}>
          {existing ? "Save changes" : "Create"}
        </Button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- trait builder

const TRAITS: { key: string; label: string; options: string[] }[] = [
  { key: "style", label: "Style", options: ["photoreal", "cinematic film still", "anime", "stylized 3D animation", "graphic novel"] },
  { key: "age", label: "Age", options: ["child", "teenager", "in their twenties", "in their thirties", "middle-aged", "elderly"] },
  { key: "presentation", label: "Presentation", options: ["woman", "man", "androgynous person", "non-human creature"] },
  { key: "build", label: "Build", options: ["slight", "athletic", "average", "broad", "heavyset", "very tall"] },
  { key: "hair", label: "Hair", options: ["short curly", "long straight", "shaved", "braided", "messy shoulder-length", "slicked back", "bald"] },
  { key: "hair_color", label: "Hair color", options: ["black", "dark brown", "auburn", "blonde", "silver", "dyed blue", "bright red"] },
  { key: "eyes", label: "Eyes", options: ["brown", "hazel", "green", "blue", "grey", "amber"] },
  { key: "outfit", label: "Outfit", options: ["worn leather jacket", "tailored suit", "streetwear hoodie", "flowing robe", "spacesuit", "work overalls", "evening dress"] },
  { key: "detail", label: "Signature detail", options: ["scar across one eyebrow", "freckles", "round glasses", "facial tattoo", "eye patch", "silver earrings", "missing tooth"] },
  { key: "archetype", label: "Archetype", options: ["hero", "rebel", "mentor", "trickster", "villain", "everyday person"] },
];

function describe(traits: Record<string, string>, heritage: string): string {
  const who = [traits.age && `${traits.age}`, heritage, traits.presentation].filter(Boolean).join(" ");
  const parts = [
    who && `a ${who}`,
    traits.build && `${traits.build} build`,
    (traits.hair || traits.hair_color) && `${[traits.hair, traits.hair_color].filter(Boolean).join(" ")} hair`,
    traits.eyes && `${traits.eyes} eyes`,
    traits.outfit && `wearing a ${traits.outfit}`,
    traits.detail,
    traits.archetype && `the look of a ${traits.archetype}`,
  ].filter(Boolean);
  return parts.join(", ");
}

function TraitBuilder({ open, onClose }: { open: boolean; onClose: () => void }) {
  const router = useRouter();
  const setDraft = useDraft((s) => s.set);
  const [traits, setTraits] = useState<Record<string, string>>({});
  const [heritage, setHeritage] = useState("");
  const description = describe(traits, heritage.trim());
  const style = traits.style ?? "photoreal";

  function randomize() {
    const next: Record<string, string> = {};
    for (const t of TRAITS) next[t.key] = t.options[Math.floor(Math.random() * t.options.length)];
    setTraits(next);
  }

  function generate() {
    setDraft("image", {
      prompt: `${style} character portrait of ${description}. Waist-up, facing the camera, soft studio light, plain background, sharp focus on the face.`,
      takes: 2,
      mode: "t2i",
    });
    onClose();
    router.push("/create/image");
    toast("Pick the take you like, then use “Save as character” on it.");
  }

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="Design a character"
      wide
      footer={
        <>
          <Button type="button" tone="ghost" onClick={randomize}>
            Randomize
          </Button>
          <Button type="button" tone="primary" onClick={generate} disabled={!description}>
            Create portraits
          </Button>
        </>
      }
    >
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
        {TRAITS.map((t) => (
          <label key={t.key} className="flex flex-col gap-1 text-2xs text-faint">
            {t.label}
            <select
              value={traits[t.key] ?? ""}
              onChange={(e) => setTraits({ ...traits, [t.key]: e.target.value })}
              className="h-9 rounded-[var(--radius-control)] border border-line-soft bg-bay px-2 text-xs text-chalk outline-none focus:border-tungsten/60"
            >
              <option value="">Any</option>
              {t.options.map((o) => (
                <option key={o} value={o}>
                  {o}
                </option>
              ))}
            </select>
          </label>
        ))}
        <label className="flex flex-col gap-1 text-2xs text-faint">
          Heritage (optional)
          <input
            value={heritage}
            onChange={(e) => setHeritage(e.target.value)}
            placeholder="e.g. Korean, Nigerian"
            className="h-9 rounded-[var(--radius-control)] border border-line-soft bg-bay px-2 text-xs text-chalk outline-none placeholder:text-faint focus:border-tungsten/60"
          />
        </label>
      </div>
      <p className={cn("mt-4 rounded-[var(--radius-control)] bg-bay p-3 text-sm leading-relaxed", description ? "text-chalk" : "text-faint")}>
        {description || "Pick a few traits, or press Randomize."}
      </p>
      <p className="mt-2 text-2xs text-faint">
        This opens Create image with two portrait takes. Save the one you like as a character; its description is
        filled in from these traits.
      </p>
    </Dialog>
  );
}
