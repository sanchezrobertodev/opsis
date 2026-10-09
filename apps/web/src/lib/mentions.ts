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
  return scenes.map((s) => ({ ...s, shots: s.shots.map((shot) => {
    const seen = new Set<string>();
    const action = shot.action.replace(MENTION, (m, h: string) => {
      const who = people[h.toLowerCase()];
      if (!who) return m;
      const first = !seen.has(h.toLowerCase());
      seen.add(h.toLowerCase());
      return first && who.look.trim() ? `${who.name} (${who.look.trim()})` : who.name;
    });
    return {
      ...shot, action, lines: shot.lines.map((l) => ({ ...l, who: name(l.who) })),
      direction: { ...shot.direction, cast_emotions: (shot.direction.cast_emotions ?? [])
        .filter((e) => !(e.who.replace(/^@/, "").toLowerCase() in people)) },
    };
  }) }));
}
