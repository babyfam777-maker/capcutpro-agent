import { get } from "@vercel/blob";
import { type NextRequest, NextResponse } from "next/server";
import { auth } from "@/lib/auth";

const ALLOWED_PREFIXES = ["uploads/", "renders/"];

export async function GET(request: NextRequest) {
  const session = await auth.api.getSession({ headers: request.headers });
  if (!session) return NextResponse.json({ error: "Unauthorized" }, { status: 401 });

  const pathname = request.nextUrl.searchParams.get("pathname");
  if (!pathname || pathname.includes("..") || !ALLOWED_PREFIXES.some((p) => pathname.startsWith(p))) {
    return NextResponse.json({ error: "Invalid pathname" }, { status: 400 });
  }

  try {
    const result = await get(pathname, {
      access: "private",
      ifNoneMatch: request.headers.get("if-none-match") ?? undefined,
    });
    if (!result) return new NextResponse("Not found", { status: 404 });
    if (result.statusCode === 304) {
      return new NextResponse(null, {
        status: 304,
        headers: { ETag: result.blob.etag, "Cache-Control": "private, no-cache" },
      });
    }
    const filename = pathname.split("/").pop() ?? "video.mp4";
    const download = request.nextUrl.searchParams.get("download") === "1";
    return new NextResponse(result.stream, {
      headers: {
        "Content-Type": result.blob.contentType,
        "Content-Length": String(result.blob.size),
        ETag: result.blob.etag,
        "Cache-Control": "private, no-cache",
        "Content-Disposition": `${download ? "attachment" : "inline"}; filename="${filename.replace(/"/g, "")}"`,
      },
    });
  } catch (error) {
    console.error("Error serving file:", error);
    return NextResponse.json({ error: "Failed to serve file" }, { status: 500 });
  }
}
