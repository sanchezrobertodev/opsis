import type { Metadata } from "next";
import { CreateStudio } from "@/components/create/CreateStudio";

export const metadata: Metadata = { title: "Motion transfer" };

export default function MotionTransferPage() {
  return <CreateStudio studio="motion" />;
}
