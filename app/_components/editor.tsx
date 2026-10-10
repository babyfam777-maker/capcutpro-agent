"use client";

import { DownloadIcon, FilmIcon, Loader2Icon } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { workerUrl } from "./worker-url";

type Version = { version: number; duration?: number; width?: number; height?: number };
type ChatMessage = { role: string; content: string };
type Project = {
  id: string;
  filename: string;
  activeVersion: number | null;
  versions: Version[];
  messages: ChatMessage[];
};

const STEP_LABELS: Record<string, string> = {
  analyze: "Analyserar videon",
  probe: "Läser filen",
  scenes: "Letar scener",
  motion: "Mäter rörelse",
  faces: "Letar ansikten",
  hands: "Letar händer",
  whisper: "Skriver ut vad som sägs",
  audio: "Lyssnar på ljudet",
  storyboard: "Bygger storyboard",
  render: "Renderar MP4",
  qa: "Kontrollerar filen",
  upload: "Laddar upp",
};

const CHAT_EXAMPLES = ["Gör hooken större", "Ta bort pilen", "Mer zoom på personen", "Gör texten roligare"];

export function Editor() {
  const [project, setProject] = useState<Project | null>(null);
  const [view, setView] = useState<number | "original" | null>(null);
  const [prompt, setPrompt] = useState("");
  const [draft, setDraft] = useState("");
  const [pending, setPending] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [step, setStep] = useState("");
  const [progress, setProgress] = useState<number | null>(null);
  const [log, setLog] = useState<string[]>([]);
  const [error, setError] = useState("");
  const [workerUp, setWorkerUp] = useState<boolean | null>(null);
  const [dragging, setDragging] = useState(false);
  const [filename, setFilename] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);
  const videoRef = useRef<HTMLVideoElement>(null);

  useEffect(() => {
    void fetch(workerUrl("health"))
      .then((response) => setWorkerUp(response.ok))
      .catch(() => setWorkerUp(false));
  }, []);

  async function refresh(id: string) {
    const response = await fetch(workerUrl(`projects/${id}`));
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(messageOf(payload, "Kunde inte läsa projektet."));
    const next = payload as Project;
    setProject(next);
    if (next.activeVersion) setView(next.activeVersion);
    return next;
  }

  async function onFile(file: File) {
    if (!isVideo(file)) {
      setError("Släpp en videofil, till exempel MP4 eller MOV.");
      return;
    }
    setError("");
    setBusy(true);
    setStep("upload");
    setLog(["Laddar upp videon"]);
    setProgress(null);
    try {
      const body = new FormData();
      body.append("file", file);
      const response = await fetch(workerUrl("projects"), { method: "POST", body });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(messageOf(payload, "Uppladdningen misslyckades."));
      setProject(payload as Project);
      setView("original");
      setFilename(file.name);
      setPending([]);
      setLog(["Videon är uppladdad. Skriv vad shortsen ska handla om."]);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Uppladdningen misslyckades.");
      setWorkerUp(false);
    } finally {
      setBusy(false);
      setStep("");
    }
  }

  function queueDraft() {
    const line = draft.trim();
    if (!line) return;
    setPending((current) => [...current, line]);
    setDraft("");
  }

  async function makeShort() {
    if (!project || busy) return;
    const text = prompt.trim();
    if (!text) {
      setError("Skriv vad shortsen ska handla om.");
      return;
    }
    await runJob(workerUrl(`projects/${project.id}/short`), { prompt: text }, "Gör shorten");
  }

  async function renderAgain() {
    if (!project || busy) return;
    const lines = [...pending];
    const extra = draft.trim();
    if (extra) lines.push(extra);
    if (lines.length === 0) {
      setError("Skriv en ändring först, till exempel: Gör hooken större.");
      return;
    }
    const ok = await runJob(workerUrl(`projects/${project.id}/rerender`), { lines }, "Renderar igen");
    if (ok) {
      setPending([]);
      setDraft("");
    }
  }

  async function runJob(url: string, body: unknown, label: string) {
    setError("");
    setBusy(true);
    setProgress(null);
    setLog([label]);
    setStep("analyze");
    let failed = false;
    try {
      const response = await fetch(url, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(body),
      });
      const type = response.headers.get("content-type") || "";
      if (!response.ok || !type.includes("text/event-stream") || !response.body) {
        const payload = await response.json().catch(() => ({}));
        throw new Error(messageOf(payload, "Render misslyckades."));
      }
      await readEvents(response, (event, data) => {
        if (event === "status" && data.step) {
          const name = String(data.step);
          setStep(name);
          setLog((current) => [...current, STEP_LABELS[name] || name]);
        }
        if (event === "analysis") {
          const duration = typeof data.duration === "number" ? data.duration : 0;
          setLog((current) => [
            ...current,
            `${duration.toFixed(1)}s källa · ${num(data.phrases)} fraser · ${num(data.faces)} ansikten · ${num(data.scenes)} klipp`,
          ]);
        }
        if (event === "progress") setProgress(typeof data.progress === "number" ? data.progress : null);
        if (event === "edits" && Array.isArray(data.changes)) {
          const lines = (data.changes as unknown[]).map((item: unknown) => String(item));
          setLog((current) => [...current, ...lines]);
        }
        if (event === "qa" && data.ok === false && Array.isArray(data.checks)) {
          const names = data.checks.flatMap((item) => {
            if (!item || typeof item !== "object") return [];
            const row = item as { ok?: boolean; name?: string };
            return row.ok === false && row.name ? [row.name] : [];
          });
          if (names.length) setLog((current) => [...current, `Kontroll: ${names.join(", ")}`]);
        }
        if (event === "version" && data.activeVersion) setView(Number(data.activeVersion));
        if (event === "error") {
          failed = true;
          setError(String(data.message || "Något gick fel."));
        }
        if (event === "done") {
          if (data.activeVersion) setView(Number(data.activeVersion));
          if (data.reply) setLog((current) => [...current, String(data.reply)]);
        }
      });
      const next = await refresh(project!.id);
      if (next.activeVersion) {
        setView(next.activeVersion);
        requestAnimationFrame(() => videoRef.current?.scrollIntoView({ block: "center", behavior: "smooth" }));
      }
      return !failed;
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Något gick fel.");
      return false;
    } finally {
      setBusy(false);
      setStep("");
      setProgress(null);
    }
  }

  async function download() {
    if (!project || typeof view !== "number") return;
    const href = workerUrl(`projects/${project.id}/versions/${view}/video?download=1`);
    const absolute = new URL(href, window.location.href);
    if (absolute.origin === window.location.origin) {
      const anchor = document.createElement("a");
      anchor.href = absolute.toString();
      anchor.download = `capcutpro-v${view}.mp4`;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      return;
    }
    try {
      const response = await fetch(absolute.toString());
      if (!response.ok) throw new Error("Kunde inte hämta MP4.");
      const blob = await response.blob();
      const objectUrl = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = objectUrl;
      anchor.download = `capcutpro-v${view}.mp4`;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      window.setTimeout(() => URL.revokeObjectURL(objectUrl), 10_000);
    } catch {
      window.location.assign(absolute.toString());
    }
  }

  const videoSrc =
    project && view === "original"
      ? workerUrl(`projects/${project.id}/original`)
      : project && typeof view === "number"
        ? workerUrl(`projects/${project.id}/versions/${view}/video`)
        : null;
  const rendered = project?.versions ?? [];

  return (
    <main
      className="mx-auto flex min-h-dvh w-full max-w-6xl flex-col bg-[#0c0c0c] text-white"
      onDragEnter={(event) => {
        event.preventDefault();
        setDragging(true);
      }}
      onDragOver={(event) => {
        event.preventDefault();
        setDragging(true);
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={(event) => {
        event.preventDefault();
        setDragging(false);
        const file = event.dataTransfer.files?.[0];
        if (file) void onFile(file);
      }}
      style={{ paddingBottom: "env(safe-area-inset-bottom)" }}
    >
      <header className="border-b border-white/10 px-4 py-4">
        <p className="text-xs tracking-[0.22em] text-white/60">CAPCUTPRO</p>
        <h1 className="text-xl font-semibold tracking-tight sm:text-2xl">VIRAL SHORT EDITOR</h1>
      </header>

      {workerUp === false ? (
        <p className="bg-[#FF2D2D]/15 px-4 py-3 text-sm text-[#ffb4b4]" role="alert">
          Workern svarar inte. Lokalt: kör <code>start.sh</code>. På Vercel måste{" "}
          <code>NEXT_PUBLIC_WORKER_URL</code> peka på en server med FFmpeg.
        </p>
      ) : null}

      <div className="grid flex-1 gap-4 p-4 md:grid-cols-[minmax(260px,380px)_1fr]">
        <section className="flex flex-col gap-3">
          {videoSrc ? (
            <video
              className="aspect-[9/16] w-full rounded-xl bg-black object-contain"
              controls
              key={videoSrc}
              playsInline
              poster=""
              preload="metadata"
              ref={videoRef}
              src={videoSrc}
            />
          ) : (
            <label
              className={`flex aspect-[9/16] w-full cursor-pointer flex-col items-center justify-center gap-3 rounded-xl border border-dashed px-6 text-center ${dragging ? "border-[#FF2D2D] bg-[#FF2D2D]/10" : "border-white/20"}`}
              htmlFor="video-file"
            >
              <FilmIcon className="size-8 text-[#FF2D2D]" />
              <span className="text-base">Släpp en video här, eller tryck för att välja</span>
              <span className="text-sm text-white/50">Vilken video som helst. MP4, MOV, WEBM.</span>
            </label>
          )}
          <div className="flex flex-wrap gap-2">
            <label
              className="inline-flex h-11 cursor-pointer items-center rounded-md bg-white/10 px-3 text-sm"
              htmlFor="video-file"
            >
              {project ? "Ny video" : "Välj video"}
            </label>
            {project ? (
              <Button className={view === "original" ? "h-11 bg-[#FF2D2D] text-white hover:bg-[#ff4545]" : "h-11 border-white/20 bg-transparent text-white hover:bg-white/10"} onClick={() => setView("original")} type="button" variant={view === "original" ? "default" : "outline"}>
                Original
              </Button>
            ) : null}
            {rendered.map((version) => (
              <Button
                className={view === version.version ? "h-11 bg-[#FF2D2D] text-white hover:bg-[#ff4545]" : "h-11 border-white/20 bg-transparent text-white hover:bg-white/10"}
                key={version.version}
                onClick={() => setView(version.version)}
                type="button"
                variant={view === version.version ? "default" : "outline"}
              >
                v{version.version}
                {version.duration ? ` · ${version.duration.toFixed(1)}s` : ""}
              </Button>
            ))}
          </div>
          {typeof view === "number" ? (
            <Button className="h-12 bg-[#FF2D2D] text-base text-white hover:bg-[#ff4545]" onClick={() => void download()} type="button">
              <DownloadIcon className="size-4" />
              Ladda ner MP4
            </Button>
          ) : null}
          {filename ? <p className="text-sm text-white/50">{filename}</p> : null}
          <input
            accept="video/*,.mp4,.mov,.m4v,.webm,.mkv"
            className="sr-only"
            id="video-file"
            onChange={(event) => {
              const file = event.target.files?.[0];
              if (file) void onFile(file);
              event.target.value = "";
            }}
            ref={fileRef}
            type="file"
          />
        </section>

        <section className="flex min-h-0 flex-col gap-4">
          <label className="flex flex-col gap-2 text-sm" htmlFor="story-prompt">
            Vad ska shortsen handla om?
            <Textarea
              className="min-h-24 border-white/15 bg-white/5 text-base text-white placeholder:text-white/40 md:text-base"
              disabled={busy}
              id="story-prompt"
              style={{ fontSize: "16px" }}
              onChange={(event) => setPrompt(event.target.value)}
              placeholder="Till exempel: den pinsamma tystnaden när hon tittar bort"
              value={prompt}
            />
          </label>
          <Button
            className="h-12 bg-[#FF2D2D] text-base font-semibold text-white hover:bg-[#ff4545]"
            disabled={!project || busy || prompt.trim().length === 0}
            onClick={() => void makeShort()}
            type="button"
          >
            {busy ? <Loader2Icon className="size-4 animate-spin" /> : null}
            GÖR MIN SHORT
          </Button>

          <div aria-live="polite" className="rounded-xl border border-white/10 bg-white/5 p-3">
            <p className="text-sm text-white/70">{busy ? STEP_LABELS[step] || step || "Jobbar" : log.length ? "Status" : "Analys och render visas här."}</p>
            {busy ? (
              <div className="mt-2 h-1 overflow-hidden rounded bg-white/10">
                <div
                  className={`h-full bg-[#FF2D2D] ${progress === null ? "w-1/3 animate-pulse" : ""}`}
                  style={progress === null ? undefined : { width: `${Math.max(4, Math.round(progress * 100))}%` }}
                />
              </div>
            ) : null}
            {progress !== null ? <p className="mt-1 text-xs text-white/50">{Math.round(progress * 100)}%</p> : null}
            <ul className="mt-2 max-h-40 space-y-1 overflow-y-auto text-sm text-white/80">
              {log.map((line, index) => (
                <li key={`${index}-${line}`}>{line}</li>
              ))}
            </ul>
            {error ? (
              <p className="mt-2 text-sm text-[#ffb4b4]" role="alert">
                {error}
              </p>
            ) : null}
          </div>

          <div className="flex flex-1 flex-col gap-3 rounded-xl border border-white/10 p-3">
            <p className="text-sm text-white/70">Be om en ändring. Den körs när du trycker RENDER IGEN.</p>
            <div className="flex flex-wrap gap-2">
              {CHAT_EXAMPLES.map((example) => (
                <button
                  className="h-10 rounded-full border border-white/15 px-3 text-sm text-white/80"
                  disabled={!project || busy}
                  key={example}
                  onClick={() => setPending((current) => [...current, example])}
                  type="button"
                >
                  {example}
                </button>
              ))}
            </div>
            <div className="min-h-16 flex-1 space-y-2 overflow-y-auto">
              {project?.messages.map((message, index) => (
                <p className={message.role === "user" ? "text-sm text-white" : "text-sm text-white/70"} key={`${message.role}-${index}`}>
                  <span className="mr-2 text-xs uppercase tracking-wide text-white/40">{message.role === "user" ? "Du" : "CapCutPro"}</span>
                  {message.content}
                </p>
              ))}
              {pending.map((line, index) => (
                <p className="text-sm text-white" key={`pending-${index}`}>
                  <span className="mr-2 text-xs uppercase tracking-wide text-[#FF2D2D]">Kö</span>
                  {line}
                </p>
              ))}
            </div>
            <Textarea
              className="min-h-20 border-white/15 bg-white/5 text-base text-white placeholder:text-white/40 md:text-base"
              disabled={!project || busy}
              style={{ fontSize: "16px" }}
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey) {
                  event.preventDefault();
                  queueDraft();
                }
              }}
              placeholder="Gör hooken större"
              value={draft}
            />
            <div className="flex flex-col gap-2 sm:flex-row">
              <Button className="h-11 border-white/20 bg-transparent text-white hover:bg-white/10" disabled={!project || busy || draft.trim().length === 0} onClick={queueDraft} type="button" variant="outline">
                Lägg i chatten
              </Button>
              <Button
                className="h-12 flex-1 bg-white text-base font-semibold text-black hover:bg-white/90"
                disabled={!project || busy || (pending.length === 0 && draft.trim().length === 0)}
                onClick={() => void renderAgain()}
                type="button"
              >
                {busy ? <Loader2Icon className="size-4 animate-spin" /> : null}
                RENDER IGEN
              </Button>
            </div>
          </div>
        </section>
      </div>
    </main>
  );
}

function num(value: unknown) {
  return typeof value === "number" ? value : 0;
}

function isVideo(file: File) {
  if (file.type.startsWith("video/")) return true;
  return /\.(mp4|mov|m4v|webm|mkv|avi|mpeg|mpg|3gp)$/i.test(file.name);
}

function messageOf(payload: { error?: string; detail?: string }, fallback: string) {
  if (typeof payload?.error === "string" && payload.error) return payload.error;
  if (typeof payload?.detail === "string" && payload.detail) return payload.detail;
  return fallback;
}

async function readEvents(response: Response, onEvent: (event: string, data: Record<string, unknown>) => void) {
  const reader = response.body?.getReader();
  if (!reader) return;
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const chunks = buffer.split("\n\n");
    buffer = chunks.pop() || "";
    for (const chunk of chunks) {
      let event = "message";
      const dataLines: string[] = [];
      for (const line of chunk.split("\n")) {
        if (line.startsWith("event:")) event = line.slice(6).trim();
        if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
      }
      if (dataLines.length === 0) continue;
      onEvent(event, JSON.parse(dataLines.join("\n")) as Record<string, unknown>);
    }
  }
}
