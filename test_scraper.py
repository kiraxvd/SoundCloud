"""Regressões: hidratação real, paginação, falhas opcionais e arquivos da API."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import httpx
from fastapi.testclient import TestClient
import api
from scraper import SoundCloudScraper, ScraperError, _parse_tag_list, validate_track_url
from downloader import ranked_transcodings, DownloadError

HTML = Path(__file__).with_name("sound-view.html").read_text(encoding="utf-8")
URL = "https://soundcloud.com/dilxcoff/montagem-alquimia-2-slowed-2"

class ScraperTests(unittest.TestCase):
    def test_reference_html(self):
        with SoundCloudScraper() as sc:
            result = sc.parse_html(HTML, URL, include_raw=True)
        self.assertEqual(result.track.id, 2262302216)
        self.assertEqual(result.track.duration_ms, 99781)
        self.assertEqual(result.user.username, "DILXC")
        self.assertTrue(result.client_id)
        self.assertEqual(result.twitter_meta["twitter:title"], result.track.title)
        self.assertEqual(ranked_transcodings(result)[0].preset, "aac_160k")
        self.assertIsInstance(result.to_dict()["raw_hydration"], list)

    def test_json_with_delimiters_and_mixed_tags(self):
        value = '[{"hydratable":"sound","data":{"title":"x ]; y"}}]'
        self.assertEqual(SoundCloudScraper._extract_hydration('window.__sc_hydration='+value+';other();')[0]["data"]["title"], 'x ]; y')
        self.assertEqual(_parse_tag_list('electronic "brazilian phonk" slowed'), ['electronic','brazilian phonk','slowed'])

    def test_pagination_limit_and_client_id(self):
        calls = []
        def handler(request):
            calls.append(request)
            self.assertEqual(request.url.params["client_id"], "test-id")
            if len(calls) == 1:
                self.assertEqual(request.url.params["threaded"], "0")
                return httpx.Response(200,json={"collection":[{"id":1,"body":"one"}],"next_href":"https://api-v2.soundcloud.com/next?offset=1"})
            return httpx.Response(200,json={"collection":[{"id":1},{"id":2},{"id":3}]})
        with SoundCloudScraper() as sc:
            sc._client.close()
            sc._client = httpx.Client(transport=httpx.MockTransport(handler))
            sc.warnings = []
            result = sc.fetch_comments(1, "test-id", limit=2)
        self.assertEqual([c.id for c in result],[1,2])

    def test_comments_failure_is_visible(self):
        with SoundCloudScraper() as sc:
            sc._client.close()
            sc._client = httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(429)))
            sc.warnings=[]
            self.assertEqual(sc.fetch_comments(1,'test'),[])
            self.assertIn('429',sc.warnings[0])

    def test_no_preview_or_blocked_download(self):
        with SoundCloudScraper() as sc:
            result=sc.parse_html(HTML, URL)
        for t in result.transcodings:
            t.snipped=True
        with self.assertRaises(DownloadError): ranked_transcodings(result)

    def test_api_invalid_urls_and_health(self):
        with TestClient(api.app) as client:
            self.assertTrue(client.get('/health').json()['ok'])
            self.assertEqual(client.get('/scrape',params={'url':'http://127.0.0.1/secret'}).status_code,400)
            self.assertEqual(client.get('/track',params={'url':'https://soundcloud.com.evil.test/a/b'}).status_code,400)
            self.assertEqual(client.get('/files/../../etc/passwd.mp3').status_code,404)
            self.assertEqual(client.get('/scrape').status_code,422)

    def test_mp3_response_cleans_temporary_file(self):
        with SoundCloudScraper() as sc:
            result=sc.parse_html(HTML,URL)
        paths=[]
        def fake_download(result,path):
            paths.append(Path(path))
            Path(path).write_bytes(b'ID3'+b'x'*2048)
            return path
        with patch.object(api.SoundCloudScraper,'scrape_track',return_value=result), patch.object(api,'find_ffmpeg',return_value='ffmpeg'), patch.object(api,'download_mp3',side_effect=fake_download):
            with TestClient(api.app) as client:
                response=client.get('/mp3',params={'url':URL})
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.headers['content-type'],'audio/mpeg')
        self.assertFalse(paths[0].parent.exists())

if __name__ == '__main__': unittest.main(verbosity=2)
