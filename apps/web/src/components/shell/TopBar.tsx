"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn, usd } from "@/lib/cn";
import { useBudget, useGenerations, useRegistry } from "@/lib/queries";
import { isActive } from "@/lib/api";
import { useConnection } from "@/lib/realtime";
import { STUDIOS } from "@/lib/studios";

export function TopBar() {
  const pathname = usePathname();
  const studio = STUDIOS.find((s) => pathname === s.href || pathname.startsWith(`${s.href}/`));
  const { data: registry } = useRegistry();
  const { data: budget } = useBudget();
  const { data: gens } = useGenerations();
  const connection = useConnection();
  const running = gens?.items.filter((g) => isActive(g.status)).length ?? 0;
  const mode = registry?.provider_mode;

  return (
    <header className="flex h-14 shrink-0 items-center gap-4 border-b border-line-soft px-5">
      <h1 className="type-title truncate text-lg">{studio?.name ?? "Studio"}</h1>

      <div className="ml-auto flex items-center gap-2 text-xs text-muted">
        {running > 0 && (
          <span className="tabular rounded-full border border-line-soft px-3 py-1 text-chalk">
            {running} rendering
          </span>
        )}
        {mode && (
          <Link
            href="/settings"
            className={cn(
              "inline-flex items-center gap-1.5 rounded-full border px-3 py-1 transition-colors",
              mode === "mock"
                ? "border-line-soft hover:bg-chalk/5 hover:text-chalk"
                : "border-tungsten/30 bg-tungsten/10 text-tungsten hover:bg-tungsten/20",
            )}
            title={
              mode === "mock"
                ? "Generations use the local mock provider. Nothing is billed."
                : "Generations call real providers and are billed."
            }
          >
            {mode === "mock" && <span className="size-1.5 rounded-full bg-steel" aria-hidden />}
            {mode === "mock" ? "Mock mode, nothing is billed" : mode === "mixed" ? "Mixed mode" : "Live"}
          </Link>
        )}
        {budget && (
          <Link href="/settings" className="readout rounded-full border border-line-soft px-3 py-1 transition-colors hover:bg-chalk/5 hover:text-chalk">
            {usd(budget.today_usd)} today
          </Link>
        )}
        <span
          className={cn(
            "size-2 rounded-full",
            connection === "live" ? "bg-done" : connection === "connecting" ? "bg-tungsten-dim" : "bg-fault",
          )}
          role="status"
          aria-label={
            connection === "live" ? "Live updates connected" : connection === "connecting" ? "Connecting" : "API offline"
          }
          title={
            connection === "live"
              ? "Live updates connected"
              : connection === "connecting"
                ? "Connecting to the API"
                : "API offline. Start it with pnpm dev."
          }
        />
      </div>
    </header>
  );
}
