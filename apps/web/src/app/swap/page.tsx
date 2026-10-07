import type { Metadata } from "next";
import { Swap } from "@/components/swap/Swap";

export const metadata: Metadata = { title: "Object swap" };

export default function SwapPage() {
  return <Swap />;
}
