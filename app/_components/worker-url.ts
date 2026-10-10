/** Browser calls the worker directly when a public URL is configured.

Vercel cannot run FFmpeg, and its function body limit cannot return a finished MP4.
Locally the same-origin `/api/worker` proxy is used instead.
*/
export function workerUrl(path: string): string {
  const trimmed = path.replace(/^\//, "");
  const pub = process.env.NEXT_PUBLIC_WORKER_URL?.replace(/\/$/, "");
  if (pub) return `${pub}/${trimmed}`;
  return `/api/worker/${trimmed}`;
}
