import createClient from "openapi-fetch";
import type { components, paths } from "./schema";

export type { components, paths };
export type Schemas = components["schemas"];

export function createApiClient(baseUrl: string) {
  // the browser's sign-in cookie (the API's access token) goes along: the API is another origin
  return createClient<paths>({ baseUrl, credentials: "include" });
}
