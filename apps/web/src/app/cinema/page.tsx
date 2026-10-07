import type { Metadata } from "next";
import { CinemaProjects } from "@/components/cinema/Projects";

export const metadata: Metadata = { title: "Cinema studio" };

export default function CinemaPage() {
  return <CinemaProjects />;
}
