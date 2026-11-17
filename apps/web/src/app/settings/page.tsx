import type { Metadata } from "next";
import { GeneralSettings } from "@/components/shell/Settings";

export const metadata: Metadata = { title: "Settings" };

export default function SettingsPage() {
  return <GeneralSettings />;
}
