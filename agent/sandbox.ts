import { defineSandbox } from "eve/sandbox";
import { VercelSandbox } from "eve/sandbox/vercel";

const FFMPEG_URL = "https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz";
const CAPTION_FONT_URL = "https://github.com/google/fonts/raw/main/ofl/anton/Anton-Regular.ttf";

export const FONTS_DIR = "/opt/fonts";

const PREPARE_SCRIPT = `
set -euo pipefail
cd /tmp
if command -v dnf >/dev/null 2>&1; then
  sudo dnf install -y -q xz tar curl
elif command -v yum >/dev/null 2>&1; then
  sudo yum install -y -q xz tar curl
elif command -v apt-get >/dev/null 2>&1; then
  sudo apt-get update -qq && sudo apt-get install -y -qq xz-utils tar curl
else
  echo "Ingen känd pakethanterare hittades" >&2
  exit 1
fi
curl -fsSL -o ffmpeg.tar.xz ${FFMPEG_URL}
mkdir -p ffmpeg && tar -xJf ffmpeg.tar.xz -C ffmpeg --strip-components=1
sudo install -m 0755 ffmpeg/ffmpeg ffmpeg/ffprobe /usr/local/bin/
rm -rf ffmpeg ffmpeg.tar.xz
sudo mkdir -p ${FONTS_DIR}
sudo curl -fsSL -o ${FONTS_DIR}/Anton-Regular.ttf ${CAPTION_FONT_URL}
ffmpeg -hide_banner -version | head -1
`;
  }),
);
