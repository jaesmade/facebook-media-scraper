import unittest
from contextlib import nullcontext
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from playwright.sync_api import sync_playwright

from scrape import download_media, extract, media_permalink


class MediaPermalinkTests(unittest.TestCase):
    def test_tracking_variants_share_a_canonical_url(self):
        first = media_permalink('https://www.facebook.com/photo/?fbid=123&ref=page')
        second = media_permalink('https://www.facebook.com/photo/?fbid=123&ref=feed')
        self.assertIsNotNone(first)
        self.assertEqual(first[0], 'image')
        self.assertEqual(first, second)

    def test_excludes_page_tabs_and_non_facebook_urls(self):
        for url in (
            'https://www.facebook.com/example/photos',
            'https://www.facebook.com/example/videos',
            'https://facebook.com.example.test/photo/?fbid=123',
        ):
            with self.subTest(url=url):
                self.assertIsNone(media_permalink(url))


class BrowserExtractionTests(unittest.TestCase):
    def test_extracts_media_and_excludes_page_chrome(self):
        photo = 'https://www.facebook.com/photo/?fbid=123'
        video = 'https://www.facebook.com/example/videos/456/'
        reel = 'https://www.facebook.com/reel/789/'
        image_url = 'https://scontent.example.fbcdn.net/gallery.jpg'
        feed_image_url = 'https://scontent.example.fbcdn.net/feed.jpg'
        video_url = 'https://video.example.fbcdn.net/gallery.mp4'

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page()
            page.route('https://**/*', lambda route: route.abort())
            page.set_content(f'''
                <header><a href="https://www.facebook.com/photo/?fbid=999">Page chrome</a></header>
                <main>
                    <a href="{photo}&amp;ref=gallery">
                        <img src="{image_url}" width="200" height="150" alt="Gallery image">
                    </a>
                    <a href="{photo}&amp;ref=duplicate">The same image</a>
                    <article role="article">
                        <a href="https://www.facebook.com/example/posts/123">
                            <time datetime="2026-03-14T07:00:00">March 14</time>
                        </a>
                        <img src="https://scontent.example.fbcdn.net/avatar.jpg" width="40" height="40">
                        <img src="{feed_image_url}" width="200" height="150" alt="Feed image">
                        <a href="{video}"><video src="{video_url}" preload="none"></video></a>
                    </article>
                    <article>
                        <a href="https://www.facebook.com/example/posts/456" title="February 28, 2026">Feb 28</a>
                        <a href="{reel}"><img src="https://scontent.example.fbcdn.net/thumbnail.jpg"
                                             width="200" height="150" alt="Reel thumbnail"></a>
                    </article>
                </main>
            ''', wait_until='domcontentloaded')
            rows = extract(page)
            browser.close()

        self.assertEqual(len(rows), 4)
        by_link = {row['facebook_url']: row for row in rows}
        image_row = by_link[media_permalink(photo)[1]]
        self.assertEqual(image_row['kind'], 'image')
        self.assertEqual(image_row['media_url'], image_url)
        self.assertEqual(image_row['alt'], 'Gallery image')
        self.assertEqual(image_row['post_date'], '')
        self.assertEqual(by_link['']['kind'], 'image')
        self.assertEqual(by_link['']['media_url'], feed_image_url)
        self.assertEqual(by_link['']['post_date'], '2026-03-14')
        video_row = by_link[media_permalink(video)[1]]
        self.assertEqual(video_row['kind'], 'video')
        self.assertEqual(video_row['media_url'], video_url)
        self.assertEqual(video_row['post_date'], '2026-03-14')
        reel_row = by_link[media_permalink(reel)[1]]
        self.assertEqual(reel_row['kind'], 'video')
        self.assertFalse(reel_row['media_url'])
        self.assertEqual(reel_row['post_date'], '2026-02-28')


class DownloadLayoutTests(unittest.TestCase):
    def test_direct_downloads_and_fallbacks_use_post_month(self):
        rows = [
            dict(kind='image', facebook_url='', media_url='direct.jpg', post_date='2026-03-01'),
            dict(kind='image', facebook_url='photo', media_url='', post_date='2026-03-31'),
            dict(kind='video', facebook_url='video', media_url='', post_date='2026-04-01'),
            dict(kind='image', facebook_url='', media_url='unknown.jpg', post_date=''),
        ]

        def save(folder, name):
            (folder / name).write_bytes(b'media')
            return str(Path(folder.name) / name), 'downloaded'

        def direct(row, folder, limit):
            if row['media_url']:
                return save(folder, row['media_url'])
            return '', 'no supported direct CDN URL'

        args = SimpleNamespace(max_file_mb=None, headed=False, wait_ms=0, session=None)
        with TemporaryDirectory() as temporary:
            output = Path(temporary)
            with (
                patch('scrape.photo_resolver', return_value=nullcontext(lambda url: 'photo.jpg')),
                patch('scrape.video_cookiefile', return_value=nullcontext()),
                patch('scrape.download', side_effect=direct),
                patch('scrape.download_video', side_effect=lambda url, folder, limit, **kw: save(folder, 'video.mp4')),
            ):
                download_media(rows, output / 'media', args)

            self.assertEqual([row['file'] for row in rows], [
                'media/2026-03/direct.jpg',
                'media/2026-03/photo.jpg',
                'media/2026-04/video.mp4',
                'media/undated/unknown.jpg',
            ])
            for row in rows:
                self.assertEqual(row['download_status'], 'downloaded')
                self.assertEqual((output / row['file']).read_bytes(), b'media')


if __name__ == '__main__':
    unittest.main()
