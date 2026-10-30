"use client";

import { useEffect, useRef, useState } from "react";
import { cn } from "@/lib/cn";

/** The frame takes wear on Create: a frosted card with the picture set into it. */
export const GLASS_CARD = "glass overflow-hidden rounded-2xl border border-line-soft p-2 transition-colors hover:border-line";

/** A picture lit from behind by a blurred copy of itself, which spills onto the glass around it. The copy loads only
 * once the card nears the screen, like the lazy picture it copies. */
export function Lit({ picture, className, children }: { picture?: string; className?: string; children: React.ReactNode }) {
  const ref = useRef<HTMLDivElement>(null);
  const [near, setNear] = useState(false);
  useEffect(() => {
    const el = ref.current;
    if (!el || near || !picture) return;
    const seen = new IntersectionObserver(([e]) => {
      if (!e?.isIntersecting) return;
      setNear(true);
      seen.disconnect();
    }, { rootMargin: "300px" });
    seen.observe(el);
    return () => seen.disconnect();
  }, [near, picture]);
  return (
    <div ref={ref} className={cn("lumen", className)}>
      {picture && near && <div aria-hidden className="lumen-glow" style={{ backgroundImage: `url("${picture}")` }} />}
      {children}
    </div>
  );
}
