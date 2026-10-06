import type { Metadata } from "next";
import { CreateStudio } from "@/components/create/CreateStudio";

export const metadata: Metadata = { title: "Create video" };

export default function CreateVideoPage() {
  return <CreateStudio studio="video" />;
}
