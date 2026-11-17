"use client";

import { Trash2 } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import type { MediaSlot } from "@/lib/api";
import type { SlotAsset } from "@/lib/draft";
import { type Element, useCloneVoice, useCreateElement, useDeleteElement } from "@/lib/elements";
import { MediaSlotField } from "@/components/create/MediaSlots";
import { VOICE_MODEL, VoicePicker } from "@/components/voice/VoicePicker";
import { Button, Dialog, Segmented } from "@/components/ui/controls";

const SAMPLE_SLOT: MediaSlot = { role: "input_audio", kinds: ["audio", "video"], label: "Recordings of the voice", max: 5, min: 1, required: true, auto: false };

/** A voice of your own: cloned from recordings, or one you already have on ElevenLabs, by its ID. */
export function AddVoice({ onDone }: { onDone: () => void }) {
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
export function VoiceDialog({ voice, onClose }: { voice: Element | null; onClose: () => void }) {
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
