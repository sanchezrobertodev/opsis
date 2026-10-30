"use client";

import { Lightbulb, LoaderCircle } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";
import { toast } from "sonner";
import { ApiError } from "@/lib/api";
import { cn, usd } from "@/lib/cn";
import {
  type Channel,
  type EpisodeSettings,
  type TopicIdea,
  useChannelEpisodes,
  useChannels,
  useFacelessCatalog,
  useSaveChannel,
  useSuggestTopics,
} from "@/lib/faceless";
import { useDebounced } from "@/lib/hooks";
import { useElements } from "@/lib/elements";
import { useRegistry } from "@/lib/queries";
import { RUN_STATUS_TEXT, type RunCreate, useEstimateRun, useStartRun } from "@/lib/runs";
import { AspectPicker, Button, Dialog, Field, Segmented, Select, Slider, Switch } from "@/components/ui/controls";
import { statusTone } from "@/components/runs/Runs";
import { useClaudeCost } from "@/components/llm/ClaudeCost";
import { StyleCard } from "./StyleCard";

const WORDS_PER_MINUTE = 150;
const BLOCK_SECONDS = { stills: 6, motion: 8, song: 18 } as const; // a song section: a bar in, then 4 lines
const FORMAT_LABEL = { stills: "Narrated stills", motion: "Animated clips", song: "Song" } as const;
const FORMAT_HINT = {
  stills: "One illustration per spoken beat, with a slow camera move.",
  motion: "Each illustration animated into a clip as long as its narration.",
  song: "A sing-along song: each verse and chorus gets its picture, and the lyrics run as captions.",
} as const;
const SONG_THEMES = ["kids"]; // the themes with a song mode

/** Topic or script in, narrated captioned episode out. Episodes are `faceless` runs, followed on the run page. */
export function Faceless() {
  const router = useRouter();
  const { data: catalog } = useFacelessCatalog();
  const { data: registry } = useRegistry();
  const { data: channels } = useChannels();
  const startRun = useStartRun();

  const [channelId, setChannelId] = useState<string>("");
  const [theme, setTheme] = useState("storytelling");
  const [style, setStyle] = useState("watercolor-chronicle");
  const [writeOwn, setWriteOwn] = useState(false);
  const { data: characters } = useElements("character");
  const [topic, setTopic] = useState("");
  const [script, setScript] = useState("");
  const [title, setTitle] = useState("");
  const [edited, setEdited] = useState<EpisodeSettings | null>(null);
  const [voiceTouched, setVoiceTouched] = useState(false);
  const [confirm, setConfirm] = useState<number | null>(null);

  const channel = channels?.find((c) => c.id === channelId);
  const themeInfo = catalog?.themes.find((t) => t.id === theme);
  // the catalog's defaults until something is changed
  const s = edited ?? (catalog?.defaults as unknown as EpisodeSettings | undefined) ?? null;
  const voiceId = (s?.voice.voice_id as string | undefined) ?? themeInfo?.voice_id;
  const tier = (s?.voice.tier as string | undefined) ?? "standard";

  function patch(p: Partial<EpisodeSettings>) {
    if (s) setEdited({ ...s, ...p });
  }

  function applyChannel(c: Channel | undefined) {
    setChannelId(c?.id ?? "");
    if (!c || !catalog) return;
    setTheme(c.theme);
    setStyle(c.style);
    setEdited({ ...(catalog.defaults as unknown as EpisodeSettings), ...(c.defaults as Partial<EpisodeSettings>),
                notes: c.notes });
    setVoiceTouched(Boolean((c.defaults as Partial<EpisodeSettings>).voice?.voice_id));
  }

  const imageModels = (registry?.models ?? []).filter((m) => m.output_type === "image" && m.modes.some((x) => x.ops.includes("t2i")));
  const videoModels = (registry?.models ?? []).filter((m) => m.output_type === "video" && m.modes.some((x) => x.ops.includes("i2v")));

  const ready = s && themeInfo && (writeOwn ? script.trim().length > 20 : topic.trim().length > 2);
  const body: RunCreate | null = useMemo(() => {
    if (!ready || !s) return null;
    const voice = { ...s.voice, tier, ...(voiceTouched && voiceId ? { voice_id: voiceId } : {}) };
    return {
      recipe: "faceless",
      input: {
        ...s, voice, theme, style, channel_id: channelId || null,
        topic: writeOwn ? "" : topic.trim(),
        script: writeOwn ? script.trim() : null,
        title: title.trim() || null,
      },
    };
  }, [ready, s, tier, voiceTouched, voiceId, theme, style, channelId, writeOwn, topic, script, title]);
  const estimate = useEstimateRun(useDebounced(body, 400));
  const problem = estimate.error instanceof ApiError
    ? ((estimate.error.body as { errors?: string[] })?.errors ?? [estimate.error.message]).join(" ")
    : null;
  // what Claude writes: the script unless it's yours, then its picture plan; a song's lyrics come with their pictures
  const claudeSteps = s?.format === "song" ? (writeOwn ? [] : ["faceless.song"])
    : writeOwn ? ["faceless.plan"] : ["faceless.script", "faceless.plan"];
  const claude = useClaudeCost(...claudeSteps);

  async function go(confirmCost?: number) {
    if (!body) return;
    try {
      const run = await startRun.mutateAsync({ ...body, confirm_cost_usd: confirmCost ?? null });
      router.push(`/runs/${run.id}`);
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        setConfirm((err.body as { estimate_usd: number }).estimate_usd);
        return;
      }
      toast.error(err instanceof Error ? err.message : "Could not start the episode");
    }
  }

  if (!catalog || !s) return null;
  const song = s.format === "song";

  const minutes = writeOwn ? script.trim().split(/\s+/).filter(Boolean).length / WORDS_PER_MINUTE : s.minutes;
  // same guess as the recipe's estimate: ~6 characters a word, read at 14.5 characters a second
  const blocks = Math.max(1, Math.ceil((minutes * WORDS_PER_MINUTE * 6) / 14.5 / BLOCK_SECONDS[s.format]));
  const modelName = (id: string) => registry?.models.find((m) => m.id === id)?.name ?? id;
  const madeWith = [
    claudeSteps.length > 0 && `${claude.name} (${song ? "lyrics" : writeOwn ? "plan" : "script and plan"}${claude.price ? `; ${claude.price}` : ""})`,
    !song && `${modelName(s.voice_model)} (voice)`,
    `${modelName(s.image_model)} (pictures)`,
    s.format === "motion" && `${modelName(s.video_model)} (clips)`,
    (song || s.music) && `${modelName(s.music_model)} (${song ? "singing" : "music"})`,
  ].filter(Boolean).join(", ");

  return (
    <div className="grid h-full grid-cols-1 lg:grid-cols-[minmax(0,1fr)_360px]">
      <div className="min-h-0 overflow-y-auto p-6 scrollbar-quiet">
        <div className="flex max-w-4xl flex-col gap-8">
          <ChannelBar
            channels={channels ?? []}
            channel={channel}
            onPick={(id) => applyChannel(channels?.find((c) => c.id === id))}
            current={{ theme, style, notes: s.notes, defaults: { ...s, voice: { ...s.voice, tier,
              ...(voiceTouched && voiceId ? { voice_id: voiceId } : {}) } } }}
            onSaved={(c) => setChannelId(c.id)}
          />

          <section className="flex flex-col gap-3">
            <div className="flex flex-wrap items-baseline justify-between gap-3">
              <h2 className="type-title text-lg">What&rsquo;s the episode about?</h2>
              <div className="w-72 shrink-0">
                <Segmented
                  label="Script"
                  value={writeOwn ? "own" : "claude"}
                  options={["claude", "own"] as const}
                  render={(v) => (v === "claude" ? "Claude writes it" : song ? "I have lyrics" : "I have a script")}
                  onChange={(v) => setWriteOwn(v === "own")}
                />
              </div>
            </div>
            {writeOwn ? (
              <>
                <input
                  value={title}
                  onChange={(e) => setTitle(e.target.value)}
                  placeholder="Title"
                  aria-label="Title"
                  className="h-10 rounded-[var(--radius-control)] border border-line-soft bg-bay px-3 text-sm outline-none focus:border-tungsten/60"
                />
                <textarea
                  value={script}
                  onChange={(e) => setScript(e.target.value)}
                  rows={10}
                  aria-label={song ? "Lyrics" : "Script"}
                  placeholder={song ? "One paragraph per verse or chorus, one sung line per line. Start a paragraph with [Chorus] to name it."
                                    : "Paste the narration exactly as it should be read. Plain paragraphs; no scene labels."}
                  className="resize-y rounded-[var(--radius-control)] border border-line-soft bg-bay px-3 py-2 text-sm leading-relaxed outline-none placeholder:text-faint focus:border-tungsten/60"
                />
                <p className="tabular text-2xs text-faint">
                  {song ? `${script.split("\n").filter((l) => l.trim() && !/^\[.*\]$/.test(l.trim())).length} sung lines`
                        : `${script.trim().split(/\s+/).filter(Boolean).length} words, about ${minutes.toFixed(1)} min read aloud`}
                </p>
              </>
            ) : (
              <TopicPicker theme={theme} channelId={channelId} topic={topic} onTopic={setTopic} notes={s.notes} />
            )}
          </section>

          <section className="flex flex-col gap-3">
            <h2 className="type-title text-lg">Theme</h2>
            <Segmented
              label="Theme"
              value={theme}
              options={catalog.themes.map((t) => t.id)}
              render={(v) => catalog.themes.find((t) => t.id === v)?.label}
              onChange={(v) => { setTheme(v); if (!SONG_THEMES.includes(v) && s.format === "song") patch({ format: "stills" }); }}
            />
            <p className="text-xs text-muted">{themeInfo?.summary}</p>
          </section>

          <section className="flex flex-col gap-3">
            <h2 className="type-title text-lg">Style</h2>
            <div role="radiogroup" aria-label="Style" className="grid grid-cols-[repeat(auto-fill,minmax(176px,1fr))] gap-3">
              {catalog.styles.map((st) => (
                <StyleCard key={st.id} style={st} selected={st.id === style} onSelect={() => setStyle(st.id)} />
              ))}
            </div>
            <p className="text-2xs text-faint">The captions in each card are drawn the way that style burns them in.</p>
          </section>
        </div>
      </div>

      <aside className="flex min-h-0 flex-col gap-5 overflow-y-auto border-t border-line-soft p-5 scrollbar-quiet lg:border-t-0 lg:border-l">
        <Field label="Format" hint={FORMAT_HINT[s.format]}>
          <Segmented label="Format" value={s.format}
                     options={SONG_THEMES.includes(theme) ? (["stills", "motion", "song"] as const) : (["stills", "motion"] as const)}
                     render={(v) => FORMAT_LABEL[v]} onChange={(v) => patch({ format: v })} />
        </Field>
        {!writeOwn && (
          <Field label="Length">
            <Slider value={s.minutes} min={0.5} max={5} step={0.5}
                    onChange={(v) => patch({ minutes: v, ...(s.max_seconds != null ? { max_seconds: Math.round(v * 60) } : {}) })}
                    format={(v) => `${v} min`} />
            {!song && (
              <Toggle label="Hold to the length"
                      hint={`Once the narration is voiced, the exact length is known: past ${Math.round(s.minutes * 60)} s, it stops before ${s.format === "motion" ? "the clips" : "the cut"} so you can shorten it.`}
                      checked={s.max_seconds != null}
                      onChange={(v) => patch({ max_seconds: v ? Math.round(s.minutes * 60) : null })} />
            )}
          </Field>
        )}
        <Field label="Aspect">
          <AspectPicker value={s.aspect_ratio} options={["16:9", "9:16"]}
                        onChange={(v) => patch({ aspect_ratio: v as EpisodeSettings["aspect_ratio"] })} />
        </Field>
        {!song && <Field label="Narrator" hint={voiceTouched ? undefined : "The theme's voice. Pick another to keep it."}>
          <Select
            value={voiceId ?? ""}
            options={catalog.voices.map((v) => ({ value: v.id, label: v.label }))}
            onChange={(v) => { setVoiceTouched(true); patch({ voice: { ...s.voice, voice_id: v } }); }}
          />
          <Segmented label="Voice quality" value={tier} options={["standard", "turbo"] as const}
                     render={(v) => (v === "standard" ? "v4" : "v4 Turbo, half price")}
                     onChange={(v) => patch({ voice: { ...s.voice, tier: v } })} />
        </Field>}
        {!song && (
          <Field label="Star" hint="A saved character, drawn from its pictures so it looks the same in every shot it's in.">
            <Select
              value={s.character ?? ""}
              onChange={(v) => patch({ character: v || null })}
              options={[
                { value: "", label: characters?.length ? "None" : "No saved characters yet" },
                ...(characters ?? []).map((c) => ({ value: c.slug, label: `${c.name} (@${c.slug})` })),
                // a channel's saved star that was since deleted: shown, so it can be seen and cleared
                ...(s.character && characters && !characters.some((c) => c.slug === s.character)
                  ? [{ value: s.character, label: `@${s.character} (no longer saved)` }] : []),
              ]}
            />
          </Field>
        )}
        <div className="flex flex-col gap-3">
          {!song && <Toggle label="Music" hint="A score ducked under the voice." checked={s.music} onChange={(v) => patch({ music: v })} />}
          {s.format === "motion" && (
            <Toggle label="Clip sound" hint="Each clip made with its own sound (animals, weather, footsteps), kept under the voice. It can cost more."
                    checked={s.clip_sound} onChange={(v) => patch({ clip_sound: v })} />
          )}
          <Toggle label="Captions" hint={song ? "The lyrics, burned in line by line. An SRT is saved too."
                                              : "Burned in, with the spoken word highlighted. An SRT is saved too."}
                  checked={s.captions} onChange={(v) => patch({ captions: v })} />
          <Toggle label="Thumbnail" hint="Three options to pick from at the end." checked={s.thumbnail}
                  onChange={(v) => patch({ thumbnail: v })} />
          {!writeOwn && (
            <Toggle label={song ? "Review the lyrics first" : "Review the script first"} hint="Read and edit it before any images are made."
                    checked={s.review_script} onChange={(v) => patch({ review_script: v })} />
          )}
        </div>
        <details className="group text-sm">
          <summary className="cursor-pointer text-xs text-muted hover:text-chalk">Models and quality</summary>
          <div className="mt-3 flex flex-col gap-4">
            <Field label="Images">
              <Select value={s.image_model} options={imageModels.map((m) => ({ value: m.id, label: m.name }))}
                      onChange={(v) => patch({ image_model: v, image_params: {} })} />
            </Field>
            {s.format === "motion" && (
              <Field label="Animation">
                <Select value={s.video_model} options={videoModels.map((m) => ({ value: m.id, label: m.name }))}
                        onChange={(v) => patch({ video_model: v, video_params: {} })} />
              </Field>
            )}
            <Field label="Resolution">
              <Segmented label="Resolution" value={s.resolution} options={["720p", "1080p"] as const}
                         onChange={(v) => patch({ resolution: v })} />
            </Field>
            <Field label="Language">
              <input value={s.language} onChange={(e) => patch({ language: e.target.value })} aria-label="Language"
                     className="h-10 rounded-[var(--radius-control)] border border-line-soft bg-bay px-3 text-sm outline-none focus:border-tungsten/60" />
            </Field>
          </div>
        </details>

        <div className="mt-auto space-y-3">
          <p className="text-2xs leading-relaxed text-faint">Made with {madeWith}.</p>
          <p className="text-xs leading-relaxed text-muted">
            {problem ? (
              <span className="text-fault">{problem}</span>
            ) : !body ? (
              writeOwn ? "Paste a script to see the cost." : "Give it a topic to see the cost."
            ) : estimate.data ? (
              <>
                About {minutes.toFixed(1)} min in {blocks} {song ? "sections" : "blocks"}, {estimate.data.generations} takes
                {estimate.data.estimate_usd === 0 ? ", free on mock routes." : `, about ${usd(estimate.data.estimate_usd)}.`}
                {estimate.data.live_estimate_usd != null && estimate.data.estimate_usd === 0 &&
                  ` Live it would cost about ${usd(estimate.data.live_estimate_usd)}.`}
              </>
            ) : (
              "Estimating…"
            )}
          </p>
          {confirm != null ? (
            <div className="flex gap-2">
              <Button tone="ghost" onClick={() => setConfirm(null)}>Cancel</Button>
              <Button tone="primary" className="flex-1" onClick={() => void go(confirm)} disabled={startRun.isPending}>
                Start for {usd(confirm)}
              </Button>
            </div>
          ) : (
            <Button tone="primary" size="lg" className="w-full" onClick={() => void go()}
                    disabled={!body || Boolean(problem) || startRun.isPending}>
              Make the episode
            </Button>
          )}
        </div>
      </aside>
    </div>
  );
}

function Toggle({ label, hint, checked, onChange }: { label: string; hint: string; checked: boolean;
                                                     onChange: (v: boolean) => void }) {
  return (
    <label className="flex items-center justify-between gap-4 text-sm">
      <span>
        {label}
        <span className="block text-2xs text-faint">{hint}</span>
      </span>
      <Switch label={label} checked={checked} onChange={onChange} />
    </label>
  );
}

function TopicPicker({ theme, channelId, topic, onTopic, notes }: {
  theme: string; channelId: string; topic: string; onTopic: (t: string) => void; notes: string;
}) {
  const suggest = useSuggestTopics();
  const claude = useClaudeCost("faceless.topics");
  const [ideas, setIdeas] = useState<TopicIdea[]>([]);
  async function ask() {
    try {
      const res = await suggest.mutateAsync({ theme, channel_id: channelId || null, notes });
      setIdeas(res.topics);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Claude couldn't suggest topics");
    }
  }
  return (
    <div className="flex flex-col gap-3">
      <div className="flex gap-2">
        <input
          value={topic}
          onChange={(e) => onTopic(e.target.value)}
          placeholder="A topic, a question, or a story idea"
          aria-label="Topic"
          className="h-11 min-w-0 flex-1 rounded-[var(--radius-control)] border border-line-soft bg-bay px-3 text-sm outline-none placeholder:text-faint focus:border-tungsten/60"
        />
        <Button tone="quiet" onClick={() => void ask()} disabled={suggest.isPending}>
          {suggest.isPending ? <LoaderCircle className="size-4 animate-spin" aria-hidden /> : <Lightbulb className="size-4" aria-hidden />}
          Suggest
        </Button>
      </div>
      <p className="text-2xs text-faint">Suggest asks {claude.text}.</p>
      {ideas.length > 0 && (
        <ul className="grid gap-2 sm:grid-cols-2">
          {ideas.map((idea) => (
            <li key={idea.title}>
              <button
                type="button"
                onClick={() => onTopic(idea.title)}
                className={cn(
                  "flex w-full flex-col gap-1 rounded-[var(--radius-control)] border px-3 py-2.5 text-left transition-colors",
                  topic === idea.title ? "border-tungsten/60 bg-raised" : "border-line-soft hover:border-line",
                )}
              >
                <span className="text-sm text-chalk">{idea.title}</span>
                <span className="text-2xs leading-snug text-faint">{idea.angle}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function ChannelBar({ channels, channel, onPick, current, onSaved }: {
  channels: Channel[];
  channel: Channel | undefined;
  onPick: (id: string) => void;
  current: { theme: string; style: string; notes: string; defaults: EpisodeSettings };
  onSaved: (c: Channel) => void;
}) {
  const save = useSaveChannel();
  const { data: episodes } = useChannelEpisodes(channel?.id);
  const [naming, setNaming] = useState(false);
  const [name, setName] = useState("");
  // notes live on the channel itself; everything else is an episode default
  const defaults = Object.fromEntries(Object.entries(current.defaults).filter(([k]) => k !== "notes"));

  async function store(id?: string) {
    try {
      const c = await save.mutateAsync({ id, name: id ? channel!.name : name.trim(), theme: current.theme,
                                         style: current.style, notes: current.notes, defaults });
      onSaved(c);
      setNaming(false);
      toast(id ? `Saved these settings to ${c.name}` : `Channel ${c.name} created`);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Could not save the channel");
    }
  }

  return (
    <section className="flex flex-col gap-3 rounded-[var(--radius-panel)] bg-panel p-4">
      <div className="flex flex-wrap items-center gap-3">
        <label htmlFor="channel" className="text-xs text-muted">Channel</label>
        <select
          id="channel"
          value={channel?.id ?? ""}
          onChange={(e) => onPick(e.target.value)}
          className="h-9 min-w-48 rounded-[var(--radius-control)] border border-line-soft bg-bay px-3 text-sm text-chalk outline-none focus:border-tungsten/60"
        >
          <option value="">No channel (one-off episode)</option>
          {channels.map((c) => (
            <option key={c.id} value={c.id}>{c.name}</option>
          ))}
        </select>
        <div className="ml-auto flex gap-2">
          {channel && (
            <Button size="sm" tone="ghost" onClick={() => void store(channel.id)} disabled={save.isPending}>
              Save settings to channel
            </Button>
          )}
          <Button size="sm" tone="quiet" onClick={() => { setName(""); setNaming(true); }}>New channel</Button>
        </div>
      </div>
      {channel && (
        <p className="text-2xs text-faint">
          {channel.episodes === 0 ? "No episodes yet." : `${channel.episodes} episode${channel.episodes === 1 ? "" : "s"}.`}{" "}
          Theme, style, voice and settings below come from the channel.
        </p>
      )}
      {episodes && episodes.length > 0 && (
        <ul className="flex flex-col gap-1">
          {episodes.map((e) => (
            <li key={e.id}>
              <Link href={`/runs/${e.id}`} className="flex items-center gap-3 rounded-[4px] px-2 py-1 text-xs hover:bg-raised">
                <span className="min-w-0 flex-1 truncate text-chalk">{e.title.replace(/^Faceless: /, "")}</span>
                <span className={cn("shrink-0", statusTone(e.status))}>{RUN_STATUS_TEXT[e.status] ?? e.status}</span>
              </Link>
            </li>
          ))}
        </ul>
      )}
      <Dialog
        open={naming}
        onClose={() => setNaming(false)}
        title="New channel"
        footer={(
          <>
            <Button tone="ghost" onClick={() => setNaming(false)}>Cancel</Button>
            <Button tone="primary" onClick={() => void store()} disabled={!name.trim() || save.isPending}>Create channel</Button>
          </>
        )}
      >
        <p className="mb-3">A channel remembers the theme, style, voice and settings you have now, so every episode matches.</p>
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="Channel name"
          aria-label="Channel name"
          autoFocus
          className="h-10 w-full rounded-[var(--radius-control)] border border-line-soft bg-bay px-3 text-sm text-chalk outline-none focus:border-tungsten/60"
        />
      </Dialog>
    </section>
  );
}
