import type { SandboxSession } from "eve/sandbox";

export const assetDir = (assetId: string) => `/workspace/media/${assetId}`;
export const renderDir = (jobId: string) => `/workspace/renders/${jobId}`;
export const PLAN_PATH = "/workspace/plan/current.json";

const ID_PATTERN = /^[a-z0-9][a-z0-9-]{0,63}$/;

export function assertSafeId(id: string, label: string): string {
  if (!ID_PATTERN.test(id)) throw new Error(`Invalid ${label}: ${id}`);
  return id;
}

export function shellQuote(value: string): string {
  return `'${value.replaceAll("'", "'\\''")}'`;
}

export async function runOrThrow(
  sandbox: SandboxSession,
  command: string,
  description: string,
): Promise<string> {
  const result = await sandbox.run({ command });
  if (result.exitCode !== 0) {
    const detail = (result.stderr || result.stdout).trim().split("\n").slice(-15).join("\n");
    throw new Error(`${description} failed (exit ${result.exitCode}):\n${detail}`);
  }
  return `${result.stdout}\n${result.stderr}`;
}

export async function readJson<T>(sandbox: SandboxSession, path: string): Promise<T | null> {
  const text = await sandbox.readTextFile({ path }).then(
    (value) => value,
    () => null,
  );
  return text ? (JSON.parse(text) as T) : null;
}

export async function writeJson(sandbox: SandboxSession, path: string, value: unknown) {
  const dir = path.slice(0, path.lastIndexOf("/"));
  await runOrThrow(sandbox, `mkdir -p ${shellQuote(dir)}`, "Creating directory");
  await sandbox.writeTextFile({ path, content: JSON.stringify(value, null, 2) });
}

export type AssetMeta = {
  assetId: string;
  role: "source" | "reference";
  filename: string;
  blobPathname: string;
  sourcePath: string;
  durationSec: number;
  width: number;
  height: number;
  fps: number;
  hasAudio: boolean;
  importedAt: string;
};

export async function requireAsset(sandbox: SandboxSession, assetId: string): Promise<AssetMeta> {
  assertSafeId(assetId, "assetId");
  const meta = await readJson<AssetMeta>(sandbox, `${assetDir(assetId)}/meta.json`);
  if (!meta) throw new Error(`Unknown asset "${assetId}". Import the video with import_video first.`);
  return meta;
}

export type TranscriptWord = { text: string; start: number; end: number };
export type Transcript = { language?: string; text: string; words: TranscriptWord[] };
