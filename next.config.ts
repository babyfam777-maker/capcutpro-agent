import type { NextConfig } from "next";
import { withEve } from "eve/next";

const nextConfig: NextConfig = {
  // Skip repeating codegen-time typechecking on every deployment build.
  typescript: { ignoreBuildErrors: true },
  // Safari loads the page from 127.0.0.1. Dev assets must allow that host.
  allowedDevOrigins: ["127.0.0.1", "localhost"],
};

// Next.js itself requires Node >= 20.9. Eve's own dev server requires Node >= 24
// and exits the whole `next dev` process on Node 22, which leaves port 3000 closed.
// The local editor does not use that server. Set EVE_DEV=1 only when Node is 24.
const nodeMajor = Number(process.versions.node.split(".")[0]) || 0;
if (process.env.EVE_DEV === "1" && nodeMajor < 24) {
  throw new Error("EVE_DEV=1 kräver Node 24. Starta utan EVE_DEV för den lokala editorn.");
}

export default process.env.EVE_DEV === "1" ? withEve(nextConfig) : nextConfig;
