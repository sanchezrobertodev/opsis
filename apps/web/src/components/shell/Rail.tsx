"use client";

import { PanelLeftClose, PanelLeftOpen } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useSyncExternalStore } from "react";
import { cn } from "@/lib/cn";
import { STUDIOS, type Studio } from "@/lib/studios";

// collapsed or not, remembered in this browser (private windows just forget it)
const KEY = "rail-collapsed";
const listeners = new Set<() => void>();
let collapsedNow: boolean | undefined;

function readCollapsed() {
  if (collapsedNow === undefined) {
    try {
      collapsedNow = localStorage.getItem(KEY) === "1";
    } catch {
      collapsedNow = false;
    }
  }
  return collapsedNow;
}

function setCollapsed(value: boolean) {
  collapsedNow = value;
  try {
    localStorage.setItem(KEY, value ? "1" : "0");
  } catch {}
  listeners.forEach((l) => l());
}

const subscribe = (l: () => void) => {
  listeners.add(l);
  return () => void listeners.delete(l);
};

function RailItem({ studio, active, wide }: { studio: Studio; active: boolean; wide: boolean }) {
  const Icon = studio.icon;
  const soon = Boolean(studio.phase);
  return (
    <Link
      href={studio.href}
      aria-current={active ? "page" : undefined}
      title={soon ? `${studio.name} arrives in ${studio.phase}` : studio.name}
      className={cn(
        "group relative flex h-9 items-center gap-3 rounded-[var(--radius-control)] px-3 text-sm transition-colors",
        active ? "lumen-selected bg-select text-chalk" : "text-muted hover:bg-chalk/5 hover:text-chalk",
        soon && !active && "text-faint",
      )}
    >
      <Icon className={cn("size-4 shrink-0", active && "text-tungsten")} strokeWidth={1.75} aria-hidden />
      <span className={cn("hidden truncate", wide && "lg:inline")}>{studio.name}</span>
      {soon && <span className={cn("ml-auto hidden text-2xs text-faint tabular", wide && "lg:inline")}>{studio.phase}</span>}
    </Link>
  );
}

export function Rail() {
  const pathname = usePathname();
  const wide = !useSyncExternalStore(subscribe, readCollapsed, () => false);
  const isActive = (s: Studio) =>
    pathname === s.href || (s.href !== "/" && pathname.startsWith(`${s.href}/`));
  const group = (g: Studio["group"]) => STUDIOS.filter((s) => s.group === g);
  const Toggle = wide ? PanelLeftClose : PanelLeftOpen;

  return (
    <nav
      aria-label="Studios"
      className={cn(
        "flex w-14 shrink-0 flex-col overflow-x-hidden overflow-y-auto glass border-r border-line-soft px-2 py-4 scrollbar-quiet",
        wide && "lg:w-56 lg:px-3",
      )}
    >
      <Link href="/create/video" aria-label="Opsis" className={cn("mb-6 flex h-8 items-center gap-2.5 px-1", wide && "lg:px-2.5")}>
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src="/opsis.webp" alt="" width={26} height={26} className="size-[26px] shrink-0" />
        <span className={cn("type-title hidden text-lg", wide && "lg:inline")}>Opsis</span>
      </Link>

      <div className="flex flex-col gap-0.5">
        {group("create").map((s) => (
          <RailItem key={s.slug} studio={s} active={isActive(s)} wide={wide} />
        ))}
      </div>

      <p className={cn("mt-6 mb-2 hidden px-3 text-2xs text-faint", wide && "lg:block")}>Studios</p>
      <div className={cn("mt-4 flex flex-col gap-0.5", wide && "lg:mt-0")}>
        {group("studios").map((s) => (
          <RailItem key={s.slug} studio={s} active={isActive(s)} wide={wide} />
        ))}
      </div>

      <div className="mt-auto flex flex-col gap-0.5 border-t border-line-soft pt-3">
        {group("manage").map((s) => (
          <RailItem key={s.slug} studio={s} active={isActive(s)} wide={wide} />
        ))}
        {/* below lg the rail is always narrow, so the toggle only shows where it changes something */}
        <button
          type="button"
          onClick={() => setCollapsed(wide)}
          aria-label={wide ? "Collapse the sidebar" : "Expand the sidebar"}
          title={wide ? "Collapse the sidebar" : "Expand the sidebar"}
          className="hidden h-9 items-center gap-3 rounded-[var(--radius-control)] px-3 text-sm text-faint transition-colors hover:bg-chalk/5 hover:text-chalk lg:flex"
        >
          <Toggle className="size-4 shrink-0" strokeWidth={1.75} aria-hidden />
          {wide && <span className="truncate">Collapse</span>}
        </button>
      </div>
    </nav>
  );
}
