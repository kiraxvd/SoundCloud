"""API local: metadados, retorno JSON e download MP3."""
from __future__ import annotations
import json
import os
import re
import shutil
import tempfile
import uuid
from dataclasses import asdict
from pathlib import Path
import httpx
from fastapi import FastAPI, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from starlette.background import BackgroundTask
from downloader import download_mp3, find_ffmpeg
from scraper import SoundCloudScraper, ScraperError

BASE = Path(__file__).resolve().parent
DOWNLOADS = Path(os.environ.get("SC_DOWNLOAD_DIR", str(BASE / "downloads"))).resolve()
app = FastAPI(title="SoundCloud Scraper API", version="2.0")


@app.exception_handler(ScraperError)
async def scraper_error(request: Request, exc: ScraperError):
    return JSONResponse(status_code=exc.status_code, content={"detail": str(exc)})


@app.exception_handler(httpx.HTTPError)
async def upstream_error(request: Request, exc: httpx.HTTPError):
    status = getattr(getattr(exc, "response", None), "status_code", None)
    code = 404 if status == 404 else 429 if status == 429 else 502
    return JSONResponse(status_code=code, content={"detail": f"SoundCloud indisponível ou bloqueou a requisição (HTTP {status or 'erro de conexão'})."})


@app.exception_handler(OSError)
async def storage_error(request: Request, exc: OSError):
    return JSONResponse(status_code=500, content={"detail": "Não foi possível gravar ou ler o arquivo local."})


@app.get("/", include_in_schema=False)
def home():
    return FileResponse(BASE / "index.html", media_type="text/html")


@app.get("/health")
def health():
    try:
        find_ffmpeg()
        available = True
    except ScraperError:
        available = False
    return {"ok": True, "ffmpeg_available": available}


@app.get("/scrape")
def scrape(url: str = Query(...), with_comments: bool = True, with_waveform: bool = True,
           comments_limit: int = Query(200, ge=0, le=1000)):
    with SoundCloudScraper() as sc:
        return sc.scrape_track(url, with_comments, with_waveform, comments_limit).to_dict()


@app.get("/scrape/raw")
def scrape_raw(url: str = Query(...)):
    with SoundCloudScraper() as sc:
        return sc.scrape_track(url, include_raw=True).to_dict()


@app.get("/track")
def track_only(url: str = Query(...)):
    with SoundCloudScraper() as sc:
        return asdict(sc.scrape_track(url, with_comments=False, with_waveform=False).track)


@app.get("/comments")
def comments_only(url: str = Query(...), limit: int = Query(200, ge=0, le=1000)):
    with SoundCloudScraper() as sc:
        result = sc.scrape_track(url, with_waveform=False, comments_limit=limit)
    return {"comments": [asdict(c) for c in result.comments], "count": len(result.comments),
            "warnings": result.warnings}


def safe_name(value: str | None) -> str:
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value or "track").strip(" .")[:120] or "track"


@app.get("/mp3")
def mp3(url: str = Query(...)):
    find_ffmpeg()
    with SoundCloudScraper() as sc:
        result = sc.scrape_track(url, with_comments=False, with_waveform=False)
    directory = Path(tempfile.mkdtemp(prefix="sc-mp3-"))
    try:
        path = download_mp3(result, str(directory / "track.mp3"))
    except Exception:
        shutil.rmtree(directory)
        raise
    return FileResponse(path, media_type="audio/mpeg", filename=safe_name(result.track.title) + ".mp3",
                        background=BackgroundTask(shutil.rmtree, directory))


class DownloadRequest(BaseModel):
    url: str


@app.post("/download")
def save_download(body: DownloadRequest):
    find_ffmpeg()
    with SoundCloudScraper() as sc:
        result = sc.scrape_track(body.url)
    DOWNLOADS.mkdir(parents=True, exist_ok=True)
    identifier = uuid.uuid4().hex
    path = DOWNLOADS / (identifier + ".mp3")
    download_mp3(result, str(path))
    data = result.to_dict()
    data["download"] = {"id": identifier, "filename": safe_name(result.track.title) + ".mp3",
                        "url": f"/files/{identifier}.mp3", "size_bytes": path.stat().st_size,
                        "format": "mp3", "saved": True}
    try:
        path.with_suffix(".json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        path.unlink(missing_ok=True)
        raise
    return data


@app.get("/files/{identifier}.mp3")
def saved_file(identifier: str):
    if not re.fullmatch(r"[a-f0-9]{32}", identifier):
        return JSONResponse(status_code=404, content={"detail": "Arquivo não encontrado."})
    path = DOWNLOADS / (identifier + ".mp3")
    if not path.is_file():
        return JSONResponse(status_code=404, content={"detail": "Arquivo não encontrado."})
    name = "track.mp3"
    metadata = path.with_suffix(".json")
    if metadata.is_file():
        name = json.loads(metadata.read_text(encoding="utf-8"))["download"]["filename"]
    return FileResponse(path, media_type="audio/mpeg", filename=name)
