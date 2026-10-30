"use client";

import { AudioLines, Copy, Mic, Plus, Sparkles, Trash2 } from "lucide-react";
import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";
import { toast } from "sonner";
import { ApiError, type MediaSlot, mediaUrl } from "@/lib/api";
import { cn, usd } from "@/lib/cn";
import type { SlotAsset } from "@/lib/draft";
import { useDraft } from "@/lib/draft";
import { useDebounced } from "@/lib/hooks";
import { uploadFile } from "@/lib/queries";
import { STARTERS } from "@/lib/starters";
import { type RunCreate, useEstimateRun, useStartRun } from "@/lib/runs";
import {
  type Element,
  type ElementKind,
  KIND_LABEL,
  useAddElementImages,
  useCloneVoice,
  useCreateElement,
  useDeleteElement,
  useElements,
  usePatchElement,
  useRemoveElementImage,
} from "@/lib/elements";
import { MediaSlotField } from "@/components/create/MediaSlots";
import { VOICE_MODEL, VoicePicker } from "@/components/voice/VoicePicker";
import { useModelsFor } from "@/components/create/QuickGenerate";
import { Button, Dialog, Field, Segmented, Select } from "@/components/ui/controls";
import { OpensFullSize, ZoomButton } from "@/components/ui/PictureViewer";
import { GLASS_CARD, Lit } from "@/components/ui/Glass";

const FILTERS = ["all", "character", "location", "prop", "style", "voice"] as const;
type Filter = (typeof FILTERS)[number];

const REF_SLOT: MediaSlot = { role: "image_references", kinds: ["image"], label: "Reference images", max: 80, min: 0, required: false, auto: false };

export function Characters() {
  const [filter, setFilter] = useState<Filter>("all");
  const [q, setQ] = useState("");
  const [editing, setEditing] = useState<Element | "new" | null>(null);
  const [building, setBuilding] = useState(false);
  const [cloning, setCloning] = useState(false);
  const { data, isLoading } = useElements(filter === "all" ? undefined : filter, q);
  const items = data ?? [];
  const newKind: ElementKind | null =
    filter === "character" || filter === "location" || filter === "prop" || filter === "style" ? filter : null;
  const starters = STARTERS.filter((s) => s.kind === filter && !q && !items.some((e) => e.name === s.name));

  return (
    <div className="h-full overflow-y-auto p-6 scrollbar-quiet">
      <div className="mb-6 flex flex-wrap items-center gap-3">
        <div className="max-w-2xl flex-1">
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
          <Button tone="quiet" size="sm" onClick={() => setCloning(true)}>
            <Mic className="size-4" aria-hidden />
            Add a voice
          </Button>
          <Button tone="quiet" size="sm" onClick={() => setBuilding(true)}>
            <Sparkles className="size-4" aria-hidden />
            Design a character
          </Button>
          <Button tone="primary" size="sm" onClick={() => setEditing("new")}>
            <Plus className="size-4" aria-hidden />
            {newKind ? `New ${KIND_LABEL[newKind].toLowerCase()}` : "New from images"}
          </Button>
        </div>
      </div>
      {starters.length > 0 && <AddStarters starters={starters} />}

      {isLoading ? null : items.length === 0 ? (
        <div className="mt-20 max-w-md">
          <p className="type-title text-xl">{q ? "No matches" : newKind ? `No ${KIND_LABEL[newKind].toLowerCase()}s yet` : "No characters yet"}</p>
          <p className="mt-2 text-sm leading-relaxed text-muted">
            Save a person, place or prop once, then write <span className="text-chalk">@name</span> in any prompt.
            Models that take reference images get its pictures automatically; others get its description.
          </p>
        </div>
      ) : (
        <ul className="grid grid-cols-[repeat(auto-fill,minmax(190px,1fr))] gap-4">
          {items.map((e) => (
            <li key={e.id} className="group relative">
              {e.kind !== "voice" && e.cover && <ZoomButton src={mediaUrl(e.cover.url)} title={e.name} className="right-4 top-4" />}
              <button
                type="button"
                onClick={() => setEditing(e)}
                className={cn(GLASS_CARD, "block w-full text-left")}
                aria-label={`${e.name}, ${KIND_LABEL[e.kind]}`}
              >
                <Lit picture={e.kind !== "voice" && e.cover ? mediaUrl(e.cover.thumb_url ?? e.cover.url) : undefined}>
                  <div className="aspect-[3/4] overflow-hidden rounded-[var(--radius-frame)] bg-raised">
                    {e.kind === "voice" ? (
                      <span className="grid size-full place-items-center text-tungsten">
                        <AudioLines className="size-8" aria-hidden />
                      </span>
                    ) : e.cover ? (
                      // eslint-disable-next-line @next/next/no-img-element
                      <img src={mediaUrl(e.cover.thumb_url ?? e.cover.url)} alt="" className="size-full object-cover transition-transform group-hover:scale-[1.02]" />
                    ) : (
                      <span className="grid size-full place-items-center text-xs text-faint">No images yet</span>
                    )}
                  </div>
                </Lit>
                <p className="type-title mt-2 truncate px-1 text-[15px]">{e.name}</p>
                <p className="truncate px-1 pb-1 text-xs text-faint">
                  <span className="text-tungsten">{e.handle}</span>, {KIND_LABEL[e.kind].toLowerCase()}
                  {e.kind !== "voice" && <>, {e.images.length} image{e.images.length === 1 ? "" : "s"}</>}
                </p>
              </button>
            </li>
          ))}
        </ul>
      )}

      <ElementDialog element={editing && editing !== "new" && editing.kind === "voice" ? null : editing}
                     newKind={newKind ?? "character"} onClose={() => setEditing(null)} />
      <VoiceDialog voice={editing && editing !== "new" && editing.kind === "voice" ? editing : null}
                   onClose={() => setEditing(null)} />
      <Dialog open={cloning} onClose={() => setCloning(false)} title="Add a voice">
        {cloning && <AddVoice onDone={() => setCloning(false)} />}
      </Dialog>
      <TraitBuilder open={building} onClose={() => setBuilding(false)} />
    </div>
  );
}

const SAMPLE_SLOT: MediaSlot = { role: "input_audio", kinds: ["audio", "video"], label: "Recordings of the voice", max: 5, min: 1, required: true, auto: false };

/** A voice of your own: cloned from recordings, or one you already have on ElevenLabs, by its ID. */
function AddVoice({ onDone }: { onDone: () => void }) {
  const [how, setHow] = useState<"clone" | "id">("clone");
  return (
    <div className="space-y-4">
      <Segmented label="How" value={how} options={["clone", "id"] as const} onChange={setHow}
                 render={(h) => (h === "clone" ? "Clone from recordings" : "ElevenLabs voice ID")} />
      {how === "clone" ? <CloneVoiceForm onDone={onDone} /> : <VoiceIdForm onDone={onDone} />}
    </div>
  );
}

/** A voice from your ElevenLabs account by its ID; deleting it here never deletes it there. */
function VoiceIdForm({ onDone }: { onDone: () => void }) {
  const create = useCreateElement();
  const [name, setName] = useState("");
  const [id, setId] = useState("");
  const valid = /^[A-Za-z0-9]{10,40}$/.test(id.trim());

  async function save() {
    try {
      const voice = await create.mutateAsync({ kind: "voice", name: name.trim(), description: "", attributes: {}, images: [],
                                               voice: { model_id: VOICE_MODEL, params: { voice_id: id.trim() } } });
      toast(`${voice.name} is ready: pick it in any voice list`);
      onDone();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Couldn't add the voice");
    }
  }

  return (
    <div className="space-y-4">
      <p className="text-sm leading-relaxed text-muted">
        Use a voice from your ElevenLabs account: one you made there, or one you added from their Voice Library. In
        ElevenLabs, open My Voices and copy the voice&apos;s ID.
      </p>
      <label className="flex flex-col gap-1.5 text-xs text-muted">
        <span>Name</span>
        <input value={name} onChange={(e) => setName(e.target.value)} maxLength={80} placeholder="e.g. Old sailor"
               className="h-10 rounded-[var(--radius-control)] border border-line-soft bg-bay px-3 text-sm text-chalk outline-none placeholder:text-faint focus:border-tungsten/60" />
      </label>
      <label className="flex flex-col gap-1.5 text-xs text-muted">
        <span>Voice ID</span>
        <input value={id} onChange={(e) => setId(e.target.value)} maxLength={40} placeholder="e.g. JBFqnCBsd6RMkjVDRZzb"
               aria-invalid={id.trim() !== "" && !valid} spellCheck={false}
               className="h-10 rounded-[var(--radius-control)] border border-line-soft bg-bay px-3 font-mono text-sm text-chalk outline-none placeholder:text-faint focus:border-tungsten/60" />
        {id.trim() !== "" && !valid && <span className="text-2xs text-fault">A voice ID is 10 to 40 letters and digits, like the example.</span>}
      </label>
      <div className="flex justify-end">
        <Button tone="primary" onClick={() => void save()} disabled={!name.trim() || !valid || create.isPending}>
          {create.isPending ? "Adding…" : "Add the voice"}
        </Button>
      </div>
    </div>
  );
}

/** Clone a voice: a minute or more of clean speech makes the best clone. */
function CloneVoiceForm({ onDone }: { onDone: () => void }) {
  const clone = useCloneVoice();
  const [name, setName] = useState("");
  const [samples, setSamples] = useState<SlotAsset[]>([]);
  const [denoise, setDenoise] = useState(false);
  const [consent, setConsent] = useState(false);
  const ready = name.trim() && samples.length > 0 && consent;

  async function save() {
    try {
      const voice = await clone.mutateAsync({ name: name.trim(), samples: samples.map((s) => s.id),
                                              remove_background_noise: denoise, consent: true });
      toast(`${voice.name} is ready: pick it in any voice list`);
      onDone();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Couldn't clone the voice");
    }
  }

  return (
    <div className="space-y-4">
      <p className="text-sm leading-relaxed text-muted">
        Upload a minute or more of clear speech by one person. The clone then shows up wherever you pick a voice:
        characters, vlogs, narration and the voice changer.
      </p>
      <label className="flex flex-col gap-1.5 text-xs text-muted">
        <span>Name</span>
        <input value={name} onChange={(e) => setName(e.target.value)} maxLength={80} placeholder="e.g. My voice"
               className="h-10 rounded-[var(--radius-control)] border border-line-soft bg-bay px-3 text-sm text-chalk outline-none placeholder:text-faint focus:border-tungsten/60" />
      </label>
      <MediaSlotField slot={SAMPLE_SLOT} value={samples}
                      onAdd={(a) => setSamples((s) => [...s.filter((x) => x.id !== a.id), a].slice(-5))}
                      onRemove={(id) => setSamples((s) => s.filter((x) => x.id !== id))} />
      <label className="flex items-center gap-2 text-sm text-muted">
        <input type="checkbox" checked={denoise} onChange={(e) => setDenoise(e.target.checked)} />
        Remove background noise (only if the recordings are noisy)
      </label>
      <label className="flex items-start gap-2 text-sm text-chalk">
        <input type="checkbox" className="mt-1" checked={consent} onChange={(e) => setConsent(e.target.checked)} />
        This is my voice, or the speaker agreed to having their voice cloned.
      </label>
      <div className="flex justify-end">
        <Button tone="primary" onClick={() => void save()} disabled={!ready || clone.isPending}>
          {clone.isPending ? "Cloning…" : "Clone the voice"}
        </Button>
      </div>
    </div>
  );
}

/** One of your voices: hear it, or delete it (a clone made here also frees its slot on the voice service). */
function VoiceDialog({ voice, onClose }: { voice: Element | null; onClose: () => void }) {
  const remove = useDeleteElement();
  const id = (voice?.voice as { params?: { voice_id?: string } } | null)?.params?.voice_id ?? null;
  return (
    <Dialog open={voice !== null} onClose={onClose} title={voice?.name ?? ""}>
      {voice && (
        <div className="space-y-4">
          <VoicePicker value={id} onChange={() => undefined} label="Hear it" />
          <div className="flex justify-end">
            <Button tone="quiet" disabled={remove.isPending} onClick={() => void remove.mutateAsync(voice.id)
              .then(() => { toast(`${voice.name} deleted`); onClose(); })
              .catch((err: unknown) => toast.error(err instanceof Error ? err.message : "Couldn't delete it"))}>
              <Trash2 className="size-4" aria-hidden />
              Delete this voice
            </Button>
          </div>
        </div>
      )}
    </Dialog>
  );
}

function ElementDialog({ element, newKind, onClose }: {
  element: Element | "new" | null; newKind: ElementKind; onClose: () => void;
}) {
  const isNew = element === "new";
  const existing = element && element !== "new" ? element : null;
  return (
    <Dialog open={element !== null} onClose={onClose} title={isNew ? `New ${KIND_LABEL[newKind].toLowerCase()}` : existing?.name ?? ""} wide>
      {element !== null && <ElementForm key={existing?.id ?? "new"} existing={existing} newKind={newKind} onDone={onClose} />}
    </Dialog>
  );
}

/** Adds the built-in styles or locations this tab doesn't have yet, each with its picture. */
function AddStarters({ starters }: { starters: typeof STARTERS }) {
  const create = useCreateElement();
  const [busy, setBusy] = useState(false);
  const noun = starters[0].kind === "style" ? "styles" : "locations";

  async function add() {
    setBusy(true);
    try {
      for (const s of starters) {
        const blob = await fetch(`/starters/${s.file}.webp`).then((r) => {
          if (!r.ok) throw new Error(`The picture for ${s.name} is missing`);
          return r.blob();
        });
        const asset = await uploadFile(new File([blob], `${s.file}.webp`, { type: "image/webp" }));
        await create.mutateAsync({ kind: s.kind, name: s.name, description: s.description, attributes: {},
                                   images: [{ asset_id: asset.id, view: "front" }] });
      }
      toast(`Added ${starters.map((s) => s.name).join(", ")}. Write their @handles in any prompt.`);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : `Couldn't add the ${noun}`);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mb-6 flex max-w-3xl flex-wrap items-center gap-3 rounded-[var(--radius-panel)] bg-panel p-4">
      <ul className="flex gap-2">
        {starters.map((s) => (
          <li key={s.file}>
            <OpensFullSize src={`/starters/${s.file}.webp`} title={s.name}>
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src={`/starters/${s.file}.webp`} alt="" className="h-12 w-20 rounded-[var(--radius-frame)] object-cover" />
            </OpensFullSize>
          </li>
        ))}
      </ul>
      <p className="min-w-48 flex-1 text-xs leading-relaxed text-muted">
        Start with {starters.length} ready-made {noun} ({starters.map((s) => s.name).join(", ")}). Write one as an
        @handle in any prompt, like a character.
      </p>
      <Button size="sm" onClick={() => void add()} disabled={busy}>
        {busy ? "Adding…" : `Add ${noun}`}
      </Button>
    </div>
  );
}

/** Draws a character's front portrait from its name and description and keeps it as its picture (the same run that
 * draws a film's new cast). The card shows it when the run is done. */
const PORTRAIT_MODEL = "nano-banana-2"; // cinema_cast's own default

function portraitRun(e: Pick<Element, "id" | "slug">, modelId: string): RunCreate {
  return { recipe: "cinema_cast", input: { characters: [{ element_id: e.id, slug: e.slug }], image_model_id: modelId } };
}

/** Which image model draws a portrait, and what one costs on it. */
function PortraitModel({ value, onChange }: { value: string; onChange: (id: string) => void }) {
  const models = useModelsFor("t2i").filter((m) => m.output_type === "image");
  // the estimate needs no saved character: any id prices the same
  const estimate = useEstimateRun(portraitRun({ id: "00000000-0000-0000-0000-000000000000", slug: "preview" }, value));
  const cost = estimate.data ? (estimate.data.estimate_usd || estimate.data.live_estimate_usd) : null;
  const options = models.map((m) => ({ value: m.id, label: m.id === PORTRAIT_MODEL ? `${m.name} (recommended)` : m.name }))
    .sort((a, b) => Number(b.value === PORTRAIT_MODEL) - Number(a.value === PORTRAIT_MODEL));
  return (
    <Field label="Portrait drawn with" htmlFor="portrait-model"
           hint={cost == null ? undefined : cost ? `About ${usd(cost)} a picture` : "Free on this route"}>
      <Select id="portrait-model" value={value} onChange={onChange} options={options} />
    </Field>
  );
}

function useDrawPortrait() {
  const start = useStartRun();
  async function draw(e: Pick<Element, "id" | "slug" | "handle">, modelId: string, confirmCost?: number): Promise<void> {
    try {
      await start.mutateAsync({ ...portraitRun(e, modelId), confirm_cost_usd: confirmCost ?? null });
      toast.success(`Drawing ${e.handle}. The picture shows up on its card in about a minute.`);
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        const cost = (err.body as { estimate_usd: number }).estimate_usd;
        toast(`Drawing ${e.handle} costs about ${usd(cost)}.`, { action: { label: "Draw it", onClick: () => void draw(e, modelId, cost) } });
        return;
      }
      toast.error(err instanceof Error ? err.message : `Couldn't start drawing ${e.handle}`);
    }
  }
  return { draw, drawing: start.isPending };
}

function ElementForm({ existing, newKind, onDone }: { existing: Element | null; newKind: ElementKind; onDone: () => void }) {
  const router = useRouter();
  const startSheet = useStartRun();
  const create = useCreateElement();
  const patch = usePatchElement();
  const remove = useDeleteElement();
  const addImages = useAddElementImages();
  const removeImage = useRemoveElementImage();
  const [name, setName] = useState(existing?.name ?? "");
  const [kind, setKind] = useState<ElementKind>(existing?.kind ?? newKind);
  const [description, setDescription] = useState(existing?.description ?? "");
  const [voice, setVoice] = useState<string | null>(
    ((existing?.voice as { params?: { voice_id?: string } } | null)?.params?.voice_id) ?? null);
  const [pending, setPending] = useState<SlotAsset[]>([]);
  const [drawIt, setDrawIt] = useState(true);
  const [drawWith, setDrawWith] = useState(PORTRAIT_MODEL);
  const portrait = useDrawPortrait();
  const pictureless = kind === "character" && pending.length === 0 && !existing?.images.length;

  const images: SlotAsset[] = useMemo(
    () => [
      ...(existing?.images ?? []).map((i, n) => ({ id: i.asset.id, kind: "image", thumb: i.asset.thumb_url, url: i.asset.url,
                                                   title: `${existing?.name}, picture ${n + 1}` })),
      ...pending,
    ],
    [existing, pending],
  );

  async function save() {
    try {
      if (existing) {
        await patch.mutateAsync({ id: existing.id, name, kind, description,
                                  voice: voice ? { model_id: VOICE_MODEL, params: { voice_id: voice } } : null, clear_voice: !voice,
                                  clear_lora: false });
        if (pending.length) {
          await addImages.mutateAsync({ id: existing.id, images: pending.map((p) => ({ asset_id: p.id, view: "front" as const })) });
        }
        toast(`Saved ${name}`);
      } else {
        const e = await create.mutateAsync({
          name, kind, description, attributes: {},
          images: pending.map((p) => ({ asset_id: p.id, view: "front" as const })),
          ...(voice ? { voice: { model_id: VOICE_MODEL, params: { voice_id: voice } } } : {}),
        });
        toast(`Created ${e.handle}. Use it in any prompt.`);
        if (pictureless && drawIt && description.trim()) await portrait.draw(e, drawWith);
      }
      onDone();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Could not save");
    }
  }

  /** Draws it from every side on one picture, kept right after its main picture so prompts that mention it attach both. */
  async function characterSheet(confirmCost?: number) {
    if (!existing) return;
    try {
      const run = await startSheet.mutateAsync({
        recipe: "character_sheet", input: { element_id: existing.id, slug: existing.slug }, confirm_cost_usd: confirmCost ?? null,
      });
      toast.success(`Drawing ${existing.handle} from every side. The sheet becomes their second picture.`);
      router.push(`/runs/${run.id}`);
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        const cost = (err.body as { estimate_usd: number }).estimate_usd;
        toast(`A reference sheet costs about ${usd(cost)}.`, { action: { label: "Make it", onClick: () => void characterSheet(cost) } });
        return;
      }
      toast.error(err instanceof Error ? err.message : "Could not start the reference sheet");
    }
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
            placeholder={HINTS[kind][0]}
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
            placeholder={HINTS[kind][1]}
            className="resize-y rounded-[var(--radius-control)] border border-line-soft bg-bay px-3 py-2 text-sm leading-relaxed text-chalk outline-none placeholder:text-faint focus:border-tungsten/60"
          />
          <span className="text-2xs text-faint">Models that can’t take images get this text instead. Keep it short.</span>
        </label>
        {kind === "character" && (
          <VoicePicker value={voice} onChange={setVoice} allowNone
                       label="Voice (for dialogue and talking shots)" />
        )}
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
        {pictureless && !existing && (
          <label className="flex items-start gap-2 text-sm text-muted">
            <input type="checkbox" className="mt-1" checked={drawIt} onChange={(e) => setDrawIt(e.target.checked)} />
            <span>
              No pictures? Draw a front portrait from the description and keep it as the first one.
              {!description.trim() && <span className="block text-2xs text-faint">Write a description first.</span>}
            </span>
          </label>
        )}
        {pictureless && (existing || drawIt) && <PortraitModel value={drawWith} onChange={setDrawWith} />}
        {existing && existing.kind === "character" && <IdentityPanel element={existing} />}
      </div>

      <div className="flex flex-wrap items-center gap-2 border-t border-line-soft pt-4 md:col-span-2">
        {existing && existing.kind === "character" && (
          <Button type="button" size="sm" onClick={() => void characterSheet()} disabled={!existing.images.length || startSheet.isPending}
                  title="Draws it from every side on one picture and keeps it as its second reference">
            Make a reference sheet
          </Button>
        )}
        {existing && pictureless && (
          <Button type="button" size="sm" onClick={() => void portrait.draw(existing, drawWith)}
                  disabled={!existing.description.trim() || portrait.drawing}
                  title={existing.description.trim() ? "Draws a front portrait from the description" : "Write a description first"}>
            <Sparkles className="size-4" aria-hidden />
            Draw a picture
          </Button>
        )}
        {existing && (
          <Button
            type="button"
            size="sm"
            tone="danger"
            onClick={() => remove.mutate(existing.id, {
              onSuccess: () => { toast(`Deleted ${existing.name}`); onDone(); },
              onError: (err) => toast.error(err instanceof Error ? err.message : "Couldn't delete it"),
            })}
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

// ---------------------------------------------------------------- trained identity (Soul ID)

const MIN_PHOTOS = 10;
const MAX_PHOTOS = 80;
const DEPTHS = [600, 1000, 1500] as const;
const DEPTH_LABEL: Record<(typeof DEPTHS)[number], string> = { 600: "Quick", 1000: "Standard", 1500: "Thorough" };

type Identity = { trigger?: string; steps?: number; trained_at?: string; run_id?: string };

function IdentityPanel({ element }: { element: Element }) {
  const router = useRouter();
  const identity = (element.lora ?? null) as Identity | null;
  const patch = usePatchElement();
  const start = useStartRun();
  const [retrain, setRetrain] = useState(false);
  const [steps, setSteps] = useState<(typeof DEPTHS)[number]>(1000);
  const [confirm, setConfirm] = useState<number | null>(null);
  const photos = element.images.map((i) => i.asset.id).slice(0, MAX_PHOTOS);
  const training = !identity || retrain;
  const body: RunCreate | null = training && photos.length >= MIN_PHOTOS
    ? { recipe: "lora_train", input: { element_id: element.id, slug: element.slug, photos, steps } }
    : null;
  const estimate = useEstimateRun(useDebounced(body, 300));
  const cost = estimate.data ? (estimate.data.estimate_usd || estimate.data.live_estimate_usd) : null;

  async function train(confirmCost?: number) {
    if (!body) return;
    try {
      const run = await start.mutateAsync({ ...body, confirm_cost_usd: confirmCost ?? null });
      toast.success(`Training ${element.handle}. Their identity is ready when the run finishes.`);
      router.push(`/runs/${run.id}`);
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        setConfirm((err.body as { estimate_usd: number }).estimate_usd);
        return;
      }
      toast.error(err instanceof Error ? err.message : "Could not start training");
    }
  }

  return (
    <section aria-labelledby="identity-title" className="space-y-3 rounded-[var(--radius-control)] border border-line-soft p-4">
      <h3 id="identity-title" className="flex items-center gap-2 text-sm text-chalk">
        <Sparkles className="size-4 text-tungsten" aria-hidden />
        Trained identity
      </h3>
      {identity && !retrain ? (
        <>
          <p className="text-xs leading-relaxed text-muted">
            Trained{identity.trained_at ? ` on ${new Date(identity.trained_at).toLocaleDateString()}` : ""}
            {identity.steps ? ` with ${identity.steps.toLocaleString()} steps` : ""}. Write {element.handle} on Wan 2.2
            stills or Wan 2.2 with characters and this face comes along; the trigger word{" "}
            <span className="text-chalk">{identity.trigger}</span> goes into the prompt for you.
          </p>
          <div className="flex flex-wrap gap-2">
            <Button type="button" size="sm" onClick={() => setRetrain(true)}>Train again</Button>
            <Button type="button" size="sm" tone="ghost" disabled={patch.isPending}
                    onClick={() => patch.mutate({ id: element.id, clear_voice: false, clear_lora: true },
                                                { onSuccess: () => toast(`${element.name} is back to pictures and words`) })}>
              Forget identity
            </Button>
          </div>
        </>
      ) : (
        <>
          <p className="text-xs leading-relaxed text-muted">
            Teach the studio this face from {MIN_PHOTOS} to {MAX_PHOTOS} photos: different angles, expressions and light,
            one person per photo. Afterwards, Wan 2.2 keeps it in every picture and clip that mentions {element.handle}.
          </p>
          {photos.length < MIN_PHOTOS ? (
            <p className="text-xs text-faint">
              {photos.length} of {MIN_PHOTOS} photos so far. Add {MIN_PHOTOS - photos.length} more on the right, then save.
            </p>
          ) : (
            <>
              <div className="flex flex-col gap-1.5 text-xs text-muted">
                How closely to learn it
                <Segmented label="Training depth" value={steps} options={DEPTHS} render={(d) => DEPTH_LABEL[d]}
                           onChange={(d) => { setSteps(d); setConfirm(null); }} />
              </div>
              <p className="text-2xs text-faint">
                {photos.length} photos, {steps.toLocaleString()} steps.{" "}
                {cost == null ? "Estimating…" : estimate.data?.estimate_usd ? `About ${usd(cost)}.` : `Free on mock routes; about ${usd(cost)} live.`}
              </p>
              <div className="flex gap-2">
                {retrain && <Button type="button" size="sm" tone="ghost" onClick={() => setRetrain(false)}>Keep the current one</Button>}
                {confirm != null ? (
                  <Button type="button" size="sm" tone="primary" disabled={start.isPending} onClick={() => void train(confirm)}>
                    Train for {usd(confirm)}
                  </Button>
                ) : (
                  <Button type="button" size="sm" tone="primary" disabled={!body || start.isPending} onClick={() => void train()}>
                    Train identity
                  </Button>
                )}
              </div>
            </>
          )}
        </>
      )}
    </section>
  );
}

// ---------------------------------------------------------------- trait builder

const TRAITS: { key: string; label: string; options: string[] }[] = [
  { key: "style", label: "Style", options: ["photoreal", "cinematic film still", "anime", "stylized 3D animation", "graphic novel"] },
  { key: "age", label: "Age", options: ["young", "teenage", "twenty-something", "thirty-something", "middle-aged", "elderly"] },
  { key: "presentation", label: "Presentation", options: ["woman", "man", "androgynous person", "non-human creature"] },
  { key: "build", label: "Build", options: ["slight", "athletic", "average", "broad", "heavyset", "very tall"] },
  { key: "hair", label: "Hair", options: ["short curly", "long straight", "shaved", "braided", "messy shoulder-length", "slicked back", "bald"] },
  { key: "hair_color", label: "Hair color", options: ["black", "dark brown", "auburn", "blonde", "silver", "dyed blue", "bright red"] },
  { key: "eyes", label: "Eyes", options: ["brown", "hazel", "green", "blue", "grey", "amber"] },
  { key: "outfit", label: "Outfit", options: ["worn leather jacket", "tailored suit", "streetwear hoodie", "flowing robe", "spacesuit", "work overalls", "evening dress"] },
  { key: "detail", label: "Signature detail", options: ["scar across one eyebrow", "freckles", "round glasses", "facial tattoo", "eye patch", "silver earrings", "missing tooth"] },
  { key: "archetype", label: "Archetype", options: ["hero", "rebel", "mentor", "trickster", "villain", "everyday person"] },
];

/** The name and description placeholders for each kind. */
const HINTS: Record<ElementKind, [string, string]> = {
  character: ["e.g. Maya Ortiz", "What makes them recognisable: age, build, hair, outfit, signature details."],
  location: ["e.g. Harbour", "The place: setting, time of day, light, weather, the details that mark it."],
  prop: ["e.g. Brass compass", "The object: shape, size, material, colour, wear."],
  style: ["e.g. Noir", "The look: medium, palette, light, texture, era."],
  voice: ["e.g. Narrator", "How it sounds."],
};

/** Trait tile widths in Design a character (the pictures are 256 px). */
const TILE = { Small: "w-[76px]", Medium: "w-[112px]", Large: "w-[156px]", XL: "w-[208px]" };

/** A trait option's preview picture in `public/designer/`: `<trait>-<option as a slug>.webp`. */
const traitFile = (option: string) => option.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");

const withArticle = (phrase: string) => `${/^[aeiou]/i.test(phrase) ? "an" : "a"} ${phrase}`;

function describe(traits: Record<string, string>, heritage: string): string {
  const who = traits.age || heritage || traits.presentation
    ? [traits.age, heritage, traits.presentation || "person"].filter(Boolean).join(" ")
    : "";
  const parts = [
    who && withArticle(who),
    traits.build && `${traits.build} build`,
    (traits.hair || traits.hair_color) && `${[traits.hair, traits.hair_color].filter(Boolean).join(" ")} hair`,
    traits.eyes && `${traits.eyes} eyes`,
    traits.outfit && `wearing ${withArticle(traits.outfit)}`,
    traits.detail,
    traits.archetype && `the look of ${withArticle(traits.archetype)}`,
  ].filter(Boolean);
  return parts.join(", ");
}

function TraitBuilder({ open, onClose }: { open: boolean; onClose: () => void }) {
  const router = useRouter();
  const setDraft = useDraft((s) => s.set);
  const [traits, setTraits] = useState<Record<string, string>>({});
  const [heritage, setHeritage] = useState("");
  const [size, setSize] = useState<keyof typeof TILE>("Medium");
  const [zoom, setZoom] = useState<{ trait: (typeof TRAITS)[number]; option: string } | null>(null);
  const [name, setName] = useState("");
  const [drawWith, setDrawWith] = useState(PORTRAIT_MODEL);
  const description = describe(traits, heritage.trim());
  const style = traits.style ?? "photoreal";
  const create = useCreateElement();
  const portrait = useDrawPortrait();

  /** Saves the character and draws its portrait here; a drawn style is part of its look, so it's in the description. */
  async function save() {
    try {
      const e = await create.mutateAsync({
        kind: "character", name: name.trim(), attributes: { traits }, images: [],
        description: style === "photoreal" ? description : `${style} style, ${description}`,
      });
      onClose();
      setTraits({});
      setName("");
      await portrait.draw(e, drawWith);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Couldn't save the character");
    }
  }

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
    toast("Pick the take you like, then use “Save to Characters” on it.");
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
          <Button type="button" onClick={generate} disabled={!description}
                  title="Two portrait takes in Create image; save the one you like to Characters">
            Try in Create image
          </Button>
          <Button type="button" tone="primary" onClick={() => void save()}
                  disabled={!description || !name.trim() || create.isPending || portrait.drawing}>
            Create character
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        <div className="ml-auto w-64">
          <Segmented label="Picture size" value={size} options={Object.keys(TILE) as (keyof typeof TILE)[]} onChange={setSize} />
        </div>
        {TRAITS.map((t) => (
          <fieldset key={t.key} className="flex flex-col gap-1.5">
            <legend className="mb-1.5 text-2xs text-faint">
              {t.label}
              {traits[t.key] && <span className="text-chalk">: {traits[t.key]}</span>}
            </legend>
            <div className="flex flex-wrap gap-2">
              {t.options.map((o) => {
                const on = traits[t.key] === o;
                return (
                  <div key={o} className={cn("group relative", TILE[size])}>
                    <button type="button" aria-pressed={on} title={o}
                            onClick={() => setTraits({ ...traits, [t.key]: on ? "" : o })}
                            className={cn("flex w-full flex-col gap-1 rounded-[var(--radius-control)] border p-1 text-left",
                                          on ? "border-tungsten bg-raised" : "border-line-soft hover:border-tungsten/40")}>
                      {/* eslint-disable-next-line @next/next/no-img-element */}
                      <img src={`/designer/${t.key}-${traitFile(o)}.webp`} alt="" loading="lazy"
                           className="aspect-square w-full rounded-[3px] bg-bay object-cover"
                           onError={(e) => { e.currentTarget.style.visibility = "hidden"; }} />
                      <span className={cn("line-clamp-2 leading-tight", size === "Small" ? "text-2xs" : "text-xs",
                                          on ? "text-chalk" : "text-muted")}>{o}</span>
                    </button>
                    <ZoomButton title={o} onClick={() => setZoom({ trait: t, option: o })} />
                  </div>
                );
              })}
            </div>
          </fieldset>
        ))}
        <label className="flex max-w-xs flex-col gap-1 text-2xs text-faint">
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
      <div className="mt-4 grid gap-4 sm:grid-cols-2">
        <label className="flex flex-col gap-1 text-2xs text-faint">
          Name
          <input value={name} onChange={(e) => setName(e.target.value)} maxLength={80} placeholder="e.g. Marco Reyes"
                 className="h-9 rounded-[var(--radius-control)] border border-line-soft bg-bay px-2 text-sm text-chalk outline-none placeholder:text-faint focus:border-tungsten/60" />
        </label>
        <PortraitModel value={drawWith} onChange={setDrawWith} />
      </div>
      <Dialog open={zoom !== null} onClose={() => setZoom(null)} title={zoom ? `${zoom.trait.label}: ${zoom.option}` : ""} wide
              footer={zoom && (
                <Button type="button" tone="primary" onClick={() => {
                  setTraits({ ...traits, [zoom.trait.key]: zoom.option });
                  setZoom(null);
                }}>
                  {traits[zoom.trait.key] === zoom.option ? "Picked" : "Pick this"}
                </Button>
              )}>
        {zoom && (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={`/designer/full/${zoom.trait.key}-${traitFile(zoom.option)}.webp`} alt={zoom.option} width={768} height={768}
               className="mx-auto block h-auto max-h-[70vh] w-auto max-w-full rounded-[var(--radius-frame)]" />
        )}
      </Dialog>
      <p className="mt-2 text-2xs text-faint">
        Click a picture to pick it, again to leave it open; the corner button shows it full size. Create character
        saves it here and draws its portrait from these traits. Try in Create image makes two takes to choose from
        instead.
      </p>
    </Dialog>
  );
}
