"use client";

import { useEffect, useRef, useState } from "react";
import { cn } from "@/lib/cn";

const BARS = 48;
// shortcut: kept for the visit and never trimmed, fine for a library's worth of sounds; trim if libraries get huge
const known = new Map<string, Promise<number[]>>();
let queue: Promise<unknown> = Promise.resolve(); // one file at a time, so a page of sounds doesn't decode at once

/** How loud each of BARS slices of the sound is, 0-1, read from the file in the browser once per visit. */
function peaks(src: string): Promise<number[]> {
  let found = known.get(src);
  if (!found) {
    found = queue.then(async () => {
      const r = await fetch(src, { credentials: "include" });
      if (!r.ok) throw new Error(`${r.status}`);
      // 8 kHz is plenty for a picture of loudness, and decodes far faster than the file's own rate
      const samples = (await new OfflineAudioContext(1, 1, 8000).decodeAudioData(await r.arrayBuffer())).getChannelData(0);
      const size = Math.max(1, Math.floor(samples.length / BARS));
      const loud = Array.from({ length: BARS }, (_, i) => {
        let top = 0;
        for (let j = i * size; j < Math.min(samples.length, (i + 1) * size); j++) top = Math.max(top, Math.abs(samples[j]));
        return top;
      });
      const most = Math.max(...loud, 1e-6);
      return loud.map((v) => v / most);
    });
    queue = found.catch(() => undefined);
    known.set(src, found);
  }
  return found;
}

/** A sound's waveform as bars, drawn once it scrolls into view; a flat line until then or if it can't be read. */
export function Waveform({ src, className }: { src: string; className?: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const [bars, setBars] = useState<number[] | null>(null);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    let live = true;
    const seen = new IntersectionObserver(([e]) => {
      if (!e?.isIntersecting) return;
      seen.disconnect();
      peaks(src).then((p) => live && setBars(p), () => live && setBars([]));
    });
    seen.observe(el);
    return () => {
      live = false;
      seen.disconnect();
    };
  }, [src]);
  const shown = bars?.length ? bars : Array<number>(BARS).fill(0);
  return (
    <div ref={ref} aria-hidden className={cn("flex items-center gap-[2px]", className)}>
      {shown.map((v, i) => (
        <span key={i} className="min-h-[3px] flex-1 rounded-full bg-gradient-to-t from-ember to-tungsten transition-[height] duration-500"
              style={{ height: `${v * 100}%` }} />
      ))}
    </div>
  );
}
