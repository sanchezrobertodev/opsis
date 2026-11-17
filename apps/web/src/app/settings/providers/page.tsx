import type { Metadata } from "next";
import { Providers } from "@/components/shell/Providers";

export const metadata: Metadata = { title: "Providers" };

export default function ProvidersPage() {
  return <Providers />;
}
