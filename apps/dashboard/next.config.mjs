// ARCO dashboard. REST calls go through same-origin rewrites (no CORS changes in the
// backend services); WebSockets connect straight to the services (browsers do not apply
// CORS to WebSocket handshakes). Upstream URLs come from the environment.
const BLACKBOARD_URL = process.env.BLACKBOARD_URL ?? "http://localhost:8001";
const BASE_STATION_URL = process.env.BASE_STATION_URL ?? "http://localhost:8002";
const AGENTS_URL = process.env.AGENTS_URL ?? "http://localhost:8003";
const SIMULATOR_URL = process.env.SIMULATOR_URL ?? "http://localhost:8004";
const BENCHMARK_URL = process.env.BENCHMARK_URL ?? "http://localhost:8005";

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Lets the e2e suite run a second dev server (dead backend) next to the first one.
  distDir: process.env.NEXT_DIST_DIR ?? ".next",
  transpilePackages: ["@arco/schemas", "@arco/mobile"],
  eslint: { ignoreDuringBuilds: true }, // the workspace root runs ESLint (flat config)
  async rewrites() {
    return [
      { source: "/api/bb/:path*", destination: `${BLACKBOARD_URL}/:path*` },
      { source: "/api/base/:path*", destination: `${BASE_STATION_URL}/:path*` },
      { source: "/api/agents/:path*", destination: `${AGENTS_URL}/:path*` },
      { source: "/api/sim/:path*", destination: `${SIMULATOR_URL}/:path*` },
      { source: "/api/bench/:path*", destination: `${BENCHMARK_URL}/:path*` },
    ];
  },
};

export default nextConfig;
