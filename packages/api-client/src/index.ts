import createClient from "openapi-fetch";
import type { components, paths } from "./schema";

export type { components, paths };
export type Schemas = components["schemas"];

export function createApiClient(baseUrl: string) {
  return createClient<paths>({ baseUrl });
}
