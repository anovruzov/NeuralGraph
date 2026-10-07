import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  distDir: process.env.NEXT_DIST_DIR || ".next",
  // PREVIEW_EXPORT=1 produces a static export in out/ for sharing a preview; the normal build is unchanged.
  ...(process.env.PREVIEW_EXPORT ? { output: "export" as const, trailingSlash: false, images: { unoptimized: true } } : {}),
};

export default nextConfig;
