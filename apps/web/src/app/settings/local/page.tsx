import type { Metadata } from "next";
import { LocalSettings } from "@/components/shell/LocalModels";

export const metadata: Metadata = { title: "Local models" };

export default function LocalSettingsPage() {
  return <LocalSettings />;
}
