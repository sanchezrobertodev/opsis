import {
  Box,
  Clapperboard,
  Film,
  GalleryHorizontalEnd,
  Globe2,
  ImageIcon,
  LayoutGrid,
  type LucideIcon,
  MessageCircle,
  Mic,
  Move3d,
  PersonStanding,
  Replace,
  Scissors,
  Settings,
  Sparkles,
  UserRound,
  Workflow,
} from "lucide-react";

export type Studio = {
  slug: string;
  name: string;
  href: string;
  icon: LucideIcon;
  /** Roadmap phase that ships it (docs/PLAN.md). Ready studios have none. */
  phase?: string;
  summary: string;
  group: "create" | "studios" | "manage";
};

export const STUDIOS: Studio[] = [
  {
    slug: "create-video", name: "Create video", href: "/create/video", icon: Clapperboard, group: "create",
    summary: "Text, image or first-and-last frame to video across Veo, Kling, Seedance and more.",
  },
  {
    slug: "create-image", name: "Create image", href: "/create/image", icon: ImageIcon, group: "create",
    summary: "Keyframes and references for your shots.",
  },
  {
    slug: "sequence", name: "Shot sequence", href: "/sequence", icon: GalleryHorizontalEnd, group: "create",
    summary: "Shots that continue from each other's last frame, joined into one cut.",
  },
  {
    slug: "presets", name: "Presets", href: "/presets", icon: Sparkles, group: "create",
    summary: "One-click camera moves, effects, looks and edits. Fill in a blank or two and generate.",
  },
  {
    slug: "edit", name: "Edit video", href: "/edit", icon: Scissors, group: "studios",
    summary: "Change footage by instruction, restyle, relight, extend, reframe, upscale, smooth motion, " +
      "remove backgrounds, add sound, or trim, retime, reverse, loop and stabilize on this computer.",
  },
  {
    slug: "motion", name: "Motion control", href: "/motion", icon: Move3d, group: "studios",
    summary: "A library of 65 camera moves (crash zoom, dolly zoom, bullet time, FPV, 360 orbit) you can stack. " +
      "Drawn trajectories that steer how things move arrive with the open models in P3.",
  },
  {
    slug: "motion-transfer", name: "Motion transfer", href: "/motion-transfer", icon: PersonStanding,
    group: "studios",
    summary: "Make a character image perform a reference clip, or put your character into it. Kling Motion " +
      "Control, Wan Animate and Runway Act-Two, with a library of the movement clips you've used.",
  },
  {
    slug: "swap", name: "Object swap", href: "/swap", icon: Replace, group: "studios",
    summary: "Click an object in a frame, track it through the clip, and replace or remove it while the " +
      "camera, lighting and motion stay as filmed.",
  },
  {
    slug: "characters", name: "Characters", href: "/characters", icon: UserRound, group: "studios",
    summary: "Reusable characters, locations and props. Mention them with @name in any prompt and their " +
      "references attach automatically. Character sheets and optional trained identities.",
  },
  {
    slug: "worlds", name: "Worlds", href: "/soon/worlds", icon: Globe2, phase: "P7", group: "studios",
    summary: "360° panoramas and explorable Gaussian-splat worlds. Fly a camera path and turn it into " +
      "start frames and camera references for video models.",
  },
  {
    slug: "faceless", name: "Faceless studio", href: "/faceless", icon: Mic, group: "studios",
    summary: "Topic or script to a finished narrated video: style, cast, script, clips or narrated stills, " +
      "voiceover, music, captions and a thumbnail.",
  },
  {
    slug: "cinema", name: "Cinema studio", href: "/cinema", icon: Film, group: "studios",
    summary: "Scenes and shots on a storyboard, with camera body, lens, aperture, genre, era, lighting and " +
      "palette controls. Hero frames, multi-shot takes and a timeline export.",
  },
  {
    slug: "jutsu3d", name: "3D blocking", href: "/soon/jutsu3d", icon: Box, phase: "P7", group: "studios",
    summary: "Block a scene in 3D with mannequins, props and a camera path, then turn the render into " +
      "photoreal angles and animate them.",
  },
  {
    slug: "lipsync", name: "Lipsync", href: "/soon/lipsync", icon: MessageCircle, phase: "P6", group: "studios",
    summary: "Make an image or clip speak a script or recording: Sync lipsync-3, Kling Avatar, OmniHuman, " +
      "VEED Fabric and InfiniteTalk.",
  },
  {
    slug: "library", name: "Library", href: "/library", icon: LayoutGrid, group: "manage",
    summary: "Every upload and generated take.",
  },
  {
    slug: "runs", name: "Runs", href: "/runs", icon: Workflow, group: "manage",
    summary: "Multi-step jobs: sequences and model comparisons, with their steps and results.",
  },
  {
    slug: "settings", name: "Settings", href: "/settings", icon: Settings, group: "manage",
    summary: "Spending limits and provider status.",
  },
];

export function studioBySlug(slug: string) {
  return STUDIOS.find((s) => s.slug === slug);
}
