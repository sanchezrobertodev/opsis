import type { ElementKind } from "./elements";

/** Built-in locations to start from: a picture in `public/starters/` and the words models without reference images
 * get. The name sets the @handle. (Starter characters are registry data, `registry/characters/starters.yaml`; picture
 * styles are the style catalog's.) */
export type Starter = { file: string; kind: Extract<ElementKind, "location">; name: string; description: string };

export const STARTERS: Starter[] = [
  { file: "harbour", kind: "location", name: "Harbour",
    description: "a small stone fishing harbour at dawn: mist, moored wooden boats, a lighthouse on the breakwater" },
  { file: "diner", kind: "location", name: "Desert diner",
    description: "a chrome roadside diner on an empty desert highway at dusk, its windows glowing" },
  { file: "cabin", kind: "location", name: "Log cabin",
    description: "a cosy log cabin at night: stone fireplace, worn armchair, wool blankets, snow outside" },
  { file: "rooftop", kind: "location", name: "Rooftop",
    description: "a city rooftop at night: water tower, string lights, potted plants, the skyline behind" },
];
