import { type NextRequest } from "next/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

function target(path: string[], search: string): string {
  const base = (process.env.WORKER_URL || "http://127.0.0.1:8787").replace(/\/$/, "");
  return `${base}/${path.join("/")}${search}`;
}

async function proxy(request: NextRequest, path: string[]): Promise<Response> {
  const headers = new Headers();
  const contentType = request.headers.get("content-type");
  if (contentType) headers.set("content-type", contentType);
  if (process.env.WORKER_TOKEN) headers.set("x-worker-token", process.env.WORKER_TOKEN);

  const init: RequestInit & { duplex?: "half" } = {
    method: request.method,
    headers,
  };
  if (request.method !== "GET" && request.method !== "HEAD") {
    init.body = request.body;
    init.duplex = "half";
  }

  let response: Response;
  try {
    response = await fetch(target(path, request.nextUrl.search), init);
  } catch {
    return Response.json(
      { error: "Workern svarar inte. Starta den med start.sh (Mac/Linux) eller start.ps1 (Windows)." },
      { status: 503 },
    );
  }

  const out = new Headers();
  for (const name of ["content-type", "content-disposition", "content-length", "cache-control"]) {
    const value = response.headers.get(name);
    if (value) out.set(name, value);
  }
  out.set("cache-control", "no-store");
  out.set("x-accel-buffering", "no");
  return new Response(response.body, { status: response.status, headers: out });
}

type Context = { params: Promise<{ path: string[] }> };

export async function GET(request: NextRequest, context: Context) {
  return proxy(request, (await context.params).path);
}

export async function POST(request: NextRequest, context: Context) {
  return proxy(request, (await context.params).path);
}
