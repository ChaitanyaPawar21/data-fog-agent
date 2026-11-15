import type { NextConfig } from "next";

const backendUrl = process.env.BACKEND_URL ?? "http://localhost:8000";

const nextConfig: NextConfig = {
  // Production Docker image runs .next/standalone/server.js (see Dockerfile).
  output: "standalone",
  cacheComponents: true,
  partialPrefetching: true,
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
  // The browser only ever talks to this app. Proxying /api to the backend keeps
  // everything on one origin, so the refresh cookie works without any CORS setup.
  async rewrites() {
    return [
      { source: "/api/:path*", destination: `${backendUrl}/api/:path*` },
      // The MCP endpoint, so MCP clients can use the same address as the browser.
      { source: "/mcp", destination: `${backendUrl}/mcp` },
    ];
  },
};

export default nextConfig;
