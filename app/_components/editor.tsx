"use client";

import { FilmIcon, Loader2Icon, Undo2Icon } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";

type Version = { version: number; duration?: number; loudness?: number; width?: number; height?: number };
type Trace = { name: string; ok?: boolean };
type ChatMessage = { role: string; content: string; trace?: Trace[] };
type Project = {
  id: string;
  filename: string;
  activeVersion: number | null;
  analyzed: boolean;
  versions: Version[];
  messages: ChatMessage[];
  plan: { outputDuration?: number; clips?: unknown[]; style?: { captionFontSize?: number } };
  originalHash?: string;
};

const STEP_LABELS: Record<string, string> = {
  analyze: "Analyserar videon",
  probe: "Läser filen",
  scenes: "Letar scener",
  silence: "Letar tystnad",
  "motion-faces": "Följer ansikten och rörelse",
  transcript: "Skriver ut vad som sägs",
  thinking: "Planerar klippet",
  render: "Renderar",
};

const SUGGESTIONS = [
  "Gör den här till en viral Short",
  "Gör texten större",
  "Första delen är för lång",
  "Ångra",
];

export function Editor() {
  const [project, setProject] = useState<Project | null>(null);
  const [view, setView] = useState<number | "original" | null>(null);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [step, setStep] = useState<string>("");
  const [progress, setProgress] = useState<number | null>(null);
  const [liveTrace, setLiveTrace] = useState<Trace[]>([]);
  const [error, setError] = useState<string>("");
  const [workerUp, setWorkerUp] = useState<boolean | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    void fetch("/api/worker/health")
      .then((response) => setWorkerUp(response.ok))
      .catch(() => setWorkerUp(false));
  }, []);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [project?.messages.length, liveTrace.length, step]);

  async function refresh(id: string) {
    const response = await fetch(`/api/worker/projects/${id}`);
    if (!response.ok) throw new Error("Kunde inte läsa projektet.");
    const next = (await response.json()) as Project;
    setProject(next);
    setView((current) => {
      if (current === "original") return current;
      if (next.activeVersion) return next.activeVersion;
      return current;
    });
    return next;
  }

  async function onFile(file: File) {
    setError("");
    setBusy(true);
    setStep("Laddar upp");
    try {
      const body = new FormData();
      body.append("file", file);
      const response = await fetch("/api/worker/projects", { method: "POST", body });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || "Uppladdningen misslyckades.");
      setProject(payload);
      setView("original");
      setLiveTrace([]);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Uppladdningen misslyckades.");
    } finally {
      setBusy(false);
      setStep("");
    }
  }

  async function send(message: string) {
    if (!project || busy) return;
    const trimmed = message.trim();
    if (!trimmed) return;
    setText("");
    setError("");
    setBusy(true);
    setProgress(null);
    setLiveTrace([]);
    setStep("thinking");
    setProject((current) =>
      current ? { ...current, messages: [...current.messages, { role: "user", content: trimmed }] } : current,
    );
    try {
      const response = await fetch(`/api/worker/projects/${project.id}/messages`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ text: trimmed }),
      });
      if (!response.ok || !response.body) {
        const payload = await response.json().catch(() => ({ error: "Meddelandet misslyckades." }));
        throw new Error(payload.error || "Meddelandet misslyckades.");
      }
      await readEvents(response, (event, data) => {
        if (event === "status" && data.step) setStep(String(data.step));
        if (event === "progress") setProgress(typeof data.progress === "number" ? data.progress : null);
        if (event === "tool") setLiveTrace((current) => [...current, { name: String(data.name), ok: true }]);
        if (event === "tool_result") {
          setLiveTrace((current) => {
            const copy = [...current];
            const last = copy[copy.length - 1];
            if (last && last.name === data.name) last.ok = data.ok !== false;
            return copy;
          });
        }
        if (event === "version" && data.activeVersion) setView(Number(data.activeVersion));
        if (event === "error") setError(String(data.message || "Något gick fel."));
        if (event === "done" && data.activeVersion) setView(Number(data.activeVersion));
      });
      const next = await refresh(project.id);
      if (next.activeVersion) setView(next.activeVersion);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Något gick fel.");
    } finally {
      setBusy(false);
      setStep("");
      setProgress(null);
    }
  }

  async function undo() {
    if (!project || busy) return;
    setBusy(true);
    setError("");
    try {
      const response = await fetch(`/api/worker/projects/${project.id}/undo`, { method: "POST" });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || "Kunde inte ångra.");
      setProject(payload.project);
      if (payload.project.activeVersion) setView(payload.project.activeVersion);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Kunde inte ångra.");
    } finally {
      setBusy(false);
    }
  }

  const videoSrc =
    project && view === "original"
      ? `/api/worker/projects/${project.id}/original`
      : project && typeof view === "number"
        ? `/api/worker/projects/${project.id}/versions/${view}/video`
        : null;

  return (
    <main className="flex h-dvh flex-col bg-background text-foreground">
      <header className="flex items-center justify-between border-b px-4 py-3">
        <div>
          <p className="font-medium tracking-tight">CapCutPro</p>
          <p className="text-muted-foreground text-xs">Ladda upp en video, skriv vad du vill, få en Short.</p>
        </div>
        {project ? (
          <Button disabled={busy || !project.activeVersion} onClick={() => void undo()} size="sm" type="button" variant="outline">
            <Undo2Icon className="size-4" />
            Ångra
          </Button>
        ) : null}
      </header>

      {workerUp === false ? (
        <p className="bg-destructive/10 px-4 py-2 text-destructive text-sm" role="alert">
          Workern är inte igång. Starta <code>start.sh</code> på Mac eller Linux, eller <code>start.ps1</code> på Windows.
        </p>
      ) : null}

      <div className="grid min-h-0 flex-1 md:grid-cols-[minmax(280px,420px)_1fr]">
        <section className="flex min-h-0 flex-col items-center gap-3 border-b p-4 md:border-r md:border-b-0">
          {videoSrc ? (
            <video className="aspect-[9/16] h-full max-h-[70vh] w-auto rounded-lg bg-black" controls key={videoSrc} playsInline src={videoSrc} />
          ) : (
            <button
              className="flex aspect-[9/16] w-full max-w-xs flex-col items-center justify-center gap-3 rounded-lg border border-dashed text-muted-foreground"
              onClick={() => fileRef.current?.click()}
              type="button"
            >
              <FilmIcon className="size-8" />
              <span>Välj en video</span>
            </button>
          )}
          <div className="flex w-full flex-wrap items-center gap-2">
            <Button onClick={() => fileRef.current?.click()} size="sm" type="button" variant="secondary">
              {project ? "Ny video" : "Välj video"}
            </Button>
            {project ? (
              <Button onClick={() => setView("original")} size="sm" type="button" variant={view === "original" ? "default" : "outline"}>
                Original
              </Button>
            ) : null}
            {project?.versions.map((version) => (
              <Button
                key={version.version}
                onClick={() => setView(version.version)}
                size="sm"
                type="button"
                variant={view === version.version ? "default" : "outline"}
              >
                v{version.version}
                {version.duration ? ` · ${version.duration.toFixed(1)}s` : ""}
              </Button>
            ))}
          </div>
          {project && typeof view === "number" ? (
            <a className="text-sm underline underline-offset-4" download href={`/api/worker/projects/${project.id}/versions/${view}/video`}>
              Ladda ner MP4
            </a>
          ) : null}
          <input
            accept="video/*"
            className="sr-only"
            onChange={(event) => {
              const file = event.target.files?.[0];
              if (file) void onFile(file);
              event.target.value = "";
            }}
            ref={fileRef}
            type="file"
          />
        </section>

        <section className="flex min-h-0 flex-col">
          <div className="min-h-0 flex-1 space-y-4 overflow-y-auto px-4 py-4">
            {project ? null : (
              <p className="text-muted-foreground text-sm">
                Originalet sparas orört. Varje render blir en ny version, v1, v2, och så vidare. Ångra går tillbaka till förra versionen.
              </p>
            )}
            {project?.messages.map((message, index) => (
              <article className={message.role === "user" ? "ml-8" : "mr-8"} key={`${message.role}-${index}`}>
                <p className="text-muted-foreground text-xs">{message.role === "user" ? "Du" : "CapCutPro"}</p>
                <p className="whitespace-pre-wrap text-sm">{message.content}</p>
                {message.trace && message.trace.length > 0 ? (
                  <p className="mt-1 text-muted-foreground text-xs">{message.trace.map((item) => item.name).join(" → ")}</p>
                ) : null}
              </article>
            ))}
            {busy ? (
              <div className="flex items-center gap-2 text-muted-foreground text-sm" aria-live="polite">
                <Loader2Icon className="size-4 animate-spin" />
                <span>{STEP_LABELS[step] || step || "Jobbar"}</span>
                {progress !== null ? <span>{Math.round(progress * 100)}%</span> : null}
              </div>
            ) : null}
            {liveTrace.length > 0 ? <p className="text-muted-foreground text-xs">{liveTrace.map((item) => item.name).join(" → ")}</p> : null}
            {error ? (
              <p className="text-destructive text-sm" role="alert">
                {error}
              </p>
            ) : null}
            <div ref={bottomRef} />
          </div>
          <form
            className="border-t p-3"
            onSubmit={(event) => {
              event.preventDefault();
              void send(text);
            }}
          >
            {project && project.messages.length === 0 ? (
              <div className="mb-2 flex flex-wrap gap-2">
                {SUGGESTIONS.slice(0, 1).map((suggestion) => (
                  <Button key={suggestion} disabled={busy} onClick={() => void send(suggestion)} size="sm" type="button" variant="outline">
                    {suggestion}
                  </Button>
                ))}
              </div>
            ) : null}
            {project && project.messages.length > 0 ? (
              <div className="mb-2 flex flex-wrap gap-2">
                {SUGGESTIONS.slice(1).map((suggestion) => (
                  <Button key={suggestion} disabled={busy} onClick={() => void send(suggestion)} size="sm" type="button" variant="outline">
                    {suggestion}
                  </Button>
                ))}
              </div>
            ) : null}
            <div className="flex gap-2">
              <Textarea
                disabled={!project || busy}
                onChange={(event) => setText(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && !event.shiftKey) {
                    event.preventDefault();
                    void send(text);
                  }
                }}
                placeholder={project ? "Skriv vad du vill ändra…" : "Ladda upp en video först"}
                rows={2}
                value={text}
              />
              <Button disabled={!project || busy || text.trim().length === 0} type="submit">
                Skicka
              </Button>
            </div>
          </form>
        </section>
      </div>
    </main>
  );
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
