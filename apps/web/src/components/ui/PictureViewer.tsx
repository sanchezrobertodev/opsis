"use client";

import { Maximize2, X } from "lucide-react";
import { useEffect, useRef } from "react";
import { create } from "zustand";
import { cn } from "@/lib/cn";

type Shown = { src: string; title: string };

const useShown = create<{ shown: Shown | null; set: (shown: Shown | null) => void }>((set) => ({
  shown: null,
  set: (shown) => set({ shown }),
}));

/** Opens a picture at full size, from anywhere (even inside another dialog: the viewer opens on top). */
export const showPicture = (picture: Shown) => useShown.getState().set(picture);

/** The one full-size viewer, mounted once by the app's providers. Escape, the backdrop or × closes it. */
export function PictureViewer() {
  const { shown, set } = useShown();
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (shown && !el.open) el.showModal();
    if (!shown && el.open) el.close();
  }, [shown]);
  return (
    <dialog
      ref={ref}
      aria-label={shown?.title || "Picture"}
      onClose={(e) => e.target === e.currentTarget && set(null)}
      onClick={(e) => e.target === ref.current && set(null)}
      className="m-auto max-h-none max-w-none overflow-visible bg-transparent p-0 backdrop:bg-black/85"
    >
      {shown && (
        <figure className="flex flex-col items-center gap-2">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src={shown.src} alt={shown.title} className="block max-h-[86dvh] max-w-[94vw] rounded-[var(--radius-frame)] object-contain" />
          <figcaption className="flex max-w-[94vw] items-center gap-3 text-sm text-chalk">
            <span className="truncate">{shown.title}</span>
            <button type="button" onClick={() => set(null)} aria-label="Close"
                    className="grid size-8 shrink-0 place-items-center rounded-full bg-raised text-muted hover:text-chalk">
              <X className="size-4" aria-hidden />
            </button>
          </figcaption>
        </figure>
      )}
    </dialog>
  );
}

/** The corner button over a picture. Put it beside the picture in a `group relative` box, never inside another
 * button. Without `onClick` it opens `src` in the shared viewer. */
export function ZoomButton({ src, title, onClick, className }: {
  src?: string; title: string; onClick?: () => void; className?: string;
}) {
  return (
    <button
      type="button"
      aria-label={`See ${title} full size`}
      title="See it full size"
      onClick={(e) => {
        e.stopPropagation();
        if (onClick) onClick();
        else if (src) showPicture({ src, title });
      }}
      className={cn(
        "absolute right-2 top-2 z-10 grid size-7 place-items-center rounded-[4px] bg-black/55 text-white opacity-70",
        "transition-opacity hover:opacity-100 focus-visible:opacity-100 group-hover:opacity-100",
        className,
      )}
    >
      <Maximize2 className="size-3.5" aria-hidden />
    </button>
  );
}

/** A picture that opens full size when clicked (for thumbnails too small for a corner button). */
export function OpensFullSize({ src, title, className, children }: {
  src: string; title: string; className?: string; children: React.ReactNode;
}) {
  return (
    <button type="button" aria-label={`See ${title} full size`} onClick={() => showPicture({ src, title })}
            className={cn("block cursor-zoom-in", className)}>
      {children}
    </button>
  );
}
