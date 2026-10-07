import type { Metadata } from "next";
import { CinemaEditor } from "@/components/cinema/Editor";

export const metadata: Metadata = { title: "Film" };

export default async function FilmPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <CinemaEditor id={id} />;
}
