import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { studioBySlug } from "@/lib/studios";

export async function generateMetadata(props: PageProps<"/soon/[studio]">): Promise<Metadata> {
  const { studio } = await props.params;
  return { title: studioBySlug(studio)?.name ?? "Studio" };
}

export default async function SoonPage(props: PageProps<"/soon/[studio]">) {
  const { studio: slug } = await props.params;
  const studio = studioBySlug(slug);
  if (!studio?.phase) notFound();
  const Icon = studio.icon;
  return (
    <div className="h-full overflow-y-auto p-6">
      <div className="mt-16 max-w-xl">
        <Icon className="size-8 text-tungsten" strokeWidth={1.5} aria-hidden />
        <h2 className="type-title mt-5 text-3xl">Arrives in phase {studio.phase.replace("P", "")}</h2>
        <p className="mt-4 text-[15px] leading-relaxed text-muted">{studio.summary}</p>
        <p className="mt-6 text-sm text-faint">
          The roadmap lives in docs/PLAN.md. Everything this studio generates will land in your library
          alongside your other takes.
        </p>
        <Link
          href="/create/video"
          className="mt-8 inline-flex h-10 items-center rounded-[var(--radius-control)] bg-raised px-4 text-sm hover:bg-line"
        >
          Go to Create video
        </Link>
      </div>
    </div>
  );
}
