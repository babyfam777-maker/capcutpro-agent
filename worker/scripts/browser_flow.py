"""Browser acceptance: upload, Swedish edit, play v1, play v2, revise, undo, download."""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
SOURCE = Path(os.environ.get(
    "SANA_CLIP",
    "/home/ubuntu/.cursor/projects/workspace/uploads/9010595258876bd5290f5c776e3f192aa07445c04ff4a16a499c30315bea06c6_5b48.mp4",
))
OUT = Path(os.environ.get("BROWSER_OUT", "/opt/cursor/artifacts/browser-flow"))
PAGE = os.environ.get("APP_URL", "http://127.0.0.1:3000")
PROMPT = "Gör en vertikal short. Behåll originalljudet och lägg ingen musik."
REVISION = "mer zoom på hans reaktion"


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    if not SOURCE.exists():
        raise SystemExit(f"missing source {SOURCE}")
    report: dict = {"prompt": PROMPT, "revision": REVISION, "steps": []}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=False,
            channel="chrome",
            args=["--autoplay-policy=no-user-gesture-required", "--use-fake-ui-for-media-stream"],
        )
        page = browser.new_page(viewport={"width": 1440, "height": 1100})
        page.set_default_timeout(1_200_000)
        page.goto(PAGE, wait_until="domcontentloaded")
        page.locator("#video-file").set_input_files(str(SOURCE))
        page.get_by_text("Ny video", exact=True).wait_for(timeout=120_000)
        shot(page, "01-uploaded")
        page.locator("#story-prompt").fill(PROMPT)
        shot(page, "02-prompt")
        page.get_by_role("button", name="GÖR MIN SHORT").click()
        played_live = False
        deadline = time.time() + 1200
        while time.time() < deadline:
            src = page.locator("video").get_attribute("src") or ""
            if "/versions/1/" in src and not played_live:
                play = play_video(page)
                report["steps"].append({"name": "play-v1-live", **play})
                shot(page, "03-v1-playing")
                if not play.get("ok"):
                    raise SystemExit(f"v1 did not play: {play}")
                played_live = True
            if button_enabled(page, "GÖR MIN SHORT") and "/versions/" in src:
                break
            page.wait_for_timeout(1000)
        else:
            raise SystemExit("first render did not finish")
        if not played_live:
            open_version(page, 1)
            play = play_video(page)
            report["steps"].append({"name": "play-v1", **play})
            shot(page, "03-v1-playing")
            if not play.get("ok"):
                raise SystemExit(f"v1 did not play: {play}")
        open_version(page, 2)
        play = play_video(page)
        report["steps"].append({"name": "play-v2", **play})
        shot(page, "04-v2-playing")
        if not play.get("ok"):
            raise SystemExit(f"v2 did not play: {play}")
        report["chatAfterV2"] = page.locator("body").inner_text()[:8000]
        if "Trend" not in report["chatAfterV2"] and "9.5" not in report["chatAfterV2"]:
            raise SystemExit("the page never showed which trend signal drove the cut")
        page.get_by_placeholder("Gör hooken större").fill(REVISION)
        shot(page, "05-revision-typed")
        page.get_by_role("button", name="RENDER IGEN").click()
        wait_src(page, "/versions/3/")
        page.wait_for_function(
            """() => {
                const button = [...document.querySelectorAll("button")].find((item) => item.textContent && item.textContent.includes("RENDER IGEN"));
                return button && !button.disabled;
            }"""
        )
        play = play_video(page)
        report["steps"].append({"name": "play-v3", **play})
        shot(page, "06-v3-playing")
        if not play.get("ok"):
            raise SystemExit(f"v3 did not play: {play}")
        page.get_by_role("button", name="Ångra", exact=True).click()
        wait_src(page, "/versions/2/")
        page.wait_for_timeout(500)
        play = play_video(page)
        report["steps"].append({"name": "play-after-undo", **play})
        shot(page, "07-undo")
        if not play.get("ok"):
            raise SystemExit(f"undo did not play the previous version: {play}")
        src = page.locator("video").get_attribute("src") or ""
        report["srcAfterUndo"] = src
        if "/versions/2/" not in src:
            raise SystemExit(f"undo did not return to v2: {src}")
        with page.expect_download(timeout=120_000) as download_info:
            page.get_by_role("button", name="Ladda ner MP4").click()
        download = download_info.value
        target = OUT / "downloaded.mp4"
        download.save_as(str(target))
        report["download"] = str(target)
        report["downloadBytes"] = target.stat().st_size
        shot(page, "08-downloaded")
        browser.close()
    (OUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps({"ok": True, "download": report["downloadBytes"], "steps": report["steps"]}, ensure_ascii=False), flush=True)


def button_enabled(page, label: str) -> bool:
    return page.evaluate(
        """(label) => {
            const button = [...document.querySelectorAll("button")].find((item) => item.textContent && item.textContent.includes(label));
            return Boolean(button && !button.disabled);
        }""",
        label,
    )


def open_version(page, version: int) -> None:
    page.get_by_role("button", name=re.compile(rf"^v{version}\\b")).click()
    wait_src(page, f"/versions/{version}/")


def wait_src(page, needle: str) -> None:
    deadline = time.time() + 1_200
    last = ""
    while time.time() < deadline:
        src = page.locator("video").get_attribute("src") or ""
        if needle in src:
            return
        last = src
        page.wait_for_timeout(1000)
    raise SystemExit(f"timed out waiting for {needle}; last src {last}")


def play_video(page) -> dict:
    return page.evaluate(
        """async () => {
            const video = document.querySelector("video");
            if (!video) return {ok: false, reason: "no video"};
            video.muted = false;
            try {
                await video.play();
            } catch (error) {
                video.muted = true;
                try { await video.play(); } catch (again) {
                    return {ok: false, reason: String(again), src: video.currentSrc};
                }
            }
            const started = performance.now();
            while (video.currentTime < 0.2 && performance.now() - started < 15000) {
                await new Promise((resolve) => setTimeout(resolve, 100));
            }
            return {
                ok: video.currentTime >= 0.2 && !video.error,
                currentTime: video.currentTime,
                duration: video.duration,
                width: video.videoWidth,
                height: video.videoHeight,
                paused: video.paused,
                src: video.currentSrc,
                error: video.error ? video.error.message : null,
            };
        }"""
    )


def shot(page, name: str) -> None:
    page.screenshot(path=str(OUT / f"{name}.png"), full_page=True)


if __name__ == "__main__":
    main()
