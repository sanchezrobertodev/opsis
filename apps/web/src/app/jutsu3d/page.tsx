import type { Metadata } from "next";
import { Scenes } from "@/components/jutsu3d/Scenes";

export const metadata: Metadata = { title: "3D blocking" };

export default function Jutsu3dPage() {
  return <Scenes />;
}
