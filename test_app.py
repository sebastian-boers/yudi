import os
import re
import tempfile
import unittest
from unittest.mock import patch
os.environ['SECRET_KEY'] = 'offline-test-signing-key'
os.environ['DISCOGS_TOKEN'] = 'offline-test-token'
os.environ['YUDI_DATA_DIR'] = tempfile.mkdtemp()
import app as y

class ParserTests(unittest.TestCase):
    def test_approved_hosts_and_exact_ids(self):
        good = 'abcdefghijk'
        for url in [f'https://youtu.be/{good}', f'https://www.youtube.com/watch?v={good}', f'https://youtube.com/embed/{good}', f'https://m.youtube.com/shorts/{good}']:
            self.assertEqual(y.extract_youtube_embed_url(url), f'https://www.youtube.com/embed/{good}')
        for url in ['https://evil.test/watch?v=abcdefghijk', 'https://youtu.be/short', 'https://youtube.com/watch?v=abcdefghijkMORE', 'https://youtube.com.evil/embed/abcdefghijk']:
            self.assertIsNone(y.extract_youtube_embed_url(url))

class Response:
    def __init__(self, data, status=200, headers=None):
        self.data, self.status_code, self.headers = data, status, headers or {}
    def json(self): return self.data
    def iter_content(self, chunk_size=16384):
        import json
        body = json.dumps(self.json()).encode()
        for offset in range(0, len(body), chunk_size):
            yield body[offset:offset+chunk_size]
    def close(self):
        self.closed = True
    def raise_for_status(self):
        if self.status_code >= 400: raise y.requests.HTTPError(response=self)

class CacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        y.app.config['YUDI_DATA_DIR'] = self.temp.name
    def tearDown(self): self.temp.cleanup()
    def test_shared_fresh_cache_exact_params(self):
        with patch.object(y.DISCOGS_SESSION, 'get', return_value=Response({'results': []})) as http:
            a = y.discogs_api_request('https://api.discogs.com/database/search', {'style': 'House', 'page': 1})
            b = y.discogs_api_request('https://api.discogs.com/database/search', {'page': 1, 'style': 'House'})
            self.assertEqual(a, b)
            self.assertEqual(http.call_count, 1)
            y.discogs_api_request('https://api.discogs.com/database/search', {'style': 'house', 'page': 1})
            self.assertEqual(http.call_count, 2)

    def test_two_instances_single_flight(self):
        import concurrent.futures
        import time
        def upstream(*args, **kw):
            time.sleep(.08)
            return Response({'title': 'one', 'videos': []})
        with patch.object(y.DISCOGS_SESSION, 'get', side_effect=upstream) as http:
            with concurrent.futures.ThreadPoolExecutor(2) as pool:
                results = list(pool.map(lambda _: y.discogs_api_request('https://api.discogs.com/releases/2'), range(2)))
            self.assertEqual(results[0], results[1])
            self.assertEqual(http.call_count, 1)

    def test_request_deadline_and_auth_abort(self):
        self.assertTrue(hasattr(y, 'Budget'), 'selection needs a request/deadline budget')
        url = 'https://api.discogs.com/releases/9'
        with patch.object(y.DISCOGS_SESSION, 'get', return_value=Response({}, 401)) as http:
            self.assertEqual(y.discogs_api_request(url, budget=y.Budget())['status'], 401)
            self.assertEqual(http.call_count, 1)
        with patch.object(y.DISCOGS_SESSION, 'get', return_value=Response({'videos': []})) as http:
            budget = y.Budget(requests=0)
            self.assertIn('budget', y.discogs_api_request(url, budget=budget)['error'].lower())
            budget = y.Budget(seconds=0)
            self.assertIn('deadline', y.discogs_api_request(url, budget=budget)['error'].lower())
            self.assertEqual(http.call_count, 0)

    def test_late_response_does_not_select_or_cache(self):
        import time
        def slow(*args, **kwargs):
            time.sleep(.15)
            return Response({'videos': []})
        with patch.object(y.DISCOGS_SESSION, 'get', side_effect=slow) as http:
            result = y.discogs_api_request('https://api.discogs.com/releases/55', budget=y.Budget(seconds=.08))
            self.assertEqual(http.call_count, 1)
        self.assertIn('deadline', result.get('error', '').lower())
        self.assertIsNone(y.store().cached(y.cache_key('https://api.discogs.com/releases/55')))

    def test_late_rate_limit_still_preserves_shared_cooldown(self):
        import time
        class SlowClose429(Response):
            def close(self):
                # Headers are known before the deadline; body/cleanup may lag.
                time.sleep(.15)
                super().close()
        def slow(*args, **kwargs):
            return SlowClose429({}, 429, {'Retry-After': '60'})
        with patch.object(y.DISCOGS_SESSION, 'get', side_effect=slow) as http:
            result = y.discogs_api_request('https://api.discogs.com/releases/59', budget=y.Budget(seconds=.08))
            self.assertEqual(result.get('status'), 429)
            self.assertEqual(y.discogs_api_request('https://api.discogs.com/releases/60')['status'], 429)
            self.assertEqual(http.call_count, 1)

    def test_no_implicit_redirect_requests(self):
        with patch.object(y.DISCOGS_SESSION, 'get', return_value=Response({}, 302)) as http:
            y.discogs_api_request('https://api.discogs.com/releases/57')
            self.assertIs(http.call_args.kwargs.get('allow_redirects'), False)

    def test_response_schema_is_validated(self):
        for body in [{'videos': None}, {'videos': [None]}, {'labels': None}]:
            with patch.object(y.DISCOGS_SESSION, 'get', return_value=Response(body)):
                result = y.discogs_api_request('https://api.discogs.com/releases/56')
                self.assertIn('error', result)

    def test_no_store_negative_freshness_and_failures(self):
        url = 'https://api.discogs.com/releases/1'
        with patch.object(y.DISCOGS_SESSION, 'get', return_value=Response({'videos': []}, headers={'Cache-Control': 'no-store'})) as http:
            y.discogs_api_request(url); y.discogs_api_request(url)
            self.assertEqual(http.call_count, 2)
        with patch.object(y.DISCOGS_SESSION, 'get', return_value=Response({'videos': []}, headers={'Cache-Control': 'max-age=0'})) as http:
            y.discogs_api_request(url); y.discogs_api_request(url)
            self.assertEqual(http.call_count, 2)
        with patch.object(y.DISCOGS_SESSION, 'get', return_value=Response({}, 429, {'Retry-After': '60'})) as http:
            self.assertEqual(y.discogs_api_request(url)['status'], 429)
            self.assertEqual(y.discogs_api_request(url)['status'], 429)
            self.assertEqual(http.call_count, 1)

class BrowserTests(unittest.TestCase):
    def setUp(self):
        CacheTests.setUp(self)
        y.app.config['TESTING'] = True
        self.client = y.app.test_client()
        self.calls = []
        def http(url, params=None, **kw):
            self.calls.append((url, params))
            if '/search' in url:
                return Response({'pagination': {'pages': 1, 'items': 1}, 'results': [{'resource_url': 'https://api.discogs.com/releases/1'}]})
            return Response({'title': 'Album title', 'videos': [{'uri': 'https://youtu.be/abcdefghijk', 'title': 'Clip A'}, {'uri': 'https://youtube.com/watch?v=lmnopqrstuv', 'title': 'Clip B'}], 'labels': [{'name': 'Label', 'resource_url': 'https://api.discogs.com/labels/7'}]})
        self.mock = patch.object(y.DISCOGS_SESSION, 'get', side_effect=http)
        self.mock.start()
    def tearDown(self):
        self.mock.stop()
        CacheTests.tearDown(self)
    def post(self, client=None, **fields):
        client = client or self.client
        page = client.get('/').get_data(as_text=True)
        token = re.search(r'name="csrf_token" value="([^"]+)"', page)
        self.assertIsNotNone(token, 'all forms need CSRF')
        return client.post('/', data={'csrf_token': token.group(1), **fields}, follow_redirects=True)
    def test_selection_prg_history_refresh_and_crossfilters(self):
        first = self.post(style=' House ').get_data(as_text=True)
        self.assertIn('Recent clips', first)
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.calls[0][1]['per_page'], 100)
        self.assertIn('Album</a>', first)
        self.assertIn('Label</a>', first)
        self.assertEqual(self.client.get('/').get_data(as_text=True), first)
        self.assertEqual(len(self.calls), 2)
        second = self.post(reroll='true').get_data(as_text=True)
        self.assertEqual(len(self.calls), 2)
        self.assertNotEqual(re.search(r'src="(https://www.youtube.com/embed/[^"]+)', first).group(1), re.search(r'src="(https://www.youtube.com/embed/[^"]+)', second).group(1))
        third = self.post(country='Sweden').get_data(as_text=True)
        self.assertIn('No new clip found yet', third)
        self.assertEqual(len(self.calls), 3)
        self.assertEqual(self.post(action='clear').status_code, 200)
        self.assertIn('History cleared', self.client.get('/').get_data(as_text=True))
        self.assertIn('video-wrapper', self.post(style='House').get_data(as_text=True))
    def test_random_pages_not_always_first_and_stop_on_auth(self):
        seen = set()
        def http(url, params=None, **kw):
            if '/search' in url:
                page = params.get('page', 1)
                return Response({'pagination': {'pages': 3, 'items': 300}, 'results': [{'resource_url': f'https://api.discogs.com/releases/{page}'}]})
            rid = int(url.rsplit('/', 1)[1])
            seen.add(rid)
            return Response({'videos': [{'uri': 'https://youtu.be/'+str(rid).zfill(11)}]})
        with patch.object(y.DISCOGS_SESSION, 'get', side_effect=http):
            for _ in range(24):
                self.post(y.app.test_client())
        self.assertGreater(len(seen), 1)
        self.assertTrue(hasattr(y, 'select_clip'), 'bounded selection API missing')
        # A new filter misses search cache; auth failure must stop discovery.
        with patch.object(y.DISCOGS_SESSION, 'get', return_value=Response({}, 403)) as http:
            page = self.post(style='new').get_data(as_text=True)
            self.assertIn('authentication', page)
            self.assertEqual(http.call_count, 1)

    def test_non_ascii_csrf_rejected_without_exception(self):
        self.client.get('/')
        self.assertEqual(self.client.post('/', data={'csrf_token':'☃'}).status_code, 400)

    def test_csrf_replay_browser_isolation_and_blank_reroll(self):
        self.assertEqual(self.client.post('/', data={'style': 'House'}).status_code, 400)
        page = self.post().get_data(as_text=True)
        self.assertIn('🎲 Re-roll', page)
        ident = re.search(r'<option value="([A-Za-z0-9_-]{11})"', page).group(1)
        count = len(self.calls)
        self.assertIn('/embed/'+ident, self.post(action='replay', video_id=ident).get_data(as_text=True))
        self.assertEqual(len(self.calls), count)
        other = y.app.test_client()
        self.assertEqual(self.post(other, action='replay', video_id=ident).status_code, 404)
        self.assertIn('video-wrapper', self.post(other).get_data(as_text=True))

class AestheticTests(unittest.TestCase):
    def test_original_css_script_static_and_decorations(self):
        import pathlib, hashlib
        root = pathlib.Path(__file__).parent
        html = (root/'templates/index.html').read_text()
        css = re.search(r'<style>(.*?)</style>', html, re.S).group(1)
        self.assertEqual(hashlib.sha256(css[:2425].encode()).hexdigest(), 'bd182e2b5b8f3b5bddae6c42cfc996de1252c0019072ac537cd11081fc689e5c')
        script = re.search(r'<script>(.*?)</script>', html, re.S).group(1)
        self.assertEqual(hashlib.sha256(script.encode()).hexdigest(), '97be65f6b24665c5ce37e877692d27403a4f4e908b7b1059a7fe815eb8392cf5')
        for name, digest in {'styles.json': 'e8190f1a2aa712797d2253cd8dc322490999e72228365e1b869bb0f5943f9805', 'countries.json': '0f17ae2cfadb0e4ff35ac8af11ac4df4011ef810ccc69cebfdb2f61350e932e4', 'gem-icon.png': '968822ce37f524d21d7b0414e500d408f1f55a36e1a87c81057af0f318854bbc'}.items():
            self.assertEqual(hashlib.sha256((root/'static'/name).read_bytes()).hexdigest(), digest)
        for text in ['<div class="gem"></div>', 'Get Random Video', '🎲 Re-roll', 'class="video-wrapper"', 'class="video-title"', 'id="style"', 'id="country"']:
            self.assertIn(text, html)
        self.assertIn('Recent clips', html)

if __name__ == '__main__':
    unittest.main()
