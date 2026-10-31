"use client";

import { useQuery } from "@tanstack/react-query";
import { Dices } from "lucide-react";
import { useId } from "react";
import { api, type Param, unwrap } from "@/lib/api";
import { AspectPicker, Field, Segmented, Select, Slider, Switch } from "@/components/ui/controls";
import { ShotsField } from "./ShotsField";

export function humanize(name: string) {
  const s = name.replace(/_/g, " ");
  return s.charAt(0).toUpperCase() + s.slice(1);
}

export function isVisible(param: Param, values: Record<string, unknown>) {
  if (!param.visible_if) return true;
  return Object.entries(param.visible_if).every(([key, expected]) => {
    const options = Array.isArray(expected) ? expected : [expected];
    return options.some((o) => String(o) === String(values[key]));
  });
}

export function defaultsFor(params: Param[]): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const p of params) if (p.default !== undefined && p.default !== null) out[p.name] = p.default;
  return out;
}

export function ParamField({
  param,
  value,
  onChange,
}: {
  param: Param;
  value: unknown;
  onChange: (v: unknown) => void;
}) {
  const id = useId();
  const label = param.label ?? humanize(param.name);

  switch (param.type) {
    case "enum": {
      if (param.comfy_choices) {
        return <ComfyChoiceField field={param.comfy_choices} label={label} help={param.help} value={value} onChange={onChange} />;
      }
      const options = param.options as (string | number)[];
      const labels = (param.labels ?? {}) as Record<string, string>;
      const show = (o: string | number) => labels[String(o)] ?? String(o);
      if (param.ui === "aspect") {
        return (
          <Field label={label} hint={param.help ?? undefined}>
            <AspectPicker value={value as string} options={options.map(String)} onChange={onChange} />
          </Field>
        );
      }
      // no default means the model needs none (Cinema Studio's look), so it can stay unset
      const optional = param.default === undefined || param.default === null;
      if (!optional && (param.ui === "segmented" || options.length <= 4)) {
        return (
          <Field label={label} hint={param.help ?? undefined}>
            <Segmented label={label} value={value as string | number} options={options} onChange={onChange} render={show} />
          </Field>
        );
      }
      return (
        <Field label={label} htmlFor={id} hint={param.help ?? undefined}>
          <Select
            id={id}
            value={String(value ?? "")}
            options={[...(optional ? [{ value: "", label: "Not set" }] : []),
                      ...options.map((o) => ({ value: String(o), label: show(o) }))]}
            onChange={(v) => onChange(v === "" ? undefined : options.find((o) => String(o) === v) ?? v)}
          />
        </Field>
      );
    }
    case "int":
    case "float": {
      const step = param.step ?? (param.type === "int" ? 1 : 0.01);
      const num = typeof value === "number" ? value : Number(param.default ?? param.min);
      const unit = param.name === "duration" ? "s" : "";
      return (
        <Field label={label} htmlFor={id} hint={param.help ?? undefined}>
          <Slider
            id={id}
            value={num}
            min={param.min}
            max={param.max}
            step={step}
            onChange={onChange}
            format={(v) => (param.type === "float" ? v.toFixed(2) : `${v}${unit}`)}
          />
        </Field>
      );
    }
    case "bool":
      return (
        <div className="flex items-center justify-between gap-4">
          <label htmlFor={id} className="text-sm text-chalk">
            {label}
            {param.help && <span className="block text-2xs text-faint">{param.help}</span>}
          </label>
          <Switch id={id} label={label} checked={Boolean(value)} onChange={onChange} />
        </div>
      );
    case "seed":
      return (
        <Field label={label} htmlFor={id} hint="Leave empty for a new random seed each take.">
          <div className="flex gap-2">
            <input
              id={id}
              inputMode="numeric"
              placeholder="Random"
              value={value === undefined || value === null ? "" : String(value)}
              onChange={(e) => {
                const raw = e.target.value.replace(/\D/g, "");
                onChange(raw === "" ? undefined : Number(raw));
              }}
              className="tabular h-10 min-w-0 flex-1 rounded-[var(--radius-control)] border border-line-soft bg-bay px-3 text-sm outline-none focus:border-tungsten/60"
            />
            <button
              type="button"
              aria-label="Pick a random seed"
              onClick={() => onChange(Math.floor(Math.random() * 2 ** 31))}
              className="grid size-10 place-items-center rounded-[var(--radius-control)] bg-raised text-muted hover:text-chalk"
            >
              <Dices className="size-4" aria-hidden />
            </button>
          </div>
        </Field>
      );
    case "text":
      return (
        <Field label={label} htmlFor={id} hint={param.help ?? undefined}>
          <textarea
            id={id}
            rows={2}
            value={String(value ?? "")}
            maxLength={param.max_chars ?? undefined}
            onChange={(e) => onChange(e.target.value)}
            className="resize-y rounded-[var(--radius-control)] border border-line-soft bg-bay px-3 py-2 text-sm outline-none focus:border-tungsten/60"
          />
        </Field>
      );
    case "shots":
      return <ShotsField param={param} value={value} onChange={onChange} />;
    default:
      return null;
  }
}

/** A choice your ComfyUI lists (its LoRA files), read when the form opens; a value it no longer lists stays shown. */
function ComfyChoiceField({ field, label, help, value, onChange }: {
  field: string; label: string; help?: string | null; value: unknown; onChange: (v: unknown) => void;
}) {
  const id = useId();
  const { data } = useQuery({
    queryKey: ["comfy-choices", field],
    queryFn: () => unwrap(api.GET("/local-engine/choices", { params: { query: { field } } })),
    staleTime: 30_000,
  });
  const current = value ? String(value) : "";
  const listed = data?.options ?? [];
  const options = current && !listed.includes(current) ? [current, ...listed] : listed;
  const hint = data && !data.available ? data.message : data && !listed.length ? `${help ?? ""} Your ComfyUI has none yet.` : help;
  return (
    <Field label={label} htmlFor={id} hint={hint?.trim() || undefined}>
      <Select
        id={id}
        value={current}
        options={[{ value: "", label: "None" }, ...options.map((o) => ({ value: o, label: o.replace(/\.safetensors$/, "") }))]}
        onChange={(v) => onChange(v === "" ? undefined : v)}
      />
    </Field>
  );
}
