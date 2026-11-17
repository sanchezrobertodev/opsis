import assert from "node:assert/strict";
import { test } from "node:test";
import { draftOver, EMPTY_PROFILE, written } from "./profile.ts";

test("a draft fills the empty fields and what a draft wrote, and leaves what you saved or typed", () => {
  const saved = { ...EMPTY_PROFILE, personality: "shy until she isn't", catchphrases: ["Mind the tide."] };
  const draft = { personality: "calm", backstory: "Grew up by the sea.", catchphrases: ["Leave it to Maya."], speech: "Slow." };
  const yours = written(saved); // the editor opens with what was saved as yours
  assert.deepEqual(yours, ["personality", "catchphrases"]);
  const first = draftOver(saved, draft, yours);
  assert.deepEqual(first, { personality: "shy until she isn't", backstory: "Grew up by the sea.",
                            catchphrases: ["Mind the tide."], speech: "Slow." });
  // another draft replaces what the first one wrote, and still leaves yours
  const again = { personality: "bold", backstory: "Ran the ferry.", catchphrases: ["Onward."], speech: "Fast." };
  assert.deepEqual(draftOver(first, again, yours), { ...first, backstory: "Ran the ferry.", speech: "Fast." });
  // a field you emptied, or a catchphrase row left blank, is filled like any empty one
  assert.deepEqual(draftOver({ ...saved, personality: " ", catchphrases: [""] }, draft, yours), draft);
});
