import type { Metadata } from "next";
import { Presets } from "@/components/presets/Presets";

export const metadata: Metadata = { title: "Presets" };

export default function PresetsPage() {
  return <Presets />;
}
