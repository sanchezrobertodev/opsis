import assert from "node:assert/strict";
import { test } from "node:test";
import { retryMedia } from "./mentions.ts";

const roles = (media: { role: string; asset_id: string }[]) => media.map((m) => `${m.role}:${m.asset_id}`);

test("another take drops what mentions attached, but keeps the same picture given in its own role", () => {
  const media = [
    { role: "start_image", asset_id: "portrait" }, // the character's portrait, given as the start frame
    { role: "image_references", asset_id: "mine" },
    { role: "image_references", asset_id: "portrait" }, // attached by @juno
    { role: "image_references", asset_id: "juno-side" }, // attached by @juno
  ];
  assert.deepEqual(roles(retryMedia(media, [{ attached: ["portrait", "juno-side"] }])),
    ["start_image:portrait", "image_references:mine"]);
});

test("a picture two mentions attached is dropped once per mention; nothing attached keeps every input", () => {
  const media = [{ role: "start_image", asset_id: "p" }, { role: "image_references", asset_id: "p" },
                 { role: "image_references", asset_id: "p" }];
  assert.deepEqual(roles(retryMedia(media, [{ attached: ["p"] }, { attached: ["p"] }])), ["start_image:p"]);
  assert.deepEqual(retryMedia(media, [{}, { attached: [] }]), media);
});
