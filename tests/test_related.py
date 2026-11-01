import unittest
from unittest.mock import patch
from music.media_job import perform,JobFailure

def fake(extract):
    class Y:
        def __init__(self,*a,**k):pass
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def extract_info(self,url,download=False):return extract(url)
    return Y

class RelatedFallbackTests(unittest.TestCase):
    def request(self,**over):
        r=dict(mode='related',value='abcdefghijk',related_title='GIMS - Est-ce que tu m\'aimes',destination=None,max_seconds=7200,max_bytes=1000)
        r.update(over);return r

    def test_radio_mix_preferred(self):
        calls=[]
        def extract(url):
            calls.append(url)
            return dict(entries=[dict(id='mix1',title='Song A',duration=200)])
        with patch('yt_dlp.YoutubeDL',fake(extract)):
            out=perform(self.request())
        self.assertEqual(out['items'],[dict(id='mix1',title='Song A',duration=200,is_live=None,live_status=None)])
        self.assertEqual(len(calls),1)
        self.assertIn('list=RDabcdefghijk',calls[0])

    def test_artist_search_fallback(self):
        calls=[]
        def extract(url):
            calls.append(url)
            if 'list=RD' in url:raise RuntimeError('mix unavailable')
            return dict(entries=[dict(id='art1',title='J\'me tire',duration=262)])
        with patch('yt_dlp.YoutubeDL',fake(extract)):
            out=perform(self.request())
        self.assertEqual(len(calls),2)
        self.assertEqual(calls[1],'ytsearch15:GIMS')
        self.assertEqual(out['items'][0]['id'],'art1')

    def test_all_sources_fail(self):
        def extract(url):raise RuntimeError('nope')
        with patch('yt_dlp.YoutubeDL',fake(extract)):
            with self.assertRaises(JobFailure) as e:perform(self.request())
        self.assertEqual(e.exception.code,'EXTRACT_FAILED')

    def test_fallback_uses_full_title_without_dash(self):
        calls=[]
        def extract(url):
            calls.append(url)
            if 'list=RD' in url:raise RuntimeError('mix unavailable')
            return dict(entries=[dict(id='t1',title='Other',duration=100)])
        with patch('yt_dlp.YoutubeDL',fake(extract)):
            perform(self.request(related_title='SingleWordTitle'))
        self.assertEqual(calls[1],'ytsearch15:SingleWordTitle')

    def test_no_fallback_without_title(self):
        calls=[]
        def extract(url):
            calls.append(url)
            raise RuntimeError('nope')
        with patch('yt_dlp.YoutubeDL',fake(extract)):
            with self.assertRaises(JobFailure):perform(self.request(related_title=''))
        self.assertEqual(len(calls),1)

if __name__=='__main__':unittest.main()
