import { generateText, Output } from "ai";
import { defineTool } from "eve/tools";
import { z } from "zod";
import { assetDir, requireAsset, runOrThrow, shellQuote, writeJson } from "../lib/workspace";

const VISION_MODEL = "google/gemini-3.5-flash";
const FRAME_COUNT = 12;

const visualSchema = z.object({
  summary: z.string().describe("What happens in the video, 2-4 sentences"),
  contentType: z.string().describe("e.g. talking head, gameplay, vlog, tutorial, sports"),
  frames: z.array(
    z.object({
      index: z.number().int(),
      description: z.string(),
      subjectX: z.number().min(0).max(1).describe("Horizontal center of the main subject, 0 = left, 1 = right"),
      subjectY: z.number().min(0).max(1).describe("Vertical center of the main subject"),
      energy: z.enum(["low", "medium", "high"]),
    }),
  ),
  hookIdeas: z.array(z.string()).describe("Moments or angles that would make a strong first 2 seconds"),
  styleNotes: z.string().describe("Pacing, framing, on-screen text, color, and editing style observed"),
});

function parseTimes(output: string, pattern: RegExp): number[] {
  return [...output.matchAll(pattern)].map((m) => Number(m[1])).filter(Number.isFinite);
}

export default defineTool({
  description:
    "Analyze an imported video: scene cuts, silences, editing pace, and a visual read of sampled frames (subject position for 9:16 crop focus, energy, hook ideas, style). Use on both source footage and reference videos.",
  inputSchema: z.object({
    assetId: z.string(),
  }),
  async execute({ assetId }, ctx) {
    const sandbox = await ctx.getSandbox();
    const asset = await requireAsset(sandbox, assetId);
    const src = shellQuote(asset.sourcePath);
    const dir = assetDir(assetId);
    const duration = asset.durationSec;

    const sceneLog = await runOrThrow(
      sandbox,
      `ffmpeg -hide_banner -nostats -i ${src} -an -vf "scale=320:-2,select='gt(scene,0.32)',showinfo" -f null - 2>&1 | grep showinfo || true`,
      "Scene detection",
    );
    const sceneCuts = parseTimes(sceneLog, /pts_time:([\d.]+)/g).map((t) => Math.round(t * 100) / 100);

    let silences: Array<{ start: number; end: number }> = [];
    if (asset.hasAudio) {
      const silenceLog = await runOrThrow(
        sandbox,
        `ffmpeg -hide_banner -nostats -i ${src} -vn -af silencedetect=noise=-35dB:d=0.5 -f null - 2>&1 | grep silence_ || true`,
        "Silence detection",
      );
      const starts = parseTimes(silenceLog, /silence_start: ([\d.]+)/g);
      const ends = parseTimes(silenceLog, /silence_end: ([\d.]+)/g);
      silences = starts.map((start, i) => ({
        start: Math.round(start * 100) / 100,
        end: Math.round((ends[i] ?? duration) * 100) / 100,
      }));
    }

    const sheetPath = `${dir}/contact-sheet.jpg`;
    const frameTimes = Array.from({ length: FRAME_COUNT }, (_, i) => Math.round(((i + 0.5) * duration * 100) / FRAME_COUNT) / 100);
    await runOrThrow(
      sandbox,
      `ffmpeg -hide_banner -y -v error -i ${src} -vf "fps=${FRAME_COUNT}/${Math.max(duration, 0.1).toFixed(3)},scale=360:-2,tile=4x3:padding=4" -frames:v 1 -q:v 4 ${shellQuote(sheetPath)}`,
      "Contact sheet extraction",
    );
    const sheet = await sandbox.readBinaryFile({ path: sheetPath });

    let visual: z.infer<typeof visualSchema> | null = null;
    let visualError: string | undefined;
    if (sheet) {
      try {
        const result = await generateText({
          model: VISION_MODEL,
          output: Output.object({ schema: visualSchema }),
          abortSignal: ctx.abortSignal,
          messages: [
            {
              role: "user",
              content: [
                {
                  type: "text",
                  text: `This is a 4x3 contact sheet of ${FRAME_COUNT} frames sampled from a ${duration.toFixed(1)}s ${asset.width}x${asset.height} video (role: ${asset.role}), read left-to-right, top-to-bottom. Frame i is at ${frameTimes.map((t, i) => `#${i}=${t}s`).join(", ")}. Describe it for an editor cutting a viral YouTube Short. Subject positions are relative to the full original frame.`,
                },
                { type: "image", image: sheet, mediaType: "image/jpeg" },
              ],
            },
          ],
        });
        visual = result.output;
      } catch (error) {
        visualError = error instanceof Error ? error.message : String(error);
      }
    }

    const shotLengths = [0, ...sceneCuts, duration].slice(1).map((t, i, arr) => t - (i === 0 ? 0 : arr[i - 1]));
    const analysis = {
      assetId,
      role: asset.role,
      durationSec: duration,
      resolution: `${asset.width}x${asset.height}`,
      fps: asset.fps,
      hasAudio: asset.hasAudio,
      sceneCuts,
      cutsPerMinute: duration > 0 ? Math.round((sceneCuts.length / duration) * 60 * 10) / 10 : 0,
      averageShotSec: shotLengths.length ? Math.round((duration / shotLengths.length) * 100) / 100 : duration,
      silences,
      silenceTotalSec: Math.round(silences.reduce((s, r) => s + (r.end - r.start), 0) * 10) / 10,
      frameTimes,
      visual: visual
        ? { ...visual, frames: visual.frames.map((f) => ({ ...f, timeSec: frameTimes[f.index] })) }
        : null,
      ...(visualError ? { visualError } : {}),
    };
    await writeJson(sandbox, `${dir}/analysis.json`, analysis);
    return analysis;
  },
});
