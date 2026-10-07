import type { Metadata } from "next";
import { Suspense } from "react";
import { Swap } from "@/components/swap/Swap";

export const metadata: Metadata = { title: "Object swap" };

export default function SwapPage() {
  return (
    <Suspense>
      <Swap />
    </Suspense>
  );
}
