import type { Metadata } from "next";
import { Lipsync } from "@/components/lipsync/Lipsync";

export const metadata: Metadata = { title: "Lipsync" };

export default function LipsyncPage() {
  return <Lipsync />;
}
