import type { Metadata } from "next";
import { SpendingSettings } from "@/components/shell/Settings";

export const metadata: Metadata = { title: "Spending settings" };

export default function SpendingSettingsPage() {
  return <SpendingSettings />;
}
