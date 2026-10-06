import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // The API client is a TypeScript workspace package; Next compiles it with the app.
  transpilePackages: ["@studio/api-client"],
  reactStrictMode: true,
};

export default nextConfig;
