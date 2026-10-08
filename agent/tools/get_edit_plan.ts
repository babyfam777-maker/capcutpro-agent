import { defineTool } from "eve/tools";
import { z } from "zod";
import type { EditPlan } from "../lib/edit-plan";
import { PLAN_PATH, readJson } from "../lib/workspace";

export default defineTool({
  description: "Read the current saved edit plan (or a previous version) so it can be revised precisely.",
  inputSchema: z.object({
    version: z.number().int().positive().optional().describe("Omit for the latest version"),
  }),
  async execute({ version }, ctx) {
    const sandbox = await ctx.getSandbox();
    const stored = await readJson<{
      version: number;
      plan: EditPlan;
      changeSummary?: string;
      history?: Array<{ version: number; plan: EditPlan }>;
    }>(sandbox, PLAN_PATH);
    if (!stored) return { found: false, message: "No edit plan has been saved yet." };
    if (version === undefined || version === stored.version) {
      return { found: true, version: stored.version, changeSummary: stored.changeSummary, plan: stored.plan };
    }
    const old = stored.history?.find((entry) => entry.version === version);
    return old
      ? { found: true, version: old.version, plan: old.plan }
      : { found: false, message: `Version ${version} is not available. Latest is ${stored.version}.` };
  },
});
