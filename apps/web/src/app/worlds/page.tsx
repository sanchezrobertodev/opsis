import type { Metadata } from "next";
import { Worlds } from "@/components/worlds/Worlds";

export const metadata: Metadata = { title: "Worlds" };

export default function WorldsPage() {
  return <Worlds />;
}
