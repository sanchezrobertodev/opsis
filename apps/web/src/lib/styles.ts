/** Picture styles (`registry/styles/<category>/<id>.yaml`, served in the director library), shared by Create image,
 * the character designer and Faceless Studio. No imports, so `node --test` loads it directly. */

export const STYLE_CATEGORIES = [
  { id: "photo", label: "Photo" },
  { id: "illustration", label: "Illustration" },
  { id: "3d", label: "3D & craft" },
  { id: "painting", label: "Painting" },
  { id: "retro", label: "Print & retro" },
] as const;

export type StyleCategory = (typeof STYLE_CATEGORIES)[number]["id"];

/** What each style's sample pictures show. */
export const SAMPLE_SUBJECTS = ["person", "place", "object"] as const;

export type SampleSubject = (typeof SAMPLE_SUBJECTS)[number];

/** A style's sample picture in `public/styles/` (256 px; `full/` at 768 px). */
export const stylePicture = (id: string, subject: SampleSubject, full = false) =>
  `/styles/${full ? "full/" : ""}${id}-${subject}.webp`;

/** The styles in a category ("all" for every one) whose label, summary or prompt holds the query. */
export function filterStyles<T extends { category: string; label: string; summary: string; prompt: string }>(
  styles: T[], category: string, query: string,
): T[] {
  const q = query.trim().toLowerCase();
  return styles.filter((s) => (category === "all" || s.category === category)
    && (!q || `${s.label} ${s.summary} ${s.prompt}`.toLowerCase().includes(q)));
}

/** The five phrases the character designer offered before the catalog, as catalog ids. */
const DESIGNER_PHRASES = new Map([
  ["photoreal", "photoreal"],
  ["cinematic film still", "cinematic-still"],
  ["anime", "anime"],
  ["stylized 3D animation", "animated-3d"],
  ["graphic novel", "comic-book"],
]);

/** A character's style trait as a catalog id: an id passes through, an old phrase is mapped, anything else is "". */
export function designerStyle(value: string | null | undefined): string {
  if (!value) return "";
  return DESIGNER_PHRASES.get(value) ?? (/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(value) ? value : "");
}
