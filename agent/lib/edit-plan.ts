import { z } from "zod";
import type { AssetMeta, Transcript, TranscriptWord } from "./workspace";

const hexColor = z
  .string()
  .regex(/^#[0-9a-fA-F]{6}$/, "Use a #RRGGBB hex color");

export const segmentSchema = z.object({
  sourceStart: z.number().min(0).describe("Start time in the source video, seconds"),
  sourceEnd: z.number().positive().describe("End time in the source video, seconds"),
  speed: z.number().min(0.5).max(2).default(1).describe("Playback speed, 0.5-2"),
  zoomStart: z.number().min(1).max(2).default(1).describe("Zoom at segment start, 1 = none"),
  zoomEnd: z.number().min(1).max(2).default(1).describe("Zoom at segment end (push-in when > zoomStart)"),
  focusX: z
    .number()
    .min(0)
    .max(1)
    .default(0.5)
    .describe("Horizontal crop focus for 9:16 (0 = left edge, 0.5 = center, 1 = right edge)"),
  focusY: z.number().min(0).max(1).default(0.5).describe("Vertical crop focus"),
  label: z.string().max(80).optional().describe("Why this segment is in the edit (hook, payoff…)"),
});

export const editPlanSchema = z.object({
  title: z.string().min(1).max(100),
  sourceAssetId: z.string().min(1),
  format: z
    .object({
      width: z.literal(1080).default(1080),
      height: z.literal(1920).default(1920),
      fps: z.union([z.literal(30), z.literal(60)]).default(30),
    })
    .default({ width: 1080, height: 1920, fps: 30 }),
  segments: z.array(segmentSchema).min(1).max(80),
  captions: z
    .object({
      enabled: z.boolean().default(true),
      fontSize: z.number().int().min(40).max(160).default(96),
      primaryColor: hexColor.default("#FFFFFF"),
      highlightColor: hexColor.default("#FF2D2D"),
      outlineWidth: z.number().min(0).max(12).default(6),
      position: z.enum(["top", "center", "bottom"]).default("center"),
      uppercase: z.boolean().default(true),
      wordsPerChunk: z.number().int().min(1).max(6).default(3),
    })
    .default({
      enabled: true,
      fontSize: 96,
      primaryColor: "#FFFFFF",
      highlightColor: "#FF2D2D",
      outlineWidth: 6,
      position: "center",
      uppercase: true,
      wordsPerChunk: 3,
    }),
  hook: z
    .object({
      text: z.string().min(1).max(80),
      durationSec: z.number().min(0.5).max(6).default(2.5),
    })
    .optional()
    .describe("Bold on-screen title shown at the very start"),
  audio: z
    .object({
      keepOriginal: z.boolean().default(true),
      volume: z.number().min(0).max(2).default(1),
      normalize: z.boolean().default(true).describe("Loudness-normalize to -14 LUFS (Shorts)"),
    })
    .default({ keepOriginal: true, volume: 1, normalize: true }),
});

export type EditPlan = z.infer<typeof editPlanSchema>;
export type Segment = z.infer<typeof segmentSchema>;

export const MAX_SHORT_SECONDS = 180;
const MIN_SEGMENT_OUTPUT_SECONDS = 0.25;

export function outputDuration(plan: EditPlan): number {
  return plan.segments.reduce((sum, s) => sum + (s.sourceEnd - s.sourceStart) / s.speed, 0);
}

export function validatePlan(plan: EditPlan, source: AssetMeta): { errors: string[]; warnings: string[] } {
  const errors: string[] = [];
  const warnings: string[] = [];
  if (plan.sourceAssetId !== source.assetId) {
    errors.push(`sourceAssetId "${plan.sourceAssetId}" does not match imported asset "${source.assetId}".`);
  }
  plan.segments.forEach((segment, index) => {
    const name = `segments[${index}]`;
    if (segment.sourceEnd <= segment.sourceStart) errors.push(`${name}: sourceEnd must be after sourceStart.`);
    if (segment.sourceEnd > source.durationSec + 0.05) {
      errors.push(`${name}: sourceEnd ${segment.sourceEnd}s exceeds source duration ${source.durationSec.toFixed(2)}s.`);
    }
    if ((segment.sourceEnd - segment.sourceStart) / segment.speed < MIN_SEGMENT_OUTPUT_SECONDS) {
      errors.push(`${name}: segment is shorter than ${MIN_SEGMENT_OUTPUT_SECONDS}s after speed change.`);
    }
  });
  const total = outputDuration(plan);
  if (total > MAX_SHORT_SECONDS) errors.push(`Output is ${total.toFixed(1)}s; YouTube Shorts allow at most ${MAX_SHORT_SECONDS}s.`);
  if (total > 60) warnings.push(`Output is ${total.toFixed(1)}s. Shorts under ~45s usually retain viewers better.`);
  if (plan.segments[0] && plan.segments[0].sourceEnd - plan.segments[0].sourceStart > 4) {
    warnings.push("The first segment is longer than 4s; consider a tighter hook.");
  }
  if (!source.hasAudio && plan.captions.enabled) warnings.push("Source has no audio, so there are no speech captions.");
  return { errors, warnings };
}

function assColor(hex: string): string {
  const r = hex.slice(1, 3);
  const g = hex.slice(3, 5);
  const b = hex.slice(5, 7);
  return `&H00${b}${g}${r}`.toUpperCase();
}

function assTime(seconds: number): string {
  const clamped = Math.max(0, seconds);
  const h = Math.floor(clamped / 3600);
  const m = Math.floor((clamped % 3600) / 60);
  const s = clamped % 60;
  return `${h}:${String(m).padStart(2, "0")}:${s.toFixed(2).padStart(5, "0")}`;
}

function assEscape(text: string): string {
  return text.replaceAll("\\", "").replaceAll("{", "(").replaceAll("}", ")").replaceAll("\n", " ");
}

export function mapWordsToTimeline(plan: EditPlan, words: TranscriptWord[]): TranscriptWord[] {
  const mapped: TranscriptWord[] = [];
  let outputOffset = 0;
  for (const segment of plan.segments) {
    for (const word of words) {
      const mid = (word.start + word.end) / 2;
      if (mid < segment.sourceStart || mid >= segment.sourceEnd) continue;
      const toOutput = (t: number) =>
        outputOffset + (Math.min(Math.max(t, segment.sourceStart), segment.sourceEnd) - segment.sourceStart) / segment.speed;
      mapped.push({ text: word.text, start: toOutput(word.start), end: toOutput(word.end) });
    }
    outputOffset += (segment.sourceEnd - segment.sourceStart) / segment.speed;
  }
  return mapped;
}

export function buildAss(plan: EditPlan, transcript: Transcript | null): string | null {
  const { captions, hook } = plan;
  const alignment = captions.position === "top" ? 8 : captions.position === "center" ? 5 : 2;
  const marginV = captions.position === "center" ? 0 : 260;
  const lines: string[] = [
    "[Script Info]",
    "ScriptType: v4.00+",
    `PlayResX: ${plan.format.width}`,
    `PlayResY: ${plan.format.height}`,
    "WrapStyle: 0",
    "ScaledBorderAndShadow: yes",
    "",
    "[V4+ Styles]",
    "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
    `Style: Caption,Anton,${captions.fontSize},${assColor(captions.primaryColor)},&H000000FF,&H00000000,&H64000000,0,0,0,0,100,100,1,0,1,${captions.outlineWidth},2,${alignment},80,80,${marginV},1`,
    `Style: Hook,Anton,92,&H00000000,&H000000FF,&H00FFFFFF,&H00FFFFFF,0,0,0,0,100,100,1,0,3,18,0,8,80,80,220,1`,
    "",
    "[Events]",
    "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
  ];
  let eventCount = 0;

  if (hook) {
    lines.push(`Dialogue: 1,${assTime(0)},${assTime(hook.durationSec)},Hook,,0,0,0,,{\\fad(120,200)}${assEscape(hook.text.toUpperCase())}`);
    eventCount += 1;
  }

  if (captions.enabled && transcript && transcript.words.length > 0) {
    const words = mapWordsToTimeline(plan, transcript.words);
    const highlight = assColor(captions.highlightColor);
    for (let i = 0; i < words.length; i += captions.wordsPerChunk) {
      const chunk = words.slice(i, i + captions.wordsPerChunk);
      chunk.forEach((word, wordIndex) => {
        const start = word.start;
        const next = chunk[wordIndex + 1];
        const end = next ? next.start : Math.max(word.end, start + 0.15);
        if (end <= start) return;
        const text = chunk
          .map((w, idx) => {
            const value = assEscape(captions.uppercase ? w.text.toUpperCase() : w.text);
            return idx === wordIndex ? `{\\c${highlight}\\fscx108\\fscy108}${value}{\\r}` : value;
          })
          .join(" ");
        lines.push(`Dialogue: 0,${assTime(start)},${assTime(end)},Caption,,0,0,0,,${text}`);
        eventCount += 1;
      });
    }
  }

  return eventCount > 0 ? `${lines.join("\n")}\n` : null;
}

const fmt = (n: number) => Number(n.toFixed(3)).toString();

export function buildFilterGraph(plan: EditPlan, source: AssetMeta, hasSubtitles: boolean, fontsDir: string): string {
  const { width, height, fps } = plan.format;
  const useSourceAudio = source.hasAudio && plan.audio.keepOriginal;
  const chains: string[] = [];
  const concatInputs: string[] = [];

  plan.segments.forEach((segment, i) => {
    const duration = (segment.sourceEnd - segment.sourceStart) / segment.speed;
    const frames = Math.max(1, Math.round(duration * fps));
    const video = [
      `[0:v]trim=start=${fmt(segment.sourceStart)}:end=${fmt(segment.sourceEnd)}`,
      `setpts=(PTS-STARTPTS)/${fmt(segment.speed)}`,
      `fps=${fps}`,
      `scale=${width}:${height}:force_original_aspect_ratio=increase`,
      `crop=${width}:${height}:(iw-${width})*${fmt(segment.focusX)}:(ih-${height})*${fmt(segment.focusY)}`,
    ];
    if (segment.zoomStart !== 1 || segment.zoomEnd !== 1) {
      const z0 = fmt(segment.zoomStart);
      const dz = fmt(segment.zoomEnd - segment.zoomStart);
      video.push(
        `zoompan=z='${z0}+${dz}*on/${frames}':x='(iw-iw/zoom)*${fmt(segment.focusX)}':y='(ih-ih/zoom)*${fmt(segment.focusY)}':d=1:s=${width}x${height}:fps=${fps}`,
      );
    }
    video.push("setsar=1", "format=yuv420p");
    chains.push(`${video.join(",")}[v${i}]`);

    const audio = useSourceAudio
      ? [
          `[0:a]atrim=start=${fmt(segment.sourceStart)}:end=${fmt(segment.sourceEnd)}`,
          "asetpts=PTS-STARTPTS",
          ...(segment.speed !== 1 ? [`atempo=${fmt(segment.speed)}`] : []),
        ]
      : [`anullsrc=r=48000:cl=stereo`, `atrim=0:${fmt(duration)}`];
    audio.push("aformat=sample_rates=48000:channel_layouts=stereo");
    chains.push(`${audio.join(",")}[a${i}]`);
    concatInputs.push(`[v${i}][a${i}]`);
  });

  chains.push(`${concatInputs.join("")}concat=n=${plan.segments.length}:v=1:a=1[vc][ac]`);
  chains.push(hasSubtitles ? `[vc]subtitles=captions.ass:fontsdir=${fontsDir}[vout]` : "[vc]null[vout]");
  const audioPost = [`volume=${fmt(plan.audio.volume)}`];
  if (plan.audio.normalize && useSourceAudio) audioPost.push("loudnorm=I=-14:TP=-1.5:LRA=11");
  audioPost.push("aresample=48000");
  chains.push(`[ac]${audioPost.join(",")}[aout]`);
  return chains.join(";\n");
}
