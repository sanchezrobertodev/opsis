"use client";

import { type CSSProperties, useState } from "react";
import { cn } from "@/lib/cn";
import type { CaptionLook, FacelessStyle } from "@/lib/faceless";

const SAMPLE = ["and", "the", "town", "was", "asleep"];

/** A style shown by what you'll actually see of it: a sample picture in that style (`public/faceless/<id>.webp`,
 * else its own palette) under its burned-in caption treatment. */
export function StyleCard({
  style,
  selected,
  onSelect,
}: {
  style: FacelessStyle;
  selected: boolean;
  onSelect: () => void;
}) {
  const look = style.caption as unknown as CaptionLook;
  const [noSample, setNoSample] = useState(false);
  const stroke = `${look.outline}`;
  const text: CSSProperties = {
    fontFamily: `"${look.font}", sans-serif`,
    fontWeight: look.bold ? 700 : 400,
    color: look.color,
    textTransform: look.uppercase ? "uppercase" : undefined,
    textShadow: `-1.5px -1.5px 0 ${stroke}, 1.5px -1.5px 0 ${stroke}, -1.5px 1.5px 0 ${stroke}, 1.5px 1.5px 0 ${stroke}, 0 2px 6px rgb(0 0 0 / 0.45)`,
  };
  return (
    <button
      type="button"
      role="radio"
      aria-checked={selected}
      onClick={onSelect}
      className={cn(
        "group flex flex-col overflow-hidden rounded-[var(--radius-panel)] border bg-panel text-left transition-colors",
        selected ? "border-tungsten" : "border-transparent hover:border-line",
      )}
    >
      <span
        aria-hidden
        className="relative flex aspect-video items-end justify-center overflow-hidden px-3 pb-[9%]"
        style={{ background: `linear-gradient(160deg, ${look.highlight}33 0%, ${look.outline} 72%)` }}
      >
        {!noSample && (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={`/faceless/${style.id}.webp`}
            alt=""
            loading="lazy"
            onError={() => setNoSample(true)}
            className="absolute inset-0 size-full object-cover transition-transform duration-300 group-hover:scale-[1.04]"
          />
        )}
        <span className="relative text-[15px] leading-tight" style={text}>
          {SAMPLE.map((w, i) => (
            <span key={w} style={i === 2 ? { color: look.highlight } : undefined}>
              {w}
              {i < SAMPLE.length - 1 ? " " : ""}
            </span>
          ))}
        </span>
      </span>
      <span className="flex flex-col gap-0.5 px-3 py-2.5">
        <span className={cn("text-sm", selected ? "text-chalk" : "text-chalk/90")}>{style.label}</span>
        <span className="text-2xs leading-snug text-faint">{style.summary}</span>
      </span>
    </button>
  );
}
