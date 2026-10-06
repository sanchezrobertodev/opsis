"use client";

import { cva, type VariantProps } from "class-variance-authority";
import { forwardRef, useEffect, useId, useRef } from "react";
import { cn } from "@/lib/cn";

// ---------------------------------------------------------------- Button

const button = cva(
  "inline-flex items-center justify-center gap-2 rounded-[var(--radius-control)] font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-45",
  {
    variants: {
      tone: {
        primary: "bg-tungsten text-bay hover:bg-[#f5b955]",
        quiet: "bg-raised text-chalk hover:bg-line",
        ghost: "text-muted hover:bg-raised hover:text-chalk",
        danger: "bg-fault/15 text-fault hover:bg-fault/25",
      },
      size: { sm: "h-8 px-3 text-xs", md: "h-10 px-4 text-sm", lg: "h-12 px-5 text-[15px]" },
    },
    defaultVariants: { tone: "quiet", size: "md" },
  },
);

export type ButtonProps = React.ButtonHTMLAttributes<HTMLButtonElement> & VariantProps<typeof button>;

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(({ className, tone, size, ...props }, ref) => (
  <button ref={ref} className={cn(button({ tone, size }), className)} {...props} />
));
Button.displayName = "Button";

// ---------------------------------------------------------------- Field label

export function Field({
  label,
  hint,
  htmlFor,
  children,
  aside,
}: {
  label: string;
  hint?: string;
  htmlFor?: string;
  children: React.ReactNode;
  aside?: React.ReactNode;
}) {
  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-baseline justify-between gap-3">
        <label htmlFor={htmlFor} className="text-xs text-muted">
          {label}
        </label>
        {aside}
      </div>
      {children}
      {hint && <p className="text-2xs text-faint">{hint}</p>}
    </div>
  );
}

// ---------------------------------------------------------------- Segmented

export function Segmented<T extends string | number>({
  value,
  options,
  onChange,
  label,
  render,
}: {
  value: T | undefined;
  options: readonly T[];
  onChange: (v: T) => void;
  label: string;
  render?: (v: T) => React.ReactNode;
}) {
  return (
    <div role="radiogroup" aria-label={label} className="flex flex-wrap gap-1 rounded-[var(--radius-control)] bg-bay p-1">
      {options.map((opt) => {
        const selected = opt === value;
        return (
          <button
            key={String(opt)}
            type="button"
            role="radio"
            aria-checked={selected}
            onClick={() => onChange(opt)}
            className={cn(
              "tabular min-w-10 flex-1 rounded-[4px] px-2.5 py-1.5 text-xs transition-colors",
              selected ? "bg-raised text-chalk" : "text-muted hover:text-chalk",
            )}
          >
            {render ? render(opt) : String(opt)}
          </button>
        );
      })}
    </div>
  );
}

/** Aspect ratio picker: each option drawn at its own proportions. */
export function AspectPicker({
  value,
  options,
  onChange,
}: {
  value: string | undefined;
  options: readonly string[];
  onChange: (v: string) => void;
}) {
  return (
    <div role="radiogroup" aria-label="Aspect ratio" className="flex flex-wrap gap-1.5">
      {options.map((opt) => {
        const [w, h] = opt.split(":").map(Number);
        const scale = 18 / Math.max(w, h);
        const selected = opt === value;
        return (
          <button
            key={opt}
            type="button"
            role="radio"
            aria-checked={selected}
            onClick={() => onChange(opt)}
            className={cn(
              "flex h-12 min-w-12 flex-col items-center justify-center gap-1 rounded-[var(--radius-control)] border px-2 transition-colors",
              selected ? "border-tungsten/60 bg-raised text-chalk" : "border-line-soft text-muted hover:border-line hover:text-chalk",
            )}
          >
            <span
              aria-hidden
              className={cn("rounded-[2px] border", selected ? "border-tungsten" : "border-current")}
              style={{ width: w * scale, height: h * scale }}
            />
            <span className="tabular text-2xs">{opt}</span>
          </button>
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------- Switch

export function Switch({
  checked,
  onChange,
  label,
  id,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
  label: string;
  id?: string;
}) {
  return (
    <button
      id={id}
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      onClick={() => onChange(!checked)}
      className={cn(
        "relative h-6 w-10 shrink-0 rounded-full transition-colors",
        checked ? "bg-tungsten" : "bg-line",
      )}
    >
      <span
        aria-hidden
        className={cn(
          "absolute top-1 size-4 rounded-full bg-chalk transition-[left]",
          checked ? "left-5 bg-bay" : "left-1",
        )}
      />
    </button>
  );
}

// ---------------------------------------------------------------- Slider

export function Slider({
  value,
  min,
  max,
  step = 1,
  onChange,
  id,
  format,
}: {
  value: number;
  min: number;
  max: number;
  step?: number;
  onChange: (v: number) => void;
  id?: string;
  format?: (v: number) => string;
}) {
  const pct = ((value - min) / (max - min || 1)) * 100;
  return (
    <div className="flex items-center gap-3">
      <input
        id={id}
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="h-1.5 flex-1 cursor-pointer appearance-none rounded-full accent-tungsten"
        style={{
          background: `linear-gradient(to right, var(--color-tungsten) ${pct}%, var(--color-line) ${pct}%)`,
        }}
      />
      <span className="tabular w-12 text-right text-sm text-chalk">{format ? format(value) : value}</span>
    </div>
  );
}

// ---------------------------------------------------------------- Select

export function Select({
  value,
  options,
  onChange,
  id,
}: {
  value: string;
  options: { value: string; label: string }[];
  onChange: (v: string) => void;
  id?: string;
}) {
  return (
    <select
      id={id}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className="h-10 rounded-[var(--radius-control)] border border-line-soft bg-bay px-3 text-sm text-chalk outline-none focus:border-tungsten/60"
    >
      {options.map((o) => (
        <option key={o.value} value={o.value}>
          {o.label}
        </option>
      ))}
    </select>
  );
}

// ---------------------------------------------------------------- Dialog

export function Dialog({
  open,
  onClose,
  title,
  children,
  footer,
  wide,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  children: React.ReactNode;
  footer?: React.ReactNode;
  wide?: boolean;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (open && !el.open) el.showModal();
    if (!open && el.open) el.close();
  }, [open]);
  return (
    <dialog
      ref={ref}
      aria-labelledby={titleId}
      onClose={onClose}
      onClick={(e) => e.target === ref.current && onClose()}
      className={cn(
        "m-auto w-[calc(100%-2rem)] rounded-[var(--radius-panel)] border border-line bg-panel p-0 text-chalk backdrop:bg-black/60",
        wide ? "max-w-3xl" : "max-w-md",
      )}
    >
      {open && (
        <div className="flex flex-col gap-4 p-6">
          <h2 id={titleId} className="type-title text-lg">
            {title}
          </h2>
          <div className="text-sm text-muted">{children}</div>
          {footer && <div className="flex justify-end gap-2 pt-2">{footer}</div>}
        </div>
      )}
    </dialog>
  );
}
