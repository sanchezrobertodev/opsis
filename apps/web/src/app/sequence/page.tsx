import type { Metadata } from "next";
import { Sequence } from "@/components/sequence/Sequence";

export const metadata: Metadata = { title: "Shot sequence" };

export default function SequencePage() {
  return <Sequence />;
}
