import type { Metadata } from "next";
import { Faceless } from "@/components/faceless/Faceless";

export const metadata: Metadata = { title: "Faceless studio" };

export default function FacelessPage() {
  return <Faceless />;
}
