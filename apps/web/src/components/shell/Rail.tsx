"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "@/lib/cn";
import { STUDIOS, type Studio } from "@/lib/studios";

function RailItem({ studio, active }: { studio: Studio; active: boolean }) {
  const Icon = studio.icon;
  const soon = Boolean(studio.phase);
  return (
    <Link
      href={studio.href}
      aria-current={active ? "page" : undefined}
      title={soon ? `${studio.name} arrives in ${studio.phase}` : studio.name}
      className={cn(
        "group relative flex h-9 items-center gap-3 rounded-[var(--radius-control)] px-3 text-sm transition-colors",
        active ? "bg-raised text-chalk" : "text-muted hover:bg-panel hover:text-chalk",
        soon && !active && "text-faint",
      )}
    >
      {active && <span className="absolute inset-y-2 left-0 w-0.5 rounded-full bg-tungsten" aria-hidden />}
      <Icon className="size-4 shrink-0" strokeWidth={1.75} aria-hidden />
      <span className="hidden truncate lg:inline">{studio.name}</span>
      {soon && <span className="ml-auto hidden text-2xs text-faint tabular lg:inline">{studio.phase}</span>}
    </Link>
  );
}

export function Rail() {
  const pathname = usePathname();
  const isActive = (s: Studio) =>
    pathname === s.href || (s.href !== "/" && pathname.startsWith(`${s.href}/`));
  const group = (g: Studio["group"]) => STUDIOS.filter((s) => s.group === g);

  return (
    <nav
      aria-label="Studios"
      className="flex w-14 shrink-0 flex-col border-r border-line-soft bg-bay px-2 py-4 lg:w-56 lg:px-3"
    >
      <Link href="/create/video" className="mb-6 flex h-8 items-center gap-2 px-1 lg:px-3">
        <span
          aria-hidden
          className="grid size-6 place-items-center rounded-[4px] bg-tungsten text-[11px] font-bold text-bay"
        >
          M
        </span>
        <span className="type-title hidden text-[15px] lg:inline">Media Studio</span>
      </Link>

      <div className="flex flex-col gap-0.5">
        {group("create").map((s) => (
          <RailItem key={s.slug} studio={s} active={isActive(s)} />
        ))}
      </div>

      <p className="mt-6 mb-2 hidden px-3 text-2xs text-faint lg:block">Studios in the roadmap</p>
      <div className="mt-4 flex flex-col gap-0.5 lg:mt-0">
        {group("studios").map((s) => (
          <RailItem key={s.slug} studio={s} active={isActive(s)} />
        ))}
      </div>

      <div className="mt-auto flex flex-col gap-0.5 border-t border-line-soft pt-3">
        {group("manage").map((s) => (
          <RailItem key={s.slug} studio={s} active={isActive(s)} />
        ))}
      </div>
    </nav>
  );
}
