import type { NextConfig } from "next";
import { withEve } from "eve/next";

const nextConfig: NextConfig = {
  // Skip repeating codegen-time typechecking on every deployment build.
  typescript: { ignoreBuildErrors: true },
};

// The home editor talks to the local worker and does not need the eve dev
// server (which requires Node 24). Leave this unset for `eve dev` / Vercel.
const skipEve = process.env.CAPCUT_SKIP_EVE === "1";

export default skipEve ? nextConfig : withEve(nextConfig);
