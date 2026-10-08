"""Offline integration/worker regressions; only external HTTP is mocked."""
import concurrent.futures
import importlib
import multiprocessing
import pathlib
import sqlite3
import time
import unittest
from unittest.mock import patch
import test_app as fixtures
from test_app import Response, y

# Use separate processes and separately opened SQLite connections, like workers.
def worker_select(browser, gate, output):
    gate.wait(3)
    output.put(y.select_clip(browser, {'year': '', 'style': '', 'country': ''}))

class PersistenceTests(unittest.TestCase):
    setUp = fixtures.BrowserTests.setUp
    tearDown = fixtures.BrowserTests.tearDown
    post = fixtures.BrowserTests.post

    def test_same_browser_two_worker_atomic_reservation(self):
        self.client.get('/')
        with self.client.session_transaction() as session:
            browser = session['browser']
        # Fill catalogue, but don't reserve any clip yet.
        y.discogs_api_request('https://api.discogs.com/database/search', {'type': 'release', 'per_page': 100, 'page': 1})
        y.discogs_api_request('https://api.discogs.com/releases/1')
        ctx = multiprocessing.get_context('fork')
        gate, output = ctx.Event(), ctx.Queue()
        workers = [ctx.Process(target=worker_select, args=(browser, gate, output)) for _ in range(2)]
        for worker in workers: worker.start()
        gate.set()
        results = [output.get(timeout=5) for _ in workers]
        for worker in workers:
            worker.join(5)
            self.assertEqual(worker.exitcode, 0)
        self.assertEqual(len({r['video'] for r in results}), 2)
        self.assertEqual(len(y.store().history(browser)), 2)

    def test_signed_persistent_cookie_survives_module_restart(self):
        page = self.post(style='House').get_data(as_text=True)
        cookie = self.client.get_cookie('session')
        self.assertIsNotNone(cookie.expires)
        self.assertTrue(cookie.http_only)
        directory = self.temp.name
        importlib.reload(y)
        y.app.config.update(TESTING=True, YUDI_DATA_DIR=directory)
        reopened = y.app.test_client()
        reopened.set_cookie('session', cookie.value)
        with patch.object(y.DISCOGS_SESSION, 'get', side_effect=AssertionError('GET must not use upstream')):
            self.assertEqual(reopened.get('/').get_data(as_text=True), page)
        tampered = y.app.test_client()
        tampered.set_cookie('session', cookie.value+'bad')
        self.assertNotIn('Clip A', tampered.get('/').get_data(as_text=True))
        self.assertNotIn('Clip B', tampered.get('/').get_data(as_text=True))
        self.assertLess(len(cookie.value), 1000)
        with y.store().connection() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM history').fetchone()[0], 1)

    def test_dropdown100_is_not_exclusion_retention(self):
        self.client.get('/')
        with self.client.session_transaction() as session:
            browser = session['browser']
        for n in range(105):
            vid = str(n).zfill(11)
            self.assertTrue(y.store().reserve(browser, vid, 1, {}, {'current': vid}))
        self.assertEqual(len(y.store().history(browser)), 100)
        page = self.client.get('/').get_data(as_text=True)
        self.assertEqual(page.count('<option value='), 100)
        self.assertFalse(y.store().reserve(browser, '00000000000', 1, {}, {}))
        with y.store().connection() as db:
            db.execute('UPDATE history SET recent=? WHERE browser=? AND video=?', (time.time()-91*86400, browser, '00000000000'))
        self.client.get('/')
        self.assertTrue(y.store().reserve(browser, '00000000000', 1, {}, {}))

    def test_expired_metadata_omitted_on_get_replay_refreshes(self):
        page = self.post().get_data(as_text=True)
        import re
        vid = re.search(r'<option value="([A-Za-z0-9_-]{11})"', page).group(1)
        with y.store().connection() as db:
            db.execute('UPDATE cache SET expires=0')
        calls = len(self.calls)
        expired = self.client.get('/').get_data(as_text=True)
        self.assertEqual(len(self.calls), calls)
        self.assertNotIn('Album title', expired)
        # Label identity is a retained history reference, not stale metadata.
        self.assertIn('>Label</a>', expired)
        refreshed = self.post(action='replay', video_id=vid).get_data(as_text=True)
        self.assertEqual(len(self.calls), calls+1)
        self.assertIn('Album title', refreshed)
        self.assertIn('>Label</a>', refreshed)

    def test_form_validation_and_303(self):
        self.client.get('/')
        with self.client.session_transaction() as session: csrf = session['csrf']
        for fields in [{'year':'199'}, {'style': 'x'*121}, {'action':'invalid'}, {'country':'x\nxx'}]:
            response = self.client.post('/', data={'csrf_token':csrf, **fields})
            self.assertEqual(response.status_code, 400)
        response = self.client.post('/', data={'csrf_token':csrf})
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers['Location'], '/')
        self.assertEqual(response.headers['Cache-Control'], 'private, no-store')

class PolicyTests(unittest.TestCase):
    setUp = fixtures.CacheTests.setUp
    tearDown = fixtures.CacheTests.tearDown

    def test_negative_ttl_and_expiry_no_stale(self):
        url = 'https://api.discogs.com/releases/77'
        with patch.object(y.DISCOGS_SESSION, 'get', return_value=Response({'videos': []})) as http:
            y.discogs_api_request(url); y.discogs_api_request(url)
            self.assertEqual(http.call_count, 1)
            with y.store().connection() as db:
                expiry = db.execute('SELECT expires FROM cache').fetchone()[0]
                self.assertLessEqual(expiry-time.time(), 900)
                db.execute('UPDATE cache SET expires=0')
            http.return_value = Response({}, 401)
            self.assertEqual(y.discogs_api_request(url)['status'], 401)
            self.assertIsNone(y.store().cached(y.cache_key(url)))

    def test_retry_after_http_date_shared_across_instances(self):
        from email.utils import formatdate
        from storage import Store
        date = formatdate(time.time()+60, usegmt=True)
        with patch.object(y.DISCOGS_SESSION, 'get', return_value=Response({}, 429, {'Retry-After':date})) as http:
            first = y.discogs_api_request('https://api.discogs.com/releases/88')
            second = y.discogs_api_request('https://api.discogs.com/releases/89')
            self.assertEqual(http.call_count, 1)
            self.assertEqual(first['status'], second['status'])
            self.assertGreater(first['retry_after'], 55)
            self.assertLessEqual(first['retry_after'], 61)
            independent = Store(self.temp.name)
            with independent.connection() as db:
                self.assertGreater(db.execute('SELECT until FROM cooldown').fetchone()[0], time.time())

    def test_transient_retry_bounded_request_budget(self):
        with patch.object(y.DISCOGS_SESSION, 'get', side_effect=y.requests.Timeout()) as http:
            result = y.discogs_api_request('https://api.discogs.com/releases/99', budget=y.Budget(requests=1))
            self.assertEqual(http.call_count, 1)
            self.assertIn('budget', result['error'].lower())
        with patch.object(y.DISCOGS_SESSION, 'get', return_value=Response({}, 503)) as http:
            result = y.discogs_api_request('https://api.discogs.com/releases/99')
            self.assertEqual(http.call_count, 2)
            self.assertIn('failure', result['error'].lower())

    def test_no_store_retains_only_identifiers_not_metadata(self):
        with patch.object(y.DISCOGS_SESSION, 'get', return_value=Response({'videos':[{'uri':'https://youtu.be/abcdefghijk'}], 'title':'never-persist-this'}, headers={'Cache-Control':'no-store'})):
            y.discogs_api_request('https://api.discogs.com/releases/100')
        content = pathlib.Path(y.store().path).read_bytes()
        for value in [b'never-persist-this', b'offline-test-token', b'offline-test-signing-key']:
            self.assertNotIn(value, content)

    def test_stricter_maxage_age_and_cache_size(self):
        with patch.object(y.DISCOGS_SESSION, 'get', return_value=Response({'videos':[]}, headers={'Cache-Control':'max-age=30', 'Age':'20'})):
            y.discogs_api_request('https://api.discogs.com/releases/101')
        with y.store().connection() as db:
            self.assertLessEqual(db.execute('SELECT expires FROM cache').fetchone()[0]-time.time(), 10)
        for n in range(10): y.store().put(str(n), {'n':n}, 100, limit=4)
        with y.store().connection() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM cache').fetchone()[0], 4)

if __name__ == '__main__': unittest.main()
