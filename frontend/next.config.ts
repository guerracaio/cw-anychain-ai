import type { NextConfig } from "next";

const backendUrl = (process.env.BACKEND_URL || "http://127.0.0.1:8000").replace(/\/$/, "");

const nextConfig: NextConfig = {
  // The Docker image runs the self-contained server; local `next start` keeps the default.
  output: process.env.NEXT_STANDALONE === "1" ? "standalone" : undefined,
  experimental: { proxyTimeout: 135_000 },
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${backendUrl}/api/:path*` }];
  },
};

export default nextConfig;
