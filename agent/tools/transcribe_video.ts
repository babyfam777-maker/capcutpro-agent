import { transcribe } from "ai";
import { defineTool } from "eve/tools";
import { z } from "zod";
import { type Transcript, type TranscriptWord, assetDir, requireAsset, runOrThrow, shellQuote, writeJson } from "../lib/workspace";

const TRANSCRIPTION_MODEL = "openai/whisper-1";
const MAX_AUDIO_BYTES = 24 * 1024 * 1024;

function splitSegmentIntoWords(text: string, start: number, end: number): TranscriptWord[] {
  const tokens = text.trim().split(/\s+/).filter(Boolean);
  const totalChars = tokens.reduce((sum, t) => sum + t.length, 0) || 1;
  const span = Math.max(end - start, 0.05);
  let cursor = start;
  return tokens.map((token) => {
    const duration = (token.length / totalChars) * span;
    const word = { text: token, start: cursor, end: cursor + duration };
    cursor += duration;
    return word;
  });
}

export default defineTool({
  description:
    "Transcribe the speech in an imported video with timestamps. Required for burned-in captions and for finding the best spoken hooks and payoffs.",
  inputSchema: z.object({
    assetId: z.string(),
  }),
  async execute({ assetId }, ctx) {
    const sandbox = await ctx.getSandbox();
    const asset = await requireAsset(sandbox, assetId);
    if (!asset.hasAudio) return { assetId, text: "", segments: [], note: "The video has no audio track." };

    const audioPath = `${assetDir(assetId)}/audio.mp3`;
    await runOrThrow(
      sandbox,
      `ffmpeg -hide_banner -y -v error -i ${shellQuote(asset.sourcePath)} -vn -ac 1 -ar 16000 -b:a 48k ${shellQuote(audioPath)}`,
      "Audio extraction",
    );
    const audio = await sandbox.readBinaryFile({ path: audioPath });
    if (!audio) throw new Error("Audio extraction produced no file.");
    if (audio.byteLength > MAX_AUDIO_BYTES) {
      throw new Error("The audio is too long to transcribe in one pass (over ~60 minutes). Trim the source first.");
    }

    const result = await transcribe({ model: TRANSCRIPTION_MODEL, audio, abortSignal: ctx.abortSignal });
    const segments = result.segments.map((s) => ({
      text: s.text.trim(),
      start: Math.round(s.startSecond * 100) / 100,
      end: Math.round(s.endSecond * 100) / 100,
    }));
    const words = segments.flatMap((s) => splitSegmentIntoWords(s.text, s.start, s.end));
    const transcript: Transcript = { language: result.language, text: result.text, words };
    await writeJson(sandbox, `${assetDir(assetId)}/transcript.json`, transcript);

    return {
      assetId,
      language: result.language,
      durationSec: result.durationInSeconds ?? asset.durationSec,
      segments,
      wordCount: words.length,
    };
  },
});
