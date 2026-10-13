import { get, put } from "@vercel/blob";
import type { SandboxSession } from "eve/sandbox";

export const UPLOAD_PREFIX = "uploads/";
export const RENDER_PREFIX = "renders/";

export function fileUrl(pathname: string): string {
  return `/api/file?pathname=${encodeURIComponent(pathname)}`;
}

export async function copyBlobToSandbox(
  sandbox: SandboxSession,
  pathname: string,
  destination: string,
): Promise<{ size: number; contentType: string }> {
  if (!pathname.startsWith(UPLOAD_PREFIX) || pathname.includes("..")) {
    throw new Error(`Only uploaded videos under "${UPLOAD_PREFIX}" can be imported.`);
  }
  const result = await get(pathname, { access: "private" });
  if (!result || result.statusCode !== 200 || !result.stream) {
    throw new Error(`Uploaded video not found: ${pathname}`);
  }
  await sandbox.writeFile({ path: destination, content: result.stream });
  return { size: result.blob.size, contentType: result.blob.contentType };
}

export async function copySandboxFileToBlob(
  sandbox: SandboxSession,
  source: string,
  pathname: string,
): Promise<{ pathname: string; url: string }> {
  const stream = await sandbox.readFile({ path: source });
  if (!stream) throw new Error(`Rendered file missing in sandbox: ${source}`);
  const blob = await put(pathname, stream, {
    access: "private",
    contentType: "video/mp4",
    multipart: true,
    addRandomSuffix: false,
    allowOverwrite: true,
  });
  return { pathname: blob.pathname, url: fileUrl(blob.pathname) };
}
