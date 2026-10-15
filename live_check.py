import json
import subprocess
from pathlib import Path
from scraper import SoundCloudScraper
from downloader import download_mp3
url = "https://soundcloud.com/dilxcoff/montagem-alquimia-2-slowed-2"
with SoundCloudScraper() as sc:
    offline = sc.parse_html(Path("sound-view.html").read_text(encoding="utf-8"), url, include_raw=True)
    print("HTML:", offline.track.title, offline.track.duration_ms, "client_id:", bool(offline.client_id), "formats:", [t.preset for t in offline.transcodings], flush=True)
    live = sc.scrape_track(url)
    Path("live-result.json").write_text(json.dumps(live.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    print("LIVE:", live.track.title, live.track.duration_ms, "comments:",len(live.comments), "warnings:", live.warnings, flush=True)
    path = download_mp3(live, "downloads/test-track.mp3")
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration,size:stream=codec_name", "-of", "json", path], capture_output=True, text=True, check=True)
    Path("live-audio-probe.json").write_text(probe.stdout, encoding="utf-8")
    print(probe.stdout, flush=True)
