import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function usd(value: number | null | undefined, opts: { free?: string } = {}) {
  if (value === null || value === undefined) return "–";
  if (value === 0 && opts.free) return opts.free;
  return `$${value < 1 && value > 0 ? value.toFixed(3).replace(/0$/, "") : value.toFixed(2)}`;
}

export function seconds(ms: number | null | undefined) {
  if (!ms && ms !== 0) return "";
  const s = ms / 1000;
  return s < 10 ? `${s.toFixed(1)}s` : `${Math.round(s)}s`;
}

/** Elapsed time as a slate timecode, mm:ss. */
export function timecode(fromIso: string | null | undefined, to: number = Date.now()) {
  if (!fromIso) return "00:00";
  const total = Math.max(0, Math.floor((to - new Date(fromIso).getTime()) / 1000));
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}
