"use client";

import { upload } from "@vercel/blob/client";
import { FilmIcon, Loader2Icon } from "lucide-react";
import { useRef, useState } from "react";
import { PromptInputButton } from "@/components/ai-elements/prompt-input";

export type UploadedVideo = { pathname: string; filename: string; role: "source" | "reference" };

export function VideoUploadButton({
  disabled,
  onUploaded,
  onError,
}: {
  readonly disabled?: boolean;
  readonly onUploaded: (video: UploadedVideo) => void;
  readonly onError: (message: string) => void;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [progress, setProgress] = useState<number | null>(null);

  const handleFiles = async (files: FileList | null) => {
    if (!files) return;
    for (const file of Array.from(files)) {
      if (!file.type.startsWith("video/")) {
        onError(`${file.name} is not a video file.`);
        continue;
      }
      setProgress(0);
      try {
        const safeName = file.name.replace(/[^a-zA-Z0-9._-]/g, "_").slice(-80) || "video.mp4";
        const blob = await upload(`uploads/${safeName}`, file, {
          access: "private",
          handleUploadUrl: "/api/upload",
          multipart: file.size > 50 * 1024 * 1024,
          onUploadProgress: ({ percentage }) => setProgress(Math.round(percentage)),
        });
        onUploaded({ pathname: blob.pathname, filename: file.name, role: "source" });
      } catch (error) {
        onError(error instanceof Error ? error.message : "Upload failed.");
      } finally {
        setProgress(null);
      }
    }
    if (inputRef.current) inputRef.current.value = "";
  };

  const uploading = progress !== null;

  return (
    <>
      <input
        accept="video/*"
        className="sr-only"
        multiple
        onChange={(event) => void handleFiles(event.currentTarget.files)}
        ref={inputRef}
        tabIndex={-1}
        type="file"
      />
      <PromptInputButton
        aria-label={uploading ? `Uploading video, ${progress}%` : "Upload video"}
        disabled={disabled || uploading}
        onClick={() => inputRef.current?.click()}
        variant="ghost"
      >
        {uploading ? <Loader2Icon className="size-4 animate-spin" /> : <FilmIcon className="size-4" />}
        <span className="text-sm">{uploading ? `${progress}%` : "Video"}</span>
      </PromptInputButton>
    </>
  );
}
