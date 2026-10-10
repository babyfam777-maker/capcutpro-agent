import json
import queue
import shutil
import tempfile
import threading
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from .agent import run_turn
from .config import MODEL, OPENAI_BASE_URL, WORKER_TOKEN
from .ffmpeg_util import run
from .store import Project, store

app = FastAPI(title="CapCutPro worker")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def token_gate(request, call_next):
    if WORKER_TOKEN and request.url.path != "/health":
        if request.headers.get("x-worker-token") != WORKER_TOKEN:
            return JSONResponse({"error": "Fel token till workern."}, status_code=401)
    return await call_next(request)


@app.get("/health")
def health():
    ffmpeg = True
    try:
        run(["ffmpeg", "-version"], "ffmpeg")
    except Exception:
        ffmpeg = False
    return {"ok": ffmpeg, "ffmpeg": ffmpeg, "model": MODEL, "baseUrl": OPENAI_BASE_URL}


@app.post("/projects")
def create_project(file: UploadFile = File(...)):
    suffix = Path(file.filename or "video.mp4").suffix or ".mp4"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        shutil.copyfileobj(file.file, tmp)
        tmp_path = Path(tmp.name)
    try:
        project = store.create(file.filename or "video.mp4", tmp_path)
    except Exception as error:
        tmp_path.unlink(missing_ok=True)
        raise HTTPException(400, str(error)) from error
    tmp_path.unlink(missing_ok=True)
    return project.public()


@app.get("/projects/{project_id}")
def get_project(project_id: str):
    return _project(project_id).public()


@app.post("/projects/{project_id}/messages")
def post_message(project_id: str, body: dict):
    project = _project(project_id)
    text = str(body.get("text") or "").strip()
    if not text:
        raise HTTPException(400, "Text saknas.")
    if not project.busy:
        project.busy = True
    else:
        raise HTTPException(409, "En redigering pågår redan för den här videon.")

    events: queue.Queue = queue.Queue()

    def work():
        try:
            run_turn(project, text, lambda event, data: events.put((event, data)))
        except Exception as error:
            events.put(("error", {"message": str(error)}))
        finally:
            project.busy = False
            project.save()
            events.put(None)

    threading.Thread(target=work, daemon=True).start()

    def stream():
        while True:
            item = events.get()
            if item is None:
                break
            event, data = item
            yield f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/projects/{project_id}/undo")
def undo(project_id: str):
    project = _project(project_id)
    if project.busy:
        raise HTTPException(409, "En redigering pågår redan.")
    result = project.editor.undo()
    project.save()
    return {"result": result, "project": project.public()}


@app.get("/projects/{project_id}/original")
def original(project_id: str):
    project = _project(project_id)
    return FileResponse(project.original, media_type="video/mp4", filename=project.filename)


@app.get("/projects/{project_id}/versions/{version}/video")
def version_video(project_id: str, version: int):
    project = _project(project_id)
    path = project.folder / "versions" / f"v{version}" / "output.mp4"
    if not path.exists():
        raise HTTPException(404, "Den versionen finns inte.")
    return FileResponse(path, media_type="video/mp4", filename=f"v{version}.mp4")


@app.get("/projects/{project_id}/versions/{version}/plan")
def version_plan(project_id: str, version: int):
    project = _project(project_id)
    found = next((v for v in project.editor.versions if v["version"] == version), None)
    if not found:
        raise HTTPException(404, "Den versionen finns inte.")
    return {"version": version, "plan": found["plan"], "probe": found.get("probe")}


def _project(project_id: str) -> Project:
    try:
        return store.get(project_id)
    except KeyError as error:
        raise HTTPException(404, "Projektet finns inte.") from error
