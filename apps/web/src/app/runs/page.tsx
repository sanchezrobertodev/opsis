import type { Metadata } from "next";
import { Runs } from "@/components/runs/Runs";

export const metadata: Metadata = { title: "Runs" };

export default function RunsPage() {
  return <Runs />;
}
