import { defineTool } from "eve/tools";
import { z } from "zod";
import { RENDER_PREFIX, copySandboxFileToBlob } from "../lib/blob";
import { assertSafeId, readJson, renderDir, writeJson } from "../lib/workspace";

const POLL_INTERVAL_MS = 3000;
const MAX_WAIT_MS = 45_000;

type Job = { jobId: string; planVersion: number; preview: boolean; title: string; totalSec: number };
type Result = { pathname: string; url: string };

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

export default defineTool({
  description:
    "Check a background render. Waits up to ~45s for progress. When finished it uploads the MP4 and returns a videoUrl to share with the user. Call again if status is still 'rendering'.",
  inputSchema: z.object({ jobId: z.string() }),
  async execute({ jobId }, ctx) {
    assertSafeId(jobId, "jobId");
    const sandbox = await ctx.getSandbox();
    const dir = renderDir(jobId);
    const job = await readJson<Job>(sandbox, `${dir}/job.json`);
    if (!job) throw new Error(`Unknown render job ${jobId}.`);

    const existing = await readJson<Result>(sandbox, `${dir}/result.json`);
    if (existing) return { status: "done", ...job, videoUrl: existing.url, pathname: existing.pathname };

    const deadline = Date.now() + MAX_WAIT_MS;
    let progress = 0;
    while (true) {
      const exitCode = await Promise.resolve(sandbox.readTextFile({ path: `${dir}/exit_code` })).then(
        (text) => (text ? Number(text.trim()) : null),
        () => null,
      );
      if (exitCode !== null) {
        if (exitCode !== 0) {
          const log = (await Promise.resolve(sandbox.readTextFile({ path: `${dir}/ffmpeg.log` })).catch(() => "")) ?? "";
          return { status: "failed", jobId, exitCode, log: log.trim().split("\n").slice(-20).join("\n") };
        }
        const pathname = `${RENDER_PREFIX}${jobId}.mp4`;
        const uploaded = await copySandboxFileToBlob(sandbox, `${dir}/output.mp4`, pathname);
        await writeJson(sandbox, `${dir}/result.json`, uploaded);
        return { status: "done", ...job, videoUrl: uploaded.url, pathname: uploaded.pathname };
      }

      const progressText = (await Promise.resolve(sandbox.readTextFile({ path: `${dir}/progress.txt` })).catch(() => "")) ?? "";
      const times = [...progressText.matchAll(/out_time_us=(\d+)/g)];
      const lastUs = times.length ? Number(times[times.length - 1][1]) : 0;
      progress = job.totalSec > 0 ? Math.min(99, Math.round((lastUs / 1_000_000 / job.totalSec) * 100)) : 0;

      if (Date.now() + POLL_INTERVAL_MS > deadline || ctx.abortSignal?.aborted) break;
      await sleep(POLL_INTERVAL_MS);
    }
    return { status: "rendering", jobId, progressPercent: progress };
  },
});
