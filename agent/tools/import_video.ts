import { defineTool } from "eve/tools";
import { z } from "zod";
import { copyBlobToSandbox } from "../lib/blob";
import { type AssetMeta, assetDir, assertSafeId, runOrThrow, shellQuote, writeJson } from "../lib/workspace";

type Probe = {
  format?: { duration?: string };
  streams?: Array<{
    codec_type?: string;
    width?: number;
    height?: number;
    avg_frame_rate?: string;
    side_data_list?: Array<{ rotation?: number }>;
    tags?: { rotate?: string };
  }>;
};

function parseFps(rate: string | undefined): number {
  if (!rate) return 30;
  const [num, den] = rate.split("/").map(Number);
  return den ? Math.round((num / den) * 100) / 100 : num || 30;
}

export default defineTool({
  description:
    "Import a video the user uploaded (Blob pathname from the chat message, e.g. uploads/...) into the editing workspace and probe its metadata. Must run before analyze, transcribe, or render.",
  inputSchema: z.object({
    pathname: z.string().describe("Blob pathname of the uploaded video, exactly as given in the message"),
    assetId: z
      .string()
      .describe("Short lowercase id for this asset, e.g. 'main' or 'ref-1' (a-z, 0-9, dashes)"),
    role: z.enum(["source", "reference"]).describe("source = footage to edit; reference = style example"),
  }),
  async execute({ pathname, assetId, role }, ctx) {
    assertSafeId(assetId, "assetId");
    const sandbox = await ctx.getSandbox();
    const dir = assetDir(assetId);
    const filename = pathname.split("/").pop() ?? "video.mp4";
    const extension = filename.includes(".") ? filename.split(".").pop()!.toLowerCase().replace(/[^a-z0-9]/g, "") : "mp4";
    const sourcePath = `${dir}/source.${extension || "mp4"}`;

    await runOrThrow(sandbox, `mkdir -p ${shellQuote(dir)}`, "Creating asset directory");
    const { size } = await copyBlobToSandbox(sandbox, pathname, sourcePath);

    const probeOutput = await sandbox.run({
      command: `ffprobe -v error -print_format json -show_format -show_streams ${shellQuote(sourcePath)}`,
    });
    if (probeOutput.exitCode !== 0) {
      throw new Error(`The file could not be read as a video: ${probeOutput.stderr.trim()}`);
    }
    const probe = JSON.parse(probeOutput.stdout) as Probe;
    const videoStream = probe.streams?.find((s) => s.codec_type === "video");
    if (!videoStream?.width || !videoStream.height) throw new Error("No video stream found in the file.");

    const rotation = Math.abs(
      Number(videoStream.side_data_list?.find((d) => d.rotation !== undefined)?.rotation ?? videoStream.tags?.rotate ?? 0),
    );
    const rotated = rotation === 90 || rotation === 270;

    const meta: AssetMeta = {
      assetId,
      role,
      filename,
      blobPathname: pathname,
      sourcePath,
      durationSec: Number(probe.format?.duration ?? 0),
      width: rotated ? videoStream.height : videoStream.width,
      height: rotated ? videoStream.width : videoStream.height,
      fps: parseFps(videoStream.avg_frame_rate),
      hasAudio: Boolean(probe.streams?.some((s) => s.codec_type === "audio")),
      importedAt: new Date().toISOString(),
    };
    await writeJson(sandbox, `${dir}/meta.json`, meta);

    return {
      ...meta,
      sizeMB: Math.round((size / 1024 / 1024) * 10) / 10,
      orientation: meta.width > meta.height ? "landscape" : meta.width < meta.height ? "portrait" : "square",
    };
  },
});
