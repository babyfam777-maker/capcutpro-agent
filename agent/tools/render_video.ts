import { defineTool } from "eve/tools";
import { z } from "zod";
import { buildAss, buildFilterGraph, type EditPlan, outputDuration, validatePlan } from "../lib/edit-plan";
import { FONTS_DIR } from "../sandbox";
import {
  PLAN_PATH,
  type Transcript,
  assetDir,
  readJson,
  renderDir,
  requireAsset,
  runOrThrow,
  shellQuote,
  writeJson,
} from "../lib/workspace";

export default defineTool({
  description:
    "Start rendering the current saved edit plan to a 1080x1920 MP4 in the background. Returns a jobId immediately; then call get_render_status until it is done.",
  inputSchema: z.object({
    preview: z
      .boolean()
      .default(false)
      .describe("true = fast low-quality draft render, false = final quality"),
  }),
  async execute({ preview }, ctx) {
    const sandbox = await ctx.getSandbox();
    const stored = await readJson<{ version: number; plan: EditPlan }>(sandbox, PLAN_PATH);
    if (!stored) throw new Error("No edit plan saved yet. Call save_edit_plan first.");
    const { plan, version } = stored;
    const source = await requireAsset(sandbox, plan.sourceAssetId);
    const { errors } = validatePlan(plan, source);
    if (errors.length > 0) return { started: false, errors };

    const transcript = await readJson<Transcript>(sandbox, `${assetDir(source.assetId)}/transcript.json`);
    const ass = buildAss(plan, transcript);
    const jobId = `v${version}-${preview ? "draft" : "final"}-${Date.now().toString(36)}`;
    const dir = renderDir(jobId);
    await runOrThrow(sandbox, `mkdir -p ${shellQuote(dir)}`, "Creating render directory");
    if (ass) await sandbox.writeTextFile({ path: `${dir}/captions.ass`, content: ass });
    await sandbox.writeTextFile({ path: `${dir}/graph.txt`, content: buildFilterGraph(plan, source, Boolean(ass), FONTS_DIR) });

    const quality = preview ? "-preset ultrafast -crf 30" : "-preset medium -crf 19";
    const script = [
      "#!/bin/sh",
      `ffmpeg -hide_banner -y -nostats -i ${shellQuote(source.sourcePath)} -filter_complex_script graph.txt -map "[vout]" -map "[aout]" -c:v libx264 ${quality} -profile:v high -pix_fmt yuv420p -r ${plan.format.fps} -c:a aac -b:a 192k -ar 48000 -movflags +faststart -progress progress.txt output.mp4 > ffmpeg.log 2>&1`,
      "echo $? > exit_code",
    ].join("\n");
    await sandbox.writeTextFile({ path: `${dir}/render.sh`, content: script });

    const totalSec = outputDuration(plan);
    await writeJson(sandbox, `${dir}/job.json`, {
      jobId,
      planVersion: version,
      preview,
      title: plan.title,
      totalSec,
      startedAt: new Date().toISOString(),
    });
    await runOrThrow(
      sandbox,
      `cd ${shellQuote(dir)} && (nohup sh render.sh > /dev/null 2>&1 &) && echo started`,
      "Starting render",
    );

    return {
      started: true,
      jobId,
      planVersion: version,
      outputDurationSec: Math.round(totalSec * 10) / 10,
      captions: Boolean(ass),
    };
  },
});
