import type { Metadata } from "next";
import { Characters } from "@/components/characters/Characters";

export const metadata: Metadata = { title: "Characters" };

export default function CharactersPage() {
  return <Characters />;
}
