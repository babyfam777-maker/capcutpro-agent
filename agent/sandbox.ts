import { defineSandbox } from "eve/sandbox";
import { VercelSandbox } from "eve/sandbox/vercel";

const STATIC_FFMPEG_URL =
  "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-linux64-gpl.tar.xz";
const CAPTION_FONT_URL = "https://github.com/google/fonts/raw/main/ofl/anton/Anton-Regular.ttf";

export const FONTS_DIR = "/opt/fonts";

// Package managers first. A static Linux build is only the fallback, and an
// image that already has ffmpeg is left alone.
const PREPARE_SCRIPT = `
set -euo pipefail
if ! command -v ffmpeg >/dev/null 2>&1 || ! command -v ffprobe >/dev/null 2>&1; then
  if command -v apt-get >/dev/null 2>&1; then
    sudo apt-get update -qq && sudo apt-get install -y -qq ffmpeg
  elif command -v dnf >/dev/null 2>&1; then
    sudo dnf install -y -q ffmpeg || true
  elif command -v yum >/dev/null 2>&1; then
    sudo yum install -y -q ffmpeg || true
  elif command -v apk >/dev/null 2>&1; then
    sudo apk add --no-cache ffmpeg
  elif command -v brew >/dev/null 2>&1; then
    brew install ffmpeg
  fi
fi
if ! command -v ffmpeg >/dev/null 2>&1 || ! command -v ffprobe >/dev/null 2>&1; then
  cd /tmp
  curl -fsSL -o ffmpeg.tar.xz ${STATIC_FFMPEG_URL}
  rm -rf ffmpeg-extract && mkdir ffmpeg-extract
  tar -xJf ffmpeg.tar.xz -C ffmpeg-extract --strip-components=1
  sudo install -m 0755 ffmpeg-extract/bin/ffmpeg ffmpeg-extract/bin/ffprobe /usr/local/bin/
  rm -rf ffmpeg-extract ffmpeg.tar.xz
fi
sudo mkdir -p ${FONTS_DIR}
if [ ! -s ${FONTS_DIR}/Anton-Regular.ttf ]; then
  sudo curl -fsSL -o ${FONTS_DIR}/Anton-Regular.ttf ${CAPTION_FONT_URL}
fi
ffmpeg -hide_banner -version | head -1
ffprobe -hide_banner -version | head -1
`;

export const environment = VercelSandbox.environment({
  prepare: async (sandbox) => {
    const result = await sandbox.run({ command: `bash -c '${PREPARE_SCRIPT.replaceAll("'", "'\\''")}'` });
    if (result.exitCode !== 0) {
      throw new Error(`FFmpeg installation failed: ${result.stderr || result.stdout}`);
    }
  },
});

export default defineSandbox(() =>
  environment.open({
    resources: { vcpus: 4 },
  }),
);
