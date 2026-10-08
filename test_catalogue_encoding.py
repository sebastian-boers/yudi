"""Discogs content-encoding must not discard catalogue titles or label links."""
import unittest
from unittest.mock import patch
import test_app as fixtures
from test_app import y, Response

class CatalogueEncodingTests(unittest.TestCase):
    setUp = fixtures.CacheTests.setUp
    tearDown = fixtures.CacheTests.tearDown

    def test_accept_encoding_variation_keeps_decoded_json_metadata(self):
        url='https://api.discogs.com/releases/1'
        data={'title':'Album title','videos':[{'uri':'https://youtu.be/abcdefghijk','title':'Artist - SongName'}],'labels':[{'name':'Human Label','resource_url':'https://api.discogs.com/labels/7'}]}
        with patch.object(y.DISCOGS_SESSION,'get',return_value=Response(data,headers={'Vary':'Accept-Encoding'})) as http:
            first=y.discogs_api_request(url)
            second=y.discogs_api_request(url)
        self.assertEqual(http.call_count,1)
        self.assertEqual(first,second)
        view=y.clip_view({'video':'abcdefghijk','release':1})
        self.assertEqual(view['video_title'],'Artist - SongName')
        self.assertEqual(view['title'],'Album title')
        self.assertEqual(view['label_url'],'https://www.discogs.com/label/7')
        self.assertEqual(view['label_name'],'Human Label')

    def test_label_reference_survives_cache_expiry(self):
        vid='abcdefghijk'; db=y.store()
        db.reserve('label-browser',vid,1,{}, {})
        db.put(y.cache_key('https://api.discogs.com/releases/1'), {'title':'Album','videos':[{'uri':'https://youtu.be/'+vid,'title':'Artist - Song'}], 'labels':[{'name':'The Label','resource_url':'https://api.discogs.com/labels/7'}]},60)
        self.assertEqual(y.clip_view(db.entry('label-browser',vid))['label_url'],'https://www.discogs.com/label/7')
        with db.connection() as con: con.execute('UPDATE cache SET expires=0')
        view=y.clip_view(db.entry('label-browser',vid))
        self.assertEqual(view['video_title'],'Artist - Song')
        self.assertEqual(view['label_url'],'https://www.discogs.com/label/7')
        self.assertTrue(view['label_name'])
        db.clear('label-browser',{})
        with db.connection() as con: self.assertEqual(con.execute('SELECT count(*) FROM clip_labels').fetchone()[0],0)

    def test_unknown_vary_remains_uncacheable(self):
        for vary in ['*','Cookie','Accept-Encoding, Cookie']:
            with self.subTest(vary=vary), patch.object(y.DISCOGS_SESSION,'get',return_value=Response({'videos':[]},headers={'Vary':vary})) as http:
                y.discogs_api_request('https://api.discogs.com/releases/2')
                y.discogs_api_request('https://api.discogs.com/releases/2')
                self.assertEqual(http.call_count,2)

class CaptionTests(unittest.TestCase):
    setUp=fixtures.BrowserTests.setUp
    tearDown=fixtures.BrowserTests.tearDown
    post=fixtures.BrowserTests.post
    def test_original_album_and_label_links_remain_with_readable_title(self):
        page=self.post().get_data(as_text=True)
        self.assertIn('>Album</a>',page)
        self.assertIn('>Label</a>',page)
        self.assertIn('https://www.discogs.com/label/7',page)
        self.assertRegex(page,r'Clip [AB] — Album title')
        self.assertNotIn('Discogs release #',page)
