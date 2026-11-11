"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "@/lib/cn";

const TABS = [
  ["/settings", "General"],
  ["/settings/spending", "Spending"],
  ["/settings/providers", "Providers"],
  ["/settings/local", "Local models"],
] as const;

/** The Settings page's sections, each its own address so other pages can link to one. */
export function SettingsNav() {
  const pathname = usePathname();
  return (
    <nav aria-label="Settings sections"
         className="sticky top-0 z-10 flex gap-1 overflow-x-auto border-b border-line-soft bg-bay/85 px-6 backdrop-blur scrollbar-quiet">
      {TABS.map(([href, label]) => {
        const current = pathname === href;
        return (
          <Link key={href} href={href} aria-current={current ? "page" : undefined}
                className={cn("-mb-px shrink-0 border-b-2 px-3 py-3 text-sm transition-colors",
                  current ? "border-tungsten text-chalk" : "border-transparent text-muted hover:text-chalk")}>
            {label}
          </Link>
        );
      })}
    </nav>
  );
}
