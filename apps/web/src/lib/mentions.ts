import type { Scene } from "./cinema";

/** A mention as the server reads it (`director/elements.py:MENTION_LOOK`): `@handle`, and a character's look after a
 * colon (`@maya:winter`). The colon must come right before a letter or digit, so "@maya: she says" stays a plain one. */
const MENTION = /(?<![\w@])@([A-Za-z0-9][A-Za-z0-9_-]{0,39})(?::([A-Za-z0-9][A-Za-z0-9-]{0,23}))?/g;

export type Mention = { slug: string; look: string | null; text: string };

/** Each @mention in a text once, as written (`@maya:winter`), with its handle and look in lower case. */
export function mentionsIn(text: string): Mention[] {
  const seen = new Map<string, Mention>();
  for (const m of (text ?? "").matchAll(MENTION)) {
    const key = m[0].toLowerCase();
    if (!seen.has(key)) seen.set(key, { slug: m[1].toLowerCase(), look: m[2]?.toLowerCase() ?? null, text: m[0] });
  }
  return [...seen.values()];
}

/** Views a mention never attaches: grid sheets, which a model given one tends to draw back as a grid. */
export const GRID_VIEWS = ["expressions", "poses"];

type Mentionable = {
  slug: string; kind: string; images: { view: string; look?: string | null }[]; members?: { cover: unknown }[];
};

/** Whether mentioning it attaches pictures: a cast's characters' (its own only once it has none), else its main
 * pictures, or its look's. */
export function hasPictures(e: Mentionable, look: string | null = null): boolean {
  if (e.kind === "cast" && e.members?.length) return e.members.some((m) => m.cover);
  return e.images.some((i) => !GRID_VIEWS.includes(i.view) && (!i.look || i.look === look));
}

/** The mentions in a text of elements that bring pictures along. */
export function picturedMentions(text: string, elements: Mentionable[]): Mention[] {
  return mentionsIn(text).filter((m) => elements.some((e) => e.slug === m.slug && hasPictures(e, m.look)));
}

/** What's typed right before the caret: a handle (`@ma`) or, after a handle and a colon, one of its looks (`@maya:wi`);
 * `at` is where its @ is. */
export function typedMention(before: string): { slug: string; look: string | null; at: number } | null {
  const look = /(?:^|[^\w@])@([A-Za-z0-9][A-Za-z0-9_-]{0,39}):([A-Za-z0-9-]{0,24})$/.exec(before);
  const handle = look ? null : /(?:^|[^\w@])@([A-Za-z0-9_-]{0,40})$/.exec(before);
  const m = look ?? handle;
  return m ? { slug: m[1].toLowerCase(), look: look ? look[2].toLowerCase() : null, at: before.lastIndexOf("@") } : null;
}

/** The text with the mention typed before the caret completed as `@text` (a handle, or `handle:look`), and the caret
 * after it. The rest of the word under the caret goes; a look or punctuation right after it stays attached. */
export function completeMention(value: string, caret: number, text: string): { value: string; caret: number } | null {
  const typed = typedMention(value.slice(0, caret));
  if (!typed) return null;
  const rest = value.slice(caret).replace(typed.look === null ? /^[A-Za-z0-9_-]*/ : /^[A-Za-z0-9-]*/, "");
  const head = `${value.slice(0, typed.at)}@${text}`;
  const tail = /^[\s.,;:!?)\]]/.test(rest) ? rest : ` ${rest}`;
  return { value: head + tail, caret: head.length + (tail.startsWith(" ") ? 1 : 0) };
}

export const LOOK_SLUG_MAX = 24;

/** A look's handle from its name or a style's id (`@maya:winter-coat`): lower case, words joined by dashes, at most 24
 * characters, cut at a dash when one is near. "" when nothing is left. */
export function lookSlug(text: string): string {
  const slug = text.normalize("NFKD").replace(/[\u0300-\u036f]/g, "").toLowerCase()
    .replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
  if (slug.length <= LOOK_SLUG_MAX) return slug;
  const cut = slug.lastIndexOf("-", LOOK_SLUG_MAX);
  return (cut >= LOOK_SLUG_MAX / 2 ? slug.slice(0, cut) : slug.slice(0, LOOK_SLUG_MAX)).replace(/-+$/, "");
}

/** How a prompt reads a look that is the character in a picture style, after its description: "drawn in comic book
 * style" (a label's first word in lower case unless it's an acronym like "3D"). */
export function styleLook(label: string): string {
  return `drawn in ${label.trim().replace(/^[A-Z](?![A-Z0-9])/, (c) => c.toLowerCase())} style`;
}

export type Unkept = { name: string; look: string };

/**
 * Write proposed characters the user didn't keep in words instead of @handles (a look written after one goes too).
 * Each shot is its own prompt, so a person's look goes in the first time they appear in a shot ("The Baker (a stout
 * woman...)"), then just the name.
 */
export function unmention(scenes: Scene[], people: Record<string, Unkept>): Scene[] {
  if (!Object.keys(people).length) return scenes;
  const name = (text: string) => text.replace(MENTION, (m, h: string) => people[h.toLowerCase()]?.name ?? m);
  return scenes.map((s) => ({ ...s, shots: s.shots.map((shot) => ({
    ...shot, action: withLooks(shot.action, people, name), lines: shot.lines.map((l) => ({ ...l, who: name(l.who) })),
    direction: { ...shot.direction, cast_emotions: (shot.direction.cast_emotions ?? [])  // `@baker:apron` is the Baker
      .filter((e) => !(e.who.trim().replace(/^@/, "").split(":")[0].toLowerCase() in people)) },
  })) }));
}

const MAX_ACTION = 2000; // Shot.action on the server

/** The action with names, and each person's look after their first mention, all within the action limit. */
function withLooks(action: string, people: Record<string, Unkept>, name: (text: string) => string): string {
  const plain = name(action);
  const firsts = [...new Set([...action.matchAll(MENTION)].map((m) => m[1].toLowerCase()).filter((h) => h in people))];
  // the room left is shared between the looks; one that doesn't fit is cut at a word, and none goes in a sliver
  const each = firsts.length ? Math.floor((MAX_ACTION - plain.length) / firsts.length) - 3 : 0;  // " (" + ")"
  const seen = new Set<string>();
  const out = action.replace(MENTION, (m, h: string) => {
    const who = people[h.toLowerCase()];
    if (!who) return m;
    const first = !seen.has(h.toLowerCase());
    seen.add(h.toLowerCase());
    const look = first ? fit(who.look.trim(), each) : "";
    return look ? `${who.name} (${look})` : who.name;
  });
  return out.slice(0, MAX_ACTION);
}

function fit(text: string, room: number): string {
  if (text.length <= room) return text;
  if (room < 12) return "";
  return text.slice(0, room - 1).replace(/[\s,;]+\S*$/, "") + "…";
}

/**
 * The inputs to send again for another take: the stored media without what its @mentions attached, which attaches
 * again when the prompt is resolved. Resolution appends what it attaches after the inputs given, so each attached
 * picture is the last of its copies: dropping those keeps the same picture given in a role of its own (a start frame
 * that is also the mentioned character's portrait). A voice listed for a second speaker or already given has no copy
 * of its own; dropping it is harmless, as resolving attaches it again in the same role.
 */
export function retryMedia<M extends { asset_id: string }>(media: M[], mentions: { attached?: string[] }[]): M[] {
  const left = new Map<string, number>();
  for (const id of mentions.flatMap((m) => m.attached ?? [])) left.set(id, (left.get(id) ?? 0) + 1);
  const kept: M[] = [];
  for (const m of [...media].reverse()) {
    const n = left.get(m.asset_id) ?? 0;
    if (n > 0) left.set(m.asset_id, n - 1);
    else kept.push(m);
  }
  return kept.reverse();
}
