import { defineTool } from "eve/tools";
import { z } from "zod";
import { editPlanSchema, outputDuration, validatePlan } from "../lib/edit-plan";
import { PLAN_PATH, readJson, requireAsset, writeJson } from "../lib/workspace";

type StoredPlan = { version: number; plan: z.infer<typeof editPlanSchema>; savedAt: string };

export default defineTool({
  description:
    "Validate and save the full edit plan (the complete timeline: ordered source segments with speed/zoom/crop focus, captions style, hook text, audio). To change an existing edit, call get_edit_plan, modify it, and save the whole plan again — only the requested parts should change. Returns errors to fix or the saved version.",
  inputSchema: z.object({
    plan: editPlanSchema,
    changeSummary: z.string().max(300).describe("What this version changes, in one sentence"),
  }),
  async execute({ plan, changeSummary }, ctx) {
    const sandbox = await ctx.getSandbox();
    const source = await requireAsset(sandbox, plan.sourceAssetId);
    const { errors, warnings } = validatePlan(plan, source);
    if (errors.length > 0) return { saved: false, errors, warnings };

    const previous = await readJson<StoredPlan & { history?: unknown[] }>(sandbox, PLAN_PATH);
    const version = (previous?.version ?? 0) + 1;
    const history = [
      ...(previous?.history ?? []),
      ...(previous ? [{ version: previous.version, plan: previous.plan, savedAt: previous.savedAt }] : []),
    ].slice(-10);
    await writeJson(sandbox, PLAN_PATH, { version, plan, savedAt: new Date().toISOString(), changeSummary, history });

    return {
      saved: true,
      version,
      outputDurationSec: Math.round(outputDuration(plan) * 10) / 10,
      segmentCount: plan.segments.length,
      warnings,
    };
  },
});
