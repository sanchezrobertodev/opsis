/** A character's profile as the editor holds it (the server's ElementProfile): what writers read when they give the
 * character lines. */
export type Profile = { personality: string; backstory: string; catchphrases: string[]; speech: string };

export const EMPTY_PROFILE: Profile = { personality: "", backstory: "", catchphrases: [], speech: "" };

const FIELDS = ["personality", "backstory", "catchphrases", "speech"] as const;
const blank = (v: string | string[]) => (Array.isArray(v) ? v : [v]).every((x) => !x.trim());

/** The fields with something in them: what was saved counts as yours, so a draft leaves it. */
export const written = (profile: Profile) => FIELDS.filter((k) => !blank(profile[k]));

/** Claude's draft over the profile on screen: a field you've written in (`yours`: saved, or typed here) stays as it is
 * unless it's empty now; the others, empty or only drafted, take the draft. */
export function draftOver(profile: Profile, draft: Profile, yours: readonly string[]): Profile {
  return Object.fromEntries(FIELDS.map((k) => [k, yours.includes(k) && !blank(profile[k]) ? profile[k] : draft[k]])) as Profile;
}
