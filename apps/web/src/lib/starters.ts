import type { ElementKind } from "./elements";

/** Built-in styles and locations to start from: a picture in `public/starters/` and the words models without
 * reference images get. The name sets the @handle. */
export type Starter = { file: string; kind: Extract<ElementKind, "style" | "location">; name: string; description: string };

export const STARTERS: Starter[] = [
  { file: "noir", kind: "style", name: "Noir",
    description: "black-and-white film noir: hard shadows, rain-slicked streets, deep blacks, 1940s mood" },
  { file: "watercolor", kind: "style", name: "Watercolor",
    description: "soft watercolor storybook illustration: loose washes, paper texture, gentle pastels" },
  { file: "neon-city", kind: "style", name: "Neon city",
    description: "neon cyberpunk night: magenta and cyan neon, wet reflections, haze, high contrast" },
  { file: "golden-film", kind: "style", name: "Golden film",
    description: "35mm film in golden-hour light: warm amber and teal, soft grain, gentle halation" },
  { file: "harbour", kind: "location", name: "Harbour",
    description: "a small stone fishing harbour at dawn: mist, moored wooden boats, a lighthouse on the breakwater" },
  { file: "diner", kind: "location", name: "Desert diner",
    description: "a chrome roadside diner on an empty desert highway at dusk, its windows glowing" },
  { file: "cabin", kind: "location", name: "Log cabin",
    description: "a cosy log cabin at night: stone fireplace, worn armchair, wool blankets, snow outside" },
  { file: "rooftop", kind: "location", name: "Rooftop",
    description: "a city rooftop at night: water tower, string lights, potted plants, the skyline behind" },
];
