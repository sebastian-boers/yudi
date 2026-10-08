"""Readable clip labels and compact additive history controls."""
import re
import time
import unittest
from unittest.mock import patch
import test_app as fixtures
from test_app import y

class ReadableHistoryTests(unittest.TestCase):
    setUp = fixtures.CacheTests.setUp
    tearDown = fixtures.CacheTests.tearDown

    def test_missing_video_title_uses_artist_release_not_identifier(self):
        vid = '7G_vUWStr12'
        data = {'title': 'Human Release', 'artists': [{'name': 'Human Artist'}], 'videos': [{'uri': 'https://youtu.be/'+vid}]}
        y.store().put(y.cache_key('https://api.discogs.com/releases/1'), data, 60)
        result = y.clip_view({'video': vid, 'release': 1})
        self.assertEqual(result['video_title'], 'Human Artist — Human Release')

    def test_selected_title_survives_cache_expiry_without_http(self):
        data = {'title': 'Release name', 'videos': [{'uri': 'https://youtu.be/abcdefghijk', 'title': 'Artist — Readable Track'}], '_yudi_expires': time.time()+60}
        search = {'results': [{'resource_url': 'https://api.discogs.com/releases/1'}]}
        with patch.object(y, 'discogs_api_request', side_effect=[search, data]):
            self.assertEqual(y.select_clip('readable-browser', {}), {'video': 'abcdefghijk'})
        with y.store().connection() as db: db.execute('UPDATE cache SET expires=0')
        with patch.object(y.DISCOGS_SESSION, 'get', side_effect=AssertionError('GET must not fetch titles')):
            result = y.clip_view(y.store().entry('readable-browser', 'abcdefghijk'))
        self.assertEqual(result['video_title'], 'Artist — Readable Track')
        self.assertEqual(result['title'], '')

    def test_legacy_unlabelled_entry_has_human_fallback(self):
        result = y.clip_view({'video': '7G_vUWStr12', 'release': 123})
        self.assertEqual(result['video_title'], 'Discogs release #123')

    def test_fresh_legacy_metadata_backfills_title_and_clear_removes_it(self):
        vid = 'abcdefghijk';db = y.store()
        db.reserve('legacy-browser', vid, 1, {}, {})
        db.put(y.cache_key('https://api.discogs.com/releases/1'), {'title': 'Album', 'videos': [{'uri': 'https://youtu.be/'+vid, 'title': 'Human song'}]}, 60)
        self.assertEqual(y.clip_view(db.entry('legacy-browser', vid))['video_title'], 'Human song')
        with db.connection() as con: con.execute('UPDATE cache SET expires=0')
        self.assertEqual(y.clip_view(db.entry('legacy-browser', vid))['video_title'], 'Human song')
        db.clear('legacy-browser', {})
        with db.connection() as con: self.assertEqual(con.execute('SELECT count(*) FROM clip_titles').fetchone()[0], 0)

class CompactHistoryTests(unittest.TestCase):
    setUp = fixtures.BrowserTests.setUp
    tearDown = fixtures.BrowserTests.tearDown
    post = fixtures.BrowserTests.post

    def test_history_is_collapsed_with_scoped_small_controls_and_readable_options(self):
        page = self.post().get_data(as_text=True)
        self.assertIn('<details class="recent-history">', page)
        self.assertNotRegex(page, r'<details[^>]+\bopen(?:\s|=|>)')
        self.assertIn('<summary>Recent clips', page)
        self.assertIn('class="history-controls"', page)
        self.assertIn('.history-controls button', page)
        self.assertNotIn('selected, not necessarily played)</label>', page)
        options = re.findall(r'<option[^>]*>(.*?)</option>', page)
        self.assertTrue(options)
        self.assertTrue(all('Clip ' in option for option in options))
