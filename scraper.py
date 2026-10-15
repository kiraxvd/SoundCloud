"""
Scraper completo de uma track do SoundCloud.

Extrai:
  - metadados da track (id, urn, título, duração, genre, tags, etc.)
  - usuário (id, nome, seguidores, avatar, etc.)
  - comments (via api-v2)
  - waveform (JSON)
  - transcodings (HLS/AAC) + track_authorization
  - client_id atual da página

Uso:
    from scraper import SoundCloudScraper
    sc = SoundCloudScraper()
    data = sc.scrape_track("https://soundcloud.com/dilxcoff/montagem-alquimia-2-slowed-2")
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field, asdict
from typing import Any
from urllib.parse import urlparse, urljoin
import shlex

import httpx
from selectolax.lexbor import LexborHTMLParser as HTMLParser


UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

DEFAULT_HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
    "Upgrade-Insecure-Requests": "1",
}


# ----------------------------------------------------------------------------
# Dataclasses — modelo de dados limpo para devolver pela API
# ----------------------------------------------------------------------------

@dataclass
class Track:
    id: int | None = None
    urn: str | None = None
    title: str | None = None
    permalink: str | None = None
    permalink_url: str | None = None
    description: str | None = None
    genre: str | None = None
    tag_list: list[str] = field(default_factory=list)
    duration_ms: int | None = None
    full_duration_ms: int | None = None
    created_at: str | None = None
    display_date: str | None = None
    last_modified: str | None = None
    license: str | None = None
    policy: str | None = None
    monetization_model: str | None = None
    streamable: bool | None = None
    downloadable: bool | None = None
    commentable: bool | None = None
    sharing: str | None = None
    state: str | None = None
    playback_count: int | None = None
    likes_count: int | None = None
    reposts_count: int | None = None
    comment_count: int | None = None
    download_count: int | None = None
    artwork_url: str | None = None
    waveform_url: str | None = None
    publisher_metadata: dict[str, Any] | None = None
    label_name: str | None = None
    isrc: str | None = None


@dataclass
class User:
    id: int | None = None
    urn: str | None = None
    username: str | None = None
    permalink: str | None = None
    permalink_url: str | None = None
    full_name: str | None = None
    description: str | None = None
    city: str | None = None
    country_code: str | None = None
    avatar_url: str | None = None
    followers_count: int | None = None
    followings_count: int | None = None
    track_count: int | None = None
    playlist_count: int | None = None
    likes_count: int | None = None
    reposts_count: int | None = None
    verified: bool | None = None
    badges: dict[str, Any] | None = None


@dataclass
class Comment:
    id: int | None = None
    body: str | None = None
    created_at: str | None = None
    timestamp_ms: int | None = None
    user_id: int | None = None
    user_username: str | None = None
    user_permalink: str | None = None
    user_avatar: str | None = None


@dataclass
class Transcoding:
    url: str
    preset: str | None = None
    duration_ms: int | None = None
    protocol: str | None = None
    mime_type: str | None = None
    quality: str | None = None
    snipped: bool | None = None


@dataclass
class ScrapeResult:
    source_url: str
    scraped_at: float
    client_id: str | None = None
    track_authorization: str | None = None
    track: Track = field(default_factory=Track)
    user: User = field(default_factory=User)
    comments: list[Comment] = field(default_factory=list)
    transcodings: list[Transcoding] = field(default_factory=list)
    waveform: dict[str, Any] | None = None
    tags_html: list[str] = field(default_factory=list)
    og_meta: dict[str, str] = field(default_factory=dict)
    twitter_meta: dict[str, str] = field(default_factory=dict)
    json_ld: list[dict[str, Any]] = field(default_factory=list)

    warnings: list[str] = field(default_factory=list)
    raw_hydration: list[dict[str, Any]] | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        if d["raw_hydration"] is None:
            d.pop("raw_hydration")
        return d


# ----------------------------------------------------------------------------
# Scraper
# ----------------------------------------------------------------------------

class SoundCloudScraper:
    """
    Scraper "completo" de uma track do SoundCloud.

    Faz:
      1. GET na página pública (com headers realistas)
      2. Parse do HTML (selectolax) para meta tags OG/twitter, JSON-LD, tags visíveis
      3. Extrai window.__sc_hydration → track + user + transcodings + track_authorization
      4. Extrai client_id de scripts inline
      5. Chama api-v2 (com client_id) para pegar comments paginados
      6. Baixa o waveform JSON
    """

    CLIENT_ID_RE = re.compile(r'client_id\s*[:=]\s*["\']([A-Za-z0-9]{20,})["\']')
    API_V2 = "https://api-v2.soundcloud.com"

    def __init__(self, timeout: float = 30.0, proxy: str | None = None):
        self.timeout = timeout
        self.proxy = proxy
        self._client: httpx.Client | None = None
        self.warnings: list[str] = []

    # -- context manager -------------------------------------------------
    def __enter__(self):
        self._client = httpx.Client(
            headers=DEFAULT_HEADERS,
            timeout=self.timeout,
            follow_redirects=True,
            proxy=self.proxy,
        )
        return self

    def __exit__(self, *exc):
        if self._client:
            self._client.close()
            self._client = None

    # -- helpers ---------------------------------------------------------
    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                headers=DEFAULT_HEADERS,
                timeout=self.timeout,
                follow_redirects=True,
                proxy=self.proxy,
            )
        return self._client

    @staticmethod
    def _extract_hydration(html: str) -> list[dict[str, Any]]:
        match = re.search(r"window\.__sc_hydration\s*=\s*", html)
        if not match:
            return []
        try:
            data, _ = json.JSONDecoder().raw_decode(html[match.end():].lstrip())
            return data if isinstance(data, list) else []
        except json.JSONDecodeError:
            return []

    def _extract_client_id(self, html: str) -> str | None:
        for entry in self._extract_hydration(html):
            if entry.get("hydratable") == "apiClient":
                value = (entry.get("data") or {}).get("id")
                if value:
                    return str(value)
        # 1) tenta pegar dos scripts
        m = self.CLIENT_ID_RE.search(html)
        if m:
            return m.group(1)
        # 2) tenta achar em assets JS
        scripts = re.findall(r'src="(https://a-v2\.sndcdn\.com/assets/[^"]+\.js)"', html)
        for url in scripts[:15]:
            try:
                response = self.client.get(url)
                response.raise_for_status()
                js = response.text
            except Exception:
                continue
            m = self.CLIENT_ID_RE.search(js)
            if m:
                return m.group(1)
        return None

    # -- passos ----------------------------------------------------------
    def fetch_html(self, url: str) -> str:
        validate_track_url(url)
        for _ in range(6):
            response = self.client.get(url, follow_redirects=False)
            if response.is_redirect:
                url = urljoin(url, response.headers["location"])
                validate_track_url(url)
                continue
            response.raise_for_status()
            return response.text
        raise ScraperError("Redirecionamentos demais no link.", 502)

    def parse_meta(self, html: str) -> tuple[dict, dict, list, list]:
        """Retorna (og, twitter, json_ld, tags_html)."""
        tree = HTMLParser(html)
        og: dict[str, str] = {}
        tw: dict[str, str] = {}
        for node in tree.css("meta"):
            prop = node.attributes.get("property") or node.attributes.get("name") or ""
            name = node.attributes.get("name") or ""
            content = node.attributes.get("content") or ""
            if prop.startswith("og:"):
                og[prop] = content
            elif prop.startswith("twitter:") or name.startswith("twitter:"):
                tw[prop or name] = content

        json_ld: list[dict] = []
        for node in tree.css('script[type="application/ld+json"]'):
            try:
                json_ld.append(json.loads(node.text()))
            except Exception:
                pass

        tags_html = [n.text(strip=True) for n in tree.css("a.tag") if n.text(strip=True)]
        return og, tw, json_ld, tags_html

    def fetch_comments(self, track_id: int, client_id: str, limit: int = 200) -> list[Comment]:
        comments: list[Comment] = []
        url = f"{self.API_V2}/tracks/{track_id}/comments"
        seen_urls, seen_ids = set(), set()
        while url and len(comments) < limit:
            if url in seen_urls:
                self.warnings.append("Comentários: paginação repetida; retorno parcial.")
                break
            seen_urls.add(url)
            if urlparse(url).hostname != "api-v2.soundcloud.com":
                self.warnings.append("Comentários: URL de paginação inválida.")
                break
            try:
                params = {"client_id": client_id}
                if len(seen_urls) == 1:
                    params.update(limit=min(50, limit), offset=0, linked_partitioning=1, threaded=0)
                response = self.client.get(url, params=params)
                response.raise_for_status()
                data = response.json()
                collection = data if isinstance(data, list) else data.get("collection", [])
                for item in collection:
                    if item.get("id") is not None and item["id"] in seen_ids:
                        continue
                    seen_ids.add(item.get("id"))
                    user = item.get("user") or {}
                    comments.append(Comment(
                        id=item.get("id"), body=item.get("body"),
                        created_at=item.get("created_at"), timestamp_ms=item.get("timestamp"),
                        user_id=user.get("id"), user_username=user.get("username"),
                        user_permalink=user.get("permalink_url"), user_avatar=user.get("avatar_url"),
                    ))
                    if len(comments) >= limit:
                        break
                url = data.get("next_href") if isinstance(data, dict) else None
            except (httpx.HTTPError, ValueError, TypeError) as exc:
                status = getattr(getattr(exc, "response", None), "status_code", None)
                self.warnings.append(f"Comentários indisponíveis ou parciais (HTTP {status or 'erro de rede/dados'}).")
                break
        return comments

    def fetch_waveform(self, waveform_url: str | None) -> dict[str, Any] | None:
        if not waveform_url:
            return None
        try:
            validate_media_url(waveform_url)
            response = self.client.get(waveform_url)
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError) as exc:
            self.warnings.append("Waveform indisponível.")
            return None

    # -- scrape principal ------------------------------------------------
    def scrape_track(self, url: str, with_comments: bool = True,
                     with_waveform: bool = True, comments_limit: int = 200,
                     include_raw: bool = False) -> ScrapeResult:
        html = self.fetch_html(url)
        return self.parse_html(html, url, with_comments, with_waveform, comments_limit, include_raw)

    def parse_html(self, html: str, url: str = "", with_comments: bool = False,
                   with_waveform: bool = False, comments_limit: int = 200,
                   include_raw: bool = False) -> ScrapeResult:
        self.warnings = []
        og, tw, json_ld, tags_html = self.parse_meta(html)
        hydration = self._extract_hydration(html)

        client_id = self._extract_client_id(html)

        sound = next((h["data"] for h in hydration if h.get("hydratable") == "sound"), None)
        if not sound:
            raise ScraperError("Hidratação sound ausente: link inválido, faixa removida ou página bloqueada.", 502)

        track = Track(
            id=sound.get("id"),
            urn=sound.get("urn"),
            title=sound.get("title"),
            permalink=sound.get("permalink"),
            permalink_url=sound.get("permalink_url"),
            description=sound.get("description"),
            genre=sound.get("genre"),
            tag_list=_parse_tag_list(sound.get("tag_list")),
            duration_ms=sound.get("duration"),
            full_duration_ms=sound.get("full_duration"),
            created_at=sound.get("created_at"),
            display_date=sound.get("display_date"),
            last_modified=sound.get("last_modified"),
            license=sound.get("license"),
            policy=sound.get("policy"),
            monetization_model=sound.get("monetization_model"),
            streamable=sound.get("streamable"),
            downloadable=sound.get("downloadable"),
            commentable=sound.get("commentable"),
            sharing=sound.get("sharing"),
            state=sound.get("state"),
            playback_count=sound.get("playback_count"),
            likes_count=sound.get("likes_count"),
            reposts_count=sound.get("reposts_count"),
            comment_count=sound.get("comment_count"),
            download_count=sound.get("download_count"),
            artwork_url=sound.get("artwork_url"),
            waveform_url=sound.get("waveform_url"),
            publisher_metadata=sound.get("publisher_metadata"),
            label_name=sound.get("label_name"),
            isrc=(sound.get("publisher_metadata") or {}).get("isrc"),
        )

        u = sound.get("user") or {}
        user = User(
            id=u.get("id"),
            urn=u.get("urn"),
            username=u.get("username"),
            permalink=u.get("permalink"),
            permalink_url=u.get("permalink_url"),
            full_name=u.get("full_name"),
            description=u.get("description"),
            city=u.get("city"),
            country_code=u.get("country_code"),
            avatar_url=u.get("avatar_url"),
            followers_count=u.get("followers_count"),
            followings_count=u.get("followings_count"),
            track_count=u.get("track_count"),
            playlist_count=u.get("playlist_count"),
            likes_count=u.get("likes_count"),
            reposts_count=u.get("reposts_count"),
            verified=u.get("verified"),
            badges=u.get("badges"),
        )

        transcodings = []
        for t in (sound.get("media") or {}).get("transcodings", []):
            fmt = t.get("format") or {}
            transcodings.append(Transcoding(
                url=t.get("url") or "",
                preset=t.get("preset"),
                duration_ms=t.get("duration"),
                protocol=fmt.get("protocol"),
                mime_type=fmt.get("mime_type"),
                quality=t.get("quality"),
                snipped=t.get("snipped"),
            ))

        comments: list[Comment] = []
        if with_comments and client_id and track.id:
            comments = self.fetch_comments(track.id, client_id, comments_limit)

        if with_comments and not client_id:
            self.warnings.append("Comentários não consultados: client_id ausente.")

        waveform = self.fetch_waveform(track.waveform_url) if with_waveform else None

        return ScrapeResult(
            source_url=url,
            warnings=list(self.warnings),
            raw_hydration=hydration if include_raw else None,
            scraped_at=time.time(),
            client_id=client_id,
            track_authorization=sound.get("track_authorization"),
            track=track,
            user=user,
            comments=comments,
            transcodings=transcodings,
            waveform=waveform,
            tags_html=tags_html,
            og_meta=og,
            twitter_meta=tw,
            json_ld=json_ld,
        )


def _parse_tag_list(raw: Any) -> list[str]:
    if not raw:
        return []
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    try:
        return shlex.split(str(raw))
    except ValueError:
        return str(raw).split()


class ScraperError(RuntimeError):
    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.status_code = status_code


def validate_track_url(url: str) -> str:
    try:
        parsed = urlparse(url)
        port = parsed.port
    except ValueError:
        raise ScraperError("URL inválida.", 400)
    if (parsed.scheme != "https" or parsed.hostname not in {
        "soundcloud.com", "www.soundcloud.com", "m.soundcloud.com", "on.soundcloud.com"
    } or parsed.username or parsed.password or port not in (None, 443)):
        raise ScraperError("Use um link HTTPS de uma faixa do SoundCloud.", 400)
    if not parsed.path.strip("/"):
        raise ScraperError("Informe o link de uma faixa, não a página inicial.", 400)
    return url


def validate_media_url(url: str) -> str:
    try:
        parsed = urlparse(url)
        port = parsed.port
    except ValueError:
        raise ScraperError("URL de mídia inválida.", 502)
    host = parsed.hostname or ""
    if (parsed.scheme != "https" or parsed.username or parsed.password
            or parsed.port not in (None, 443)
            or not any(host == domain or host.endswith("." + domain)
                       for domain in ("soundcloud.com", "sndcdn.com", "soundcloud.cloud"))):
        raise ScraperError("URL de mídia externa ao SoundCloud rejeitada.", 502)
    return url
