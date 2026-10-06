import type { Metadata } from "next";
import { EditStudio } from "@/components/edit/EditStudio";

export const metadata: Metadata = { title: "Edit video" };

export default function EditPage() {
  return <EditStudio />;
}
