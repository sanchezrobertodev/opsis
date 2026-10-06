import type { Metadata } from "next";
import { CreateStudio } from "@/components/create/CreateStudio";

export const metadata: Metadata = { title: "Create image" };

export default function CreateImagePage() {
  return <CreateStudio studio="image" />;
}
