import { createApiClient, type Schemas } from "@opsis/api-client";

export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://127.0.0.1:8400";

export const api = createApiClient(API_URL);

export type ModelSummary = Schemas["ModelSummary"];
export type ModelDetail = Schemas["ModelDetail"];
export type Mode = Schemas["Mode"];
export type MediaSlot = Schemas["MediaSlot"];
export type Generation = Schemas["GenerationOut"];
export type Asset = Schemas["AssetOut"];
export type Estimate = Schemas["EstimateOut"];
export type Budget = Schemas["BudgetOut"];
export type ProviderKey = Schemas["KeyOut"];
export type Param = ModelDetail["parameters"][number];

/** Media paths from the API are relative to the API origin. */
export function mediaUrl(path: string | null | undefined): string | undefined {
  if (!path) return undefined;
  return path.startsWith("http") ? path : `${API_URL}${path}`;
}

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly body: unknown,
  ) {
    super(describe(body) ?? `Request failed (${status})`);
  }
}

function describe(body: unknown): string | undefined {
  if (!body || typeof body !== "object") return undefined;
  const b = body as { errors?: string[]; message?: string; detail?: unknown };
  if (Array.isArray(b.errors) && b.errors.length) return b.errors.join(" ");
  if (typeof b.message === "string") return b.message;
  if (typeof b.detail === "string") return b.detail;
  return undefined;
}

/** openapi-fetch returns {data, error, response}; turn errors into exceptions for TanStack Query. */
export async function unwrap<T>(
  call: Promise<{ data?: T; error?: unknown; response: Response }>,
): Promise<T> {
  const { data, error, response } = await call;
  if (!response.ok) throw new ApiError(response.status, error);
  return data as T;
}

export const ACTIVE_STATUSES = new Set([
  "queued",
  "awaiting_confirmation",
  "blocked_budget",
  "submitting",
  "in_progress",
  "ingesting",
  "unknown",
]);

export function isActive(status: string) {
  return ACTIVE_STATUSES.has(status);
}

/** What a take made through a virtual model (Cinema Studio) was asked for: the studio, its mode and engine. */
export type Via = { model_id: string; name: string; mode: string; engine: string; engine_label: string };

export function viaOf(gen: Pick<Generation, "request">): Via | null {
  return ((gen.request ?? {}) as { via?: Via | null }).via ?? null;
}

/** "Cinema Studio on Kling 3.0" for a take made through a virtual model, else the model's own name. */
export function viaName(gen: Pick<Generation, "request">, modelName: string): string {
  const via = viaOf(gen);
  return via ? `${via.name} on ${modelName}` : modelName;
}
