import { cn } from "@/lib/cn";

/** The frame takes wear on Create: a frosted card with the picture set into it. */
export const GLASS_CARD = "glass overflow-hidden rounded-2xl border border-line-soft p-2 transition-colors hover:border-line";

/** A picture lit from behind by a blurred copy of itself, which spills onto the glass around it. */
export function Lit({ picture, className, children }: { picture?: string; className?: string; children: React.ReactNode }) {
  return (
    <div className={cn("lumen", className)}>
      {picture && <div aria-hidden className="lumen-glow" style={{ backgroundImage: `url("${picture}")` }} />}
      {children}
    </div>
  );
}
