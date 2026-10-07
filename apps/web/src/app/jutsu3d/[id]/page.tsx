import type { Metadata } from "next";
import { SceneEditorPage } from "@/components/jutsu3d/Scenes";

export const metadata: Metadata = { title: "Scene" };

export default async function ScenePage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <SceneEditorPage id={id} />;
}
