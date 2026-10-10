import type { NextConfig } from "next";
import { withEve } from "eve/next";

const nextConfig: NextConfig = {
  // Skip repeating codegen-time typechecking on every deployment build.
  typescript: { ignoreBuildErrors: true },
};

// The home editor talks to the local worker and does not need the eve dev
// server. That server requires Node 24, so `npm run dev` on Node 22 skips it.
// On Node 24, eve still wraps the app unless CAPCUT_SKIP_EVE=1.
const nodeMajor = Number(process.versions.node.split(".")[0]) || 0;
const skipEve = process.env.CAPCUT_SKIP_EVE === "1" || nodeMajor < 24;

export default skipEve ? nextConfig : withEve(nextConfig);
