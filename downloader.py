"""Resolve a mídia pública do SoundCloud e converte para MP3 com FFmpeg."""
from __future__ import annotations
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
import httpx
from scraper import SoundCloudScraper, ScrapeResult, ScraperError, UA, validate_media_url


class DownloadError(ScraperError):
    pass


def find_ffmpeg() -> str:
    configured = os.environ.get("FFMPEG_BINARY")
    binary = shutil.which(configured or "ffmpeg")
    if not binary:
        raise DownloadError("FFmpeg não encontrado. Instale-o no PATH ou configure FFMPEG_BINARY.", 503)
    return binary


def ranked_transcodings(result: ScrapeResult):
    if result.track.policy in {"BLOCK", "SNIP"} or result.track.streamable is False:
        raise DownloadError("Faixa bloqueada ou disponível apenas como prévia.", 403)
    priority = ["aac_160k", "mp3_0_1", "mp3_0_0", "aac_96k", "opus_0_0", "abr_sq", "aac_64k"]
    candidates = [t for t in result.transcodings if t.url and not t.snipped
                  and t.protocol in {"hls", "progressive"}]
    if not candidates:
        raise DownloadError("Nenhum stream completo e público disponível.", 403)
    return sorted(candidates, key=lambda t: priority.index(t.preset) if t.preset in priority else len(priority))


def pick_best(result: ScrapeResult) -> dict:
    item = ranked_transcodings(result)[0]
    return {"url": item.url, "preset": item.preset, "mime": item.mime_type}


def resolve_stream_url(transcoding_url: str, client_id: str | None, authorization: str | None) -> str:
    validate_media_url(transcoding_url)
    if not client_id:
        raise DownloadError("client_id não encontrado na página. Tente consultar a faixa novamente.")
    params = {"client_id": client_id}
    if authorization:
        params["track_authorization"] = authorization
    with httpx.Client(timeout=30, headers={"User-Agent": UA}) as client:
        response = client.get(transcoding_url, params=params)
        response.raise_for_status()
        url = response.json().get("url")
    if not url:
        raise DownloadError("A API de mídia não retornou uma URL de áudio.")
    return validate_media_url(url)


def download_mp3(result: ScrapeResult, out_path: str = "track.mp3", timeout: float = 600) -> str:
    ffmpeg = find_ffmpeg()
    candidates = ranked_transcodings(result)
    output = Path(out_path).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    errors = []
    for transcoding in candidates:
        temporary = None
        try:
            stream = resolve_stream_url(transcoding.url, result.client_id, result.track_authorization)
            handle, filename = tempfile.mkstemp(suffix=".mp3", prefix=".sc-", dir=output.parent)
            os.close(handle)
            temporary = Path(filename)
            command = [ffmpeg, "-nostdin", "-y", "-hide_banner", "-loglevel", "error",
                       "-rw_timeout", "30000000", "-user_agent", UA,
                       "-protocol_whitelist", "https,http,tls,tcp,crypto",
                       "-i", stream, "-map", "0:a:0", "-vn", "-c:a", "libmp3lame", "-q:a", "2",
                       "-metadata", f"title={result.track.title or ''}",
                       "-metadata", f"artist={result.user.username or ''}",
                       "-metadata", f"comment={result.source_url}", "-f", "mp3", str(temporary)]
            process = subprocess.run(command, capture_output=True, text=True, errors="replace",
                                     timeout=timeout, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if process.returncode:
                # Signed CDN URLs may contain credentials; do not expose stderr in API responses.
                raise DownloadError(f"FFmpeg falhou com código {process.returncode}.")
            if temporary.stat().st_size < 1024:
                raise DownloadError("O arquivo retornado está vazio ou incompleto.")
            os.replace(temporary, output)
            return str(output)
        except (httpx.HTTPError, ValueError, OSError, subprocess.TimeoutExpired, ScraperError) as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            reason = f"HTTP {status}" if status else type(exc).__name__
            errors.append(f"{transcoding.preset}: {reason}")
        finally:
            if temporary:
                temporary.unlink(missing_ok=True)
    raise DownloadError("Nenhum formato pôde ser baixado (" + "; ".join(errors) + ").")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url", help="Link HTTPS da faixa")
    parser.add_argument("output", nargs="?", default="track.mp3")
    parser.add_argument("--json", dest="json_path", help="Salvar também o retorno completo em JSON")
    args = parser.parse_args()
    try:
        with SoundCloudScraper() as scraper:
            result = scraper.scrape_track(args.url)
        path = download_mp3(result, args.output)
        data = result.to_dict()
        data["download"] = {"path": path, "size_bytes": Path(path).stat().st_size, "format": "mp3"}
        if args.json_path:
            Path(args.json_path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0
    except (ScraperError, httpx.HTTPError, OSError) as exc:
        print(f"Erro: {type(exc).__name__}: {exc if not isinstance(exc, httpx.HTTPError) else 'Falha HTTP ao acessar SoundCloud'}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
