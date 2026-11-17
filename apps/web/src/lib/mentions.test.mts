import assert from "node:assert/strict";
import { test } from "node:test";
import type { Scene } from "./cinema.ts";
import {
  completeMention,
  hasPictures,
  lookSlug,
  mentionsIn,
  picturedMentions,
  retryMedia,
  styleLook,
  typedMention,
  unmention,
} from "./mentions.ts";

test("a look is a colon right before a letter or digit; a colon ending a phrase leaves a plain mention", () => {
  assert.deepEqual(mentionsIn("@Maya:Winter by @kai: she waves at @MAYA:winter. In the style of @noir."), [
    { slug: "maya", look: "winter", text: "@Maya:Winter" },
    { slug: "kai", look: null, text: "@kai" },
    { slug: "noir", look: null, text: "@noir" },
  ]);
  assert.deepEqual(mentionsIn("mail me@maya:winter, or @maya:"), [{ slug: "maya", look: null, text: "@maya" }]);
});

test("a proposed character written in words takes the look written after its handle with it", () => {
  const shot = { id: "s1", action: "@baker:apron greets @maya:winter.", lines: [], direction: {
    cast_emotions: [{ who: "@baker:apron", emotion: "joy" }, { who: "@maya:winter", emotion: "fear" }] } };
  const scenes = [{ id: "a", heading: "", summary: "", shots: [shot] }] as unknown as Scene[];
  const [done] = unmention(scenes, { baker: { name: "The Baker", look: "" } })[0].shots;
  assert.equal(done.action, "The Baker greets @maya:winter.");
  // a feeling of someone written in words goes, whichever look named them
  assert.deepEqual(done.direction.cast_emotions?.map((e) => e.who), ["@maya:winter"]);
});

const image = (view: string, look: string | null = null) => ({ view, look });

test("a mention brings pictures: a character's main ones, its look's when it wears it, a cast's characters'", () => {
  const maya = { slug: "maya", kind: "character", images: [image("expressions"), image("front", "winter")] };
  assert.equal(hasPictures(maya), false); // a grid sheet and a look's picture only
  assert.equal(hasPictures(maya, "winter"), true);
  const crew = { slug: "crew", kind: "cast", images: [image("front")], members: [{ cover: null }, { cover: {} }] };
  assert.equal(hasPictures(crew), true);
  assert.equal(hasPictures({ ...crew, members: [{ cover: null }] }), false); // its own pictures wait until it has none
  assert.deepEqual(picturedMentions("@maya:winter and @maya with @crew and @nobody", [maya, crew]).map((m) => m.text),
    ["@maya:winter", "@crew"]);
});

test("typing after a handle's colon suggests its looks; picking one replaces what was typed", () => {
  assert.deepEqual(typedMention("Wide shot, @ma"), { slug: "ma", look: null, at: 11 });
  assert.deepEqual(typedMention("@Maya:wi"), { slug: "maya", look: "wi", at: 0 });
  assert.equal(typedMention("@maya: she"), null);
  assert.deepEqual(completeMention("Wide shot, @ma", 14, "maya"), { value: "Wide shot, @maya ", caret: 17 });
  assert.deepEqual(completeMention("@maya:wint, walking", 9, "maya:winter"), { value: "@maya:winter, walking", caret: 12 });
  // a new handle keeps the look after it, and a word after stays one space away
  assert.deepEqual(completeMention("@ka:winter", 3, "kai"), { value: "@kai:winter", caret: 4 });
  assert.deepEqual(completeMention("@kx more", 2, "kai"), { value: "@kai more", caret: 5 });
});

test("a look's handle: dashes for words, accents dropped, 24 characters at most, cut at a dash when one is near", () => {
  assert.equal(lookSlug("Winter coat"), "winter-coat");
  assert.equal(lookSlug("  Café au lait!  "), "cafe-au-lait");
  assert.equal(lookSlug("editorial-motion-graphics"), "editorial-motion");
  assert.equal(lookSlug("@my-saved-style"), "my-saved-style");
  assert.equal(lookSlug("abcdefghijklmnopqrstuvwxyz"), "abcdefghijklmnopqrstuvwx");
  assert.equal(lookSlug("!!!"), "");
  assert.equal(styleLook("Comic book"), "drawn in comic book style");
  assert.equal(styleLook("3D animated feature"), "drawn in 3D animated feature style");
  assert.equal(styleLook("VHS glow"), "drawn in VHS glow style");
});

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

test("another take drops what a look and a cast's characters attached, so they attach again", () => {
  const media = [
    { role: "image_references", asset_id: "mine" },
    { role: "image_references", asset_id: "maya-winter" }, // @maya:winter: the look's picture, then her main one
    { role: "image_references", asset_id: "maya" },
    { role: "image_references", asset_id: "kai" }, // @crew: one picture of each of its characters
    { role: "image_references", asset_id: "juno" },
  ];
  const mentions = [{ slug: "maya", look: "winter", attached: ["maya-winter", "maya"] },
                    { slug: "crew", look: null, attached: ["kai", "juno"] }];
  assert.deepEqual(roles(retryMedia(media, mentions)), ["image_references:mine"]);
});

test("a picture two mentions attached is dropped once per mention; nothing attached keeps every input", () => {
  const media = [{ role: "start_image", asset_id: "p" }, { role: "image_references", asset_id: "p" },
                 { role: "image_references", asset_id: "p" }];
  assert.deepEqual(roles(retryMedia(media, [{ attached: ["p"] }, { attached: ["p"] }])), ["start_image:p"]);
  assert.deepEqual(retryMedia(media, [{}, { attached: [] }]), media);
});
