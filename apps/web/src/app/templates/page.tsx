import type { Metadata } from "next";
import { Presets } from "@/components/presets/Presets";

export const metadata: Metadata = { title: "Templates" };

export default function TemplatesPage() {
  return <Presets />;
}
