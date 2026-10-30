import path from "node:path";
import type { NextConfig } from "next";

// infra/server's web image builds a standalone server (NEXT_OUTPUT=standalone, infra/server/Containerfile.web);
// it traces from the repo root so the workspace's api-client comes along.
const standalone = process.env.NEXT_OUTPUT === "standalone";

const nextConfig: NextConfig = {
  // The API client is a TypeScript workspace package; Next compiles it with the app.
  transpilePackages: ["@studio/api-client"],
  reactStrictMode: true,
  // the dev badge sits over the Director's Send button; build and runtime errors still show
  devIndicators: false,
  // the gallery was called Presets before it took guided templates too
  redirects: async () => [{ source: "/presets", destination: "/templates", permanent: true }],
  ...(standalone && { output: "standalone", outputFileTracingRoot: path.join(__dirname, "../..") }),
};

export default nextConfig;
