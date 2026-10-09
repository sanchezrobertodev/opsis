import type { Scene } from "./cinema";

const MENTION = /(?<![\w@])@([A-Za-z0-9][A-Za-z0-9_-]{0,39})/g;

export type Unkept = { name: string; look: string };

/**
 * Write proposed characters the user didn't keep in words instead of @handles. Each shot is its own prompt, so a
 * person's look goes in the first time they appear in a shot ("The Baker (a stout woman...)"), then just the name.
 */
export function unmention(scenes: Scene[], people: Record<string, Unkept>): Scene[] {
  if (!Object.keys(people).length) return scenes;
  const name = (text: string) => text.replace(MENTION, (m, h: string) => people[h.toLowerCase()]?.name ?? m);
  return scenes.map((s) => ({ ...s, shots: s.shots.map((shot) => ({
    ...shot, action: withLooks(shot.action, people, name), lines: shot.lines.map((l) => ({ ...l, who: name(l.who) })),
    direction: { ...shot.direction, cast_emotions: (shot.direction.cast_emotions ?? [])
      .filter((e) => !(e.who.replace(/^@/, "").toLowerCase() in people)) },
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
