import type { Metadata } from "next";
import { Motion } from "@/components/motion/Motion";

export const metadata: Metadata = { title: "Motion control" };

export default function MotionPage() {
  return <Motion />;
}
