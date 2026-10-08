"""Independent-review regressions: real SQLite, mocked HTTP boundary only."""
import json
import threading
import time
import unittest
from unittest.mock import patch
import test_app as fixtures
from test_app import Response, y


class ReviewDeadlineTests(unittest.TestCase):
    setUp = fixtures.CacheTests.setUp
    tearDown = fixtures.CacheTests.tearDown

    def test_slow_json_returns_by_deadline_without_late_cache_or_history(self):
        finished = threading.Event()

        class SlowJSON(Response):
            def json(self):
                time.sleep(.4)
                finished.set()
                return self.data

        def upstream(url, **kwargs):
            if '/search' in url:
                return Response({'results': [{'resource_url': 'https://api.discogs.com/releases/501'}]})
            return SlowJSON({'videos': [{'uri': 'https://youtu.be/abcdefghijk'}]})

        with patch.object(y.DISCOGS_SESSION, 'get', side_effect=upstream):
            start = time.monotonic()
            result = y.select_clip('review-browser', {}, y.Budget(seconds=.15))
            elapsed = time.monotonic() - start
            finished.wait(1)
        self.assertIn('deadline', result.get('error', '').lower())
        self.assertLess(elapsed, .30)
        self.assertIsNone(y.store().cached(y.cache_key('https://api.discogs.com/releases/501')))
        self.assertEqual(y.store().history('review-browser'), [])
        self.assertEqual(y.store().state('review-browser'), {})


    def test_streamed_body_size_and_deadline_are_bounded(self):
        class StreamResponse(Response):
            def __init__(self, chunks, delay=0):
                super().__init__({})
                self.chunks, self.delay = chunks, delay
                self.closed_event = threading.Event()
                self.reads = 0
            def iter_content(self, chunk_size=16384):
                for chunk in self.chunks:
                    time.sleep(self.delay)
                    self.reads += 1
                    yield chunk
            def close(self):
                super().close()
                self.closed_event.set()

        oversized = StreamResponse([b'x' * (1024 * 1024 + 1)])
        with patch.object(y.DISCOGS_SESSION, 'get', return_value=oversized) as http:
            result = y.discogs_api_request('https://api.discogs.com/releases/503', budget=y.Budget(requests=1))
        self.assertIn('error', result)
        self.assertTrue(oversized.closed_event.wait(1))
        self.assertIs(http.call_args.kwargs['stream'], True)
        self.assertIsNone(y.store().cached(y.cache_key('https://api.discogs.com/releases/503')))

        drip = StreamResponse([b' '] * 100, .02)
        with patch.object(y.DISCOGS_SESSION, 'get', return_value=drip):
            start = time.monotonic()
            result = y.discogs_api_request('https://api.discogs.com/releases/504', budget=y.Budget(seconds=.08))
            elapsed = time.monotonic() - start
            self.assertTrue(drip.closed_event.wait(1))
        self.assertIn('deadline', result['error'].lower())
        self.assertLess(elapsed, .20)
        self.assertLess(drip.reads, 10)
        self.assertIsNone(y.store().cached(y.cache_key('https://api.discogs.com/releases/504')))

    def test_drip_reader_checks_deadline_before_filling_large_chunk(self):
        closed = threading.Event()
        class BufferedDrip(Response):
            def iter_content(self, chunk_size=16384):
                # A streaming HTTP read waits for chunk_size bytes, even if
                # bytes arrive often enough to avoid socket inactivity timeout.
                body = bytearray()
                for _ in range(40):
                    time.sleep(.01)
                    body.extend(b' ')
                    if len(body) >= chunk_size:
                        yield bytes(body)
                        body.clear()
                if body:
                    yield bytes(body)
            def close(self):
                super().close()
                closed.set()
        response = BufferedDrip({})
        with patch.object(y.DISCOGS_SESSION, 'get', return_value=response):
            result = y.discogs_api_request('https://api.discogs.com/releases/530', budget=y.Budget(seconds=.06))
            stopped_promptly = closed.wait(.10)
            closed.wait(1)
        self.assertIn('deadline', result['error'].lower())
        self.assertTrue(stopped_promptly, 'worker keeps reading drip until a large chunk fills')

    def test_worker_admissions_stay_bounded_after_caller_timeout(self):
        gate = threading.Event()
        entered = threading.Event()
        lock = threading.Lock()
        calls = []
        def upstream(url, **kwargs):
            with lock:
                calls.append(url)
                if len(calls) == 2:
                    entered.set()
            gate.wait(2)
            return Response({'videos': []})
        try:
            with patch.object(y.DISCOGS_SESSION, 'get', side_effect=upstream):
                with y.concurrent.futures.ThreadPoolExecutor(6) as callers:
                    jobs = [callers.submit(y.discogs_api_request, f'https://api.discogs.com/releases/{510+n}', budget=y.Budget(seconds=.12)) for n in range(6)]
                    self.assertTrue(entered.wait(1))
                    results = [job.result(timeout=1) for job in jobs]
                    self.assertEqual(len(calls), 2)
                    self.assertTrue(all('deadline' in r.get('error', '').lower() for r in results))
        finally:
            gate.set()
        # Wait for admitted workers to close, without permitting background writes.
        deadline = time.monotonic()+1
        while time.monotonic() < deadline:
            if y._HTTP_SLOTS.acquire(blocking=False):
                y._HTTP_SLOTS.release()
                break
            time.sleep(.01)
        with y.store().connection() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM cache').fetchone()[0], 0)

    def test_429_headers_arriving_after_timeout_do_not_write_in_background(self):
        closed = threading.Event()
        class LateHeaders(Response):
            def close(self):
                super().close()
                closed.set()
        def upstream(*args, **kwargs):
            time.sleep(.15)
            return LateHeaders({}, 429, {'Retry-After': '60'})
        with patch.object(y.DISCOGS_SESSION, 'get', side_effect=upstream):
            start = time.monotonic()
            result = y.discogs_api_request('https://api.discogs.com/releases/520', budget=y.Budget(seconds=.06))
            elapsed = time.monotonic()-start
            self.assertTrue(closed.wait(1))
        self.assertIn('deadline', result['error'].lower())
        self.assertLess(elapsed, .12)
        with y.store().connection() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM cooldown').fetchone()[0], 0)

    def test_database_lock_wait_cannot_create_late_cache_or_reservation(self):
        database = y.store()
        for action in ('put', 'reserve'):
            with self.subTest(action=action):
                locked = threading.Event()
                def hold_writer():
                    with database.connection() as db:
                        db.execute('BEGIN IMMEDIATE')
                        locked.set()
                        time.sleep(.10)
                holder = threading.Thread(target=hold_writer)
                holder.start()
                self.assertTrue(locked.wait(1))
                deadline = time.monotonic()+.03
                if action == 'put':
                    result = database.put('locked', {'videos': []}, 60, deadline=deadline)
                else:
                    result = database.reserve('locked-browser', 'abcdefghijk', 1, {}, {}, deadline=deadline)
                holder.join(1)
                self.assertFalse(result)
                self.assertIsNone(database.cached('locked'))
                self.assertEqual(database.history('locked-browser'), [])
                self.assertEqual(database.state('locked-browser'), {})


class MalformedFieldTests(unittest.TestCase):
    setUp = fixtures.CacheTests.setUp
    tearDown = fixtures.CacheTests.tearDown

    def test_non_string_uri_fields_are_rejected_without_exception(self):
        for field in (123, True, [], {}, ['https://youtu.be/abcdefghijk']):
            with self.subTest(field=field):
                self.assertIsNone(y.extract_youtube_embed_url(field))
                self.assertIsNone(y.discogs_api_to_public_url(field))
                self.assertIn('error', y.discogs_api_request(field))

    def test_malformed_catalogue_fields_do_not_break_selection_or_formatter(self):
        def upstream(url, **kwargs):
            if '/search' in url:
                return Response({'results': [{'resource_url': 123}, {'resource_url': 'https://api.discogs.com/releases/502'}]})
            return Response({'master_url': 123, 'labels': [{'resource_url': {'bad': 'value'}}],
                             'videos': [{'uri': 123}, {'uri': 'https://youtu.be/abcdefghijk'}]})
        with patch.object(y.DISCOGS_SESSION, 'get', side_effect=upstream):
            result = y.select_clip('malformed-browser', {})
        self.assertEqual(result, {'video': 'abcdefghijk'})
        view = y.clip_view(y.store().entry('malformed-browser', result['video']))
        self.assertIsNone(view['label_url'])
        self.assertEqual(view['release_url'], 'https://www.discogs.com/release/502')


if __name__ == '__main__':
    unittest.main()
