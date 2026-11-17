"use client";

import { Copy, Images, Sparkles, Trash2, UsersRound } from "lucide-react";
import { useMemo, useState } from "react";
import { toast } from "sonner";
import type { MediaSlot } from "@/lib/api";
import type { SlotAsset } from "@/lib/draft";
import {
  type Element,
  type ElementKind,
  KIND_LABEL,
  useAddElementImages,
  useCreateElement,
  useDeleteElement,
  usePatchElement,
  useRemoveElementImage,
} from "@/lib/elements";
import { hasPictures } from "@/lib/mentions";
import { EMPTY_PROFILE, type Profile } from "@/lib/profile";
import { useAsset } from "@/lib/queries";
import { MediaSlotField } from "@/components/create/MediaSlots";
import { VOICE_MODEL, VoicePicker } from "@/components/voice/VoicePicker";
import { Button, Dialog, Segmented } from "@/components/ui/controls";
import { CAST_SIZE, CastMembers, useGroupShot } from "./Casts";
import { IdentityPanel } from "./IdentityPanel";
import { LooksSection } from "./Looks";
import { AddLookPanel, MorePictures, SheetPanel, StyleLookPanel } from "./MakePanels";
import { PORTRAIT_MODEL, PortraitModel, useDrawPortrait } from "./portrait";
import { ProfileSection } from "./Profile";
import { FigurePanel, HelloPanel, MakeSection, PlacePanel, type QuickAction } from "./QuickActions";

const VOICE_SAMPLE_SLOT: MediaSlot = { role: "audio_references", kinds: ["audio"], label: "Voice sample (optional)", max: 1, min: 0, required: false, auto: false };
const REF_SLOT: MediaSlot = { role: "image_references", kinds: ["image"], label: "Reference images", max: 80, min: 0, required: false, auto: false };

export function ElementDialog({ element, newKind, onClose }: {
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

function ElementForm({ existing, newKind, onDone }: { existing: Element | null; newKind: ElementKind; onDone: () => void }) {
  const create = useCreateElement();
  const patch = usePatchElement();
  const remove = useDeleteElement();
  const addImages = useAddElementImages();
  const removeImage = useRemoveElementImage();
  const [name, setName] = useState(existing?.name ?? "");
  const [kind, setKind] = useState<ElementKind>(existing?.kind ?? newKind);
  const [description, setDescription] = useState(existing?.description ?? "");
  const savedVoice = (existing?.voice as { params?: { voice_id?: string } } | null)?.params?.voice_id ?? null;
  const [voice, setVoice] = useState<string | null>(savedVoice);
  const savedSample = (existing?.voice as { sample_asset_id?: string } | null)?.sample_asset_id ?? null;
  const { data: savedSampleAsset } = useAsset(savedSample ?? undefined);
  // undefined: the saved one stands; null: removed
  const [sample, setSample] = useState<SlotAsset | null | undefined>(undefined);
  const sampleId = sample === undefined ? savedSample : (sample?.id ?? null);
  const samples: SlotAsset[] = sample !== undefined ? (sample ? [sample] : [])
    : savedSampleAsset ? [{ id: savedSampleAsset.id, kind: "audio", url: savedSampleAsset.url, title: "Voice sample" }] : [];
  const voiceBody = voice || sampleId
    ? { model_id: VOICE_MODEL, params: voice ? { voice_id: voice } : {}, ...(sampleId ? { sample_asset_id: sampleId } : {}) }
    : null;
  const [profile, setProfile] = useState<Profile>(existing?.profile ?? EMPTY_PROFILE);
  const [pending, setPending] = useState<SlotAsset[]>([]);
  const [drawIt, setDrawIt] = useState(true);
  const [drawWith, setDrawWith] = useState(PORTRAIT_MODEL);
  const [panel, setPanel] = useState<{ open: "sheet" | "more" | "look" | "style" | QuickAction; count?: number } | null>(null);
  const portrait = useDrawPortrait();
  const cast = kind === "cast";
  const [members, setMembers] = useState<string[]>(() => existing?.members.map((m) => m.id) ?? []);
  const castSized = members.length >= CAST_SIZE[0] && members.length <= CAST_SIZE[1];
  const groupShot = useGroupShot();
  // a group shot, the quick actions, sheets, more pictures and looks read the saved element on the server and leave
  // the page, which would drop these edits: they must be saved first
  const unsaved = Boolean(existing && (name !== existing.name || description !== existing.description
    || members.join() !== existing.members.map((m) => m.id).join() || pending.length > 0 || sample !== undefined
    || voice !== savedVoice || JSON.stringify(profile) !== JSON.stringify(existing.profile)));
  // what a plain mention attaches, which looks, sheets and more pictures are drawn from (a cast: its characters')
  const drawable = existing ? hasPictures(existing) : false;

  const images: SlotAsset[] = useMemo(
    () => [
      // a look's pictures are shown with the look, not with the main ones a plain mention attaches
      ...(existing?.images ?? []).filter((i) => !i.look).map((i, n) => ({
        id: i.asset.id, kind: "image", thumb: i.asset.thumb_url, url: i.asset.url, title: `${existing?.name}, picture ${n + 1}` })),
      ...pending,
    ],
    [existing, pending],
  );
  const pictureless = kind === "character" && images.length === 0;

  async function save() {
    // only a character has one; its other data (traits, looks, identity) stays as saved
    const profileBody = kind === "character" ? { profile } : {};
    try {
      if (existing) {
        // a saved style's gallery line was written for its old look: with a new one, the look itself shows
        const style = existing.attributes.style as { category?: string; summary?: string } | undefined;
        const relook = existing.kind === "style" && style?.summary && description.trim() !== existing.description;
        await patch.mutateAsync({ id: existing.id, name, kind, description,
                                  voice: voiceBody, clear_voice: !voiceBody, clear_lora: false,
                                  ...(cast ? { members } : {}), ...profileBody,
                                  ...(relook ? { attributes: { style: { category: style.category } } } : {}) });
        if (pending.length) {
          await addImages.mutateAsync({ id: existing.id, images: pending.map((p) => ({ asset_id: p.id, view: "front" as const })) });
        }
        toast(`Saved ${name}`);
      } else {
        const e = await create.mutateAsync({
          name, kind, description, attributes: {},
          images: pending.map((p) => ({ asset_id: p.id, view: "front" as const })),
          ...(voiceBody ? { voice: voiceBody } : {}), ...profileBody,
        });
        toast(`Created ${e.handle}. Use it in any prompt.`);
        if (pictureless && drawIt && description.trim()) await portrait.draw(e, drawWith);
      }
      onDone();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Could not save");
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
        {existing ? (
          // fixed once saved: only characters star in runs, speak or train an identity, and its mentions read as one
          <p className="text-xs text-muted">
            Type <span className="ml-1 text-chalk">{cast ? "Cast (a saved group of characters)" : KIND_LABEL[kind]}</span>
          </p>
        ) : (
          <div className="flex flex-col gap-1.5 text-xs text-muted">
            Type
            <Segmented label="Type" value={kind} options={["character", "location", "prop", "style"] as const}
                       render={(k) => KIND_LABEL[k]} onChange={setKind} />
          </div>
        )}
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
          <span className="text-2xs text-faint">
            {cast ? "Every prompt that mentions the cast reads this after their names. Keep it short."
              : "Models that can’t take images get this text instead. Keep it short."}
          </span>
        </label>
        {kind === "character" && (
          <>
            <ProfileSection element={existing} value={profile} onChange={setProfile} />
            <VoicePicker value={voice} onChange={setVoice} allowNone
                         label="Voice (for dialogue and talking shots)" />
            <MediaSlotField slot={VOICE_SAMPLE_SLOT} value={samples} onAdd={(a) => setSample(a)} onRemove={() => setSample(null)} />
            <p className="text-2xs text-faint">
              A recording of them speaking, 2 to 30 s (5 to 15 s of clear speech works best). Video models that take a
              voice, like MiniMax H3, say their lines in it. A cloned voice brings its own.
            </p>
          </>
        )}
        {existing && (
          <p className="flex items-center gap-2 text-xs text-muted">
            Write <span className="text-tungsten">{existing.handle}</span> in a prompt to {cast ? "bring them all" : "use it"}.
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
        {cast ? (
          // a cast's pictures are its characters'
          <CastMembers value={members} onChange={setMembers} known={existing?.members ?? []} />
        ) : (
          <>
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
          </>
        )}
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
        {existing && existing.kind === "character" && (
          <>
            <LooksSection element={existing} open={panel?.open}
                          onAdd={() => setPanel(panel?.open === "look" ? null : { open: "look" })}
                          onStyle={() => setPanel(panel?.open === "style" ? null : { open: "style" })} />
            <MakeSection element={existing} open={panel?.open} unsaved={unsaved}
                         onOpen={(action) => setPanel(panel?.open === action ? null : { open: action })} />
            <IdentityPanel element={existing} onMore={(count) => setPanel({ open: "more", count })} />
          </>
        )}
      </div>

      {existing && panel?.open === "sheet" && <SheetPanel element={existing} unsaved={unsaved} onClose={() => setPanel(null)} />}
      {existing && panel?.open === "more" && (
        <MorePictures key={panel.count} element={existing} count={panel.count} unsaved={unsaved} onClose={() => setPanel(null)} />
      )}
      {existing && panel?.open === "look" && <AddLookPanel element={existing} unsaved={unsaved} onClose={() => setPanel(null)} />}
      {existing && panel?.open === "style" && <StyleLookPanel element={existing} unsaved={unsaved} onClose={() => setPanel(null)} />}
      {existing && panel?.open === "place" && <PlacePanel element={existing} unsaved={unsaved} onClose={() => setPanel(null)} />}
      {existing && panel?.open === "figure" && <FigurePanel element={existing} unsaved={unsaved} onClose={() => setPanel(null)} />}
      {existing && panel?.open === "hello" && <HelloPanel element={existing} unsaved={unsaved} onClose={() => setPanel(null)} />}

      <div className="flex flex-wrap items-center gap-2 border-t border-line-soft pt-4 md:col-span-2">
        {existing && existing.kind === "character" && (
          <>
            <Button type="button" size="sm" onClick={() => setPanel(panel?.open === "sheet" ? null : { open: "sheet" })}
                    disabled={!drawable} aria-expanded={panel?.open === "sheet"}
                    title={drawable ? "Draws it from every side, or its expressions or poses, on one picture" : "Add a picture first"}>
              Make a reference sheet
            </Button>
            <Button type="button" size="sm" onClick={() => setPanel(panel?.open === "more" ? null : { open: "more" })}
                    disabled={!drawable} aria-expanded={panel?.open === "more"}
                    title={drawable ? "New pictures from other angles, with other expressions and light" : "Add a picture first"}>
              <Images className="size-4" aria-hidden />
              More pictures
            </Button>
          </>
        )}
        {existing && cast && (
          <Button type="button" size="sm" onClick={() => groupShot(existing)} disabled={!drawable || unsaved}
                  title={!drawable ? "Its characters have no pictures yet" : unsaved ? "Save your changes first"
                    : "Opens Create image with a group shot of the cast, drawn from their pictures"}>
            <UsersRound className="size-4" aria-hidden />
            Group shot
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
                disabled={!name.trim() || create.isPending || patch.isPending || (cast && !castSized)}
                title={cast && !castSized ? `A cast holds ${CAST_SIZE[0]} to ${CAST_SIZE[1]} characters` : undefined}>
          {existing ? "Save changes" : "Create"}
        </Button>
      </div>
    </div>
  );
}

/** The name and description placeholders for each kind. */
const HINTS: Record<ElementKind, [string, string]> = {
  character: ["e.g. Maya Ortiz", "What makes them recognisable: age, build, hair, outfit, signature details."],
  cast: ["e.g. The crew", "What they share, read after their names: a band on tour, in matching denim jackets."],
  location: ["e.g. Harbour", "The place: setting, time of day, light, weather, the details that mark it."],
  prop: ["e.g. Brass compass", "The object: shape, size, material, colour, wear."],
  style: ["e.g. Noir", "The look: medium, palette, light, texture, era."],
  voice: ["e.g. Narrator", "How it sounds."],
};
