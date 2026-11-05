"""Find and download photos and videos from a Facebook page."""
import argparse
import csv
import hashlib
import json
import re
import sys
from datetime import date
from http.client import HTTPException
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from playwright.sync_api import Error, sync_playwright

from facebook_session import browser_options, save_login_session, video_cookiefile
from history_filters import creation_date, select_period, timeline_url
from photo_source import photo_resolver
from post_history import ARTICLE_LINKS, article_post
from video_download import download_video


def facebook_url(url):
    parts = urlsplit(url)
    return (
        parts.scheme == "https"
        and parts.hostname in {"facebook.com", "www.facebook.com", "m.facebook.com", "web.facebook.com"}
        and parts.netloc == parts.hostname
    )


def media_permalink(url):
    if not facebook_url(url):
        return None
    parts = urlsplit(url)
    path = parts.path.rstrip("/")
    query = parse_qs(parts.query)
    root = "https://www.facebook.com"
    if path in {"/photo", "/photo.php"}:
        kind, key, route = "image", query.get("fbid", [""])[0], "/photo/?fbid="
    elif path in {"/watch", "/video.php"}:
        kind, key, route = "video", query.get("v", [""])[0], "/watch/?v="
    elif (match := re.search(r"/(?:reel|reels)/([0-9]+)$", path)):
        kind, key, route = "video", match[1], "/reel/"
    elif (match := re.search(r"/videos/(?:[^/]+/)?([0-9]+)$", path)):
        kind, key, route = "video", match[1], "/watch/?v="
    elif "/photos/" in path and re.fullmatch(r"[0-9]+", path.rsplit("/", 1)[-1]):
        kind, key, route = "image", path.rsplit("/", 1)[-1], "/photo/?fbid="
    else:
        return None
    if re.fullmatch(r"[0-9]+", key):
        return kind, root + route + key + ("/" if route == "/reel/" else "")
    return None


SNAPSHOT = """() => {
    const root = document.querySelector('[role=main], main') || document.body;
    if (!root) return {media: [], posts: []};
    const selector = '[role=article], article';
    const articles = [...root.querySelectorAll(selector)]
        .filter(article => !article.parentElement?.closest(selector));
    const articleIndex = new Map(articles.map((article, index) => [article, index]));
    const readLinks = """ + ARTICLE_LINKS + """;
    const result = [];
    const href = el => el.closest('a[href]')?.href || '';
    for (const el of root.querySelectorAll('a[href], img, video')) {
        const tag = el.localName;
        let article = el.closest(selector);
        while (article && !articleIndex.has(article)) {
            article = article.parentElement?.closest(selector);
        }
        const post = articleIndex.get(article) ?? -1;
        if (tag === 'a') {
            result.push({tag, post, href: el.href});
        } else if (tag === 'img') {
            const rect = el.getBoundingClientRect();
            const large = Math.max(rect.width, el.naturalWidth) >= 180
                       && Math.max(rect.height, el.naturalHeight) >= 120;
            result.push({
                tag, post, href: href(el), src: el.currentSrc || el.src, alt: el.alt,
                feed: large && !!el.closest('[role=article], article')
            });
        } else {
            const urls = [el.currentSrc, el.src, ...Array.from(el.querySelectorAll('source[src]'), s => s.src)];
            result.push({tag, post, href: href(el), src: urls.find(u => /^https?:/.test(u)) || ''});
        }
    }
    return {media: result, posts: articles.map(readLinks)};
}"""


SCROLL = """distance => {
    const root = document.querySelector('[role=main], main') || document.body;
    let target = root;
    while (target && !(target.clientHeight && target.scrollHeight > target.clientHeight + 1
           && /^(auto|scroll)$/.test(getComputedStyle(target).overflowY))) {
        target = target.parentElement;
    }
    target ||= document.scrollingElement;
    if (!target) return [0, 0];
    if (distance) target.scrollBy(0, distance);
    return [target.scrollTop, target.scrollHeight];
}"""


def album_urls(page):
    links = page.evaluate("""() => {
        const root = document.querySelector('[role=main], main') || document.body;
        return [...root.querySelectorAll('a[href]')].map(link => link.href);
    }""")
    for link in links:
        try:
            if not facebook_url(link):
                continue
            parts = urlsplit(link)
        except ValueError:
            continue
        key = parse_qs(parts.query).get("set", [""])[0]
        if parts.path.rstrip("/") == "/media/set" and re.fullmatch(r"a\.[0-9]+", key):
            yield f"https://www.facebook.com/media/set/?set={key}&type=3"


def direct_url(url):
    if not url:
        return ""
    parts = urlsplit(url)
    return url if parts.scheme in {"https", "http"} and parts.hostname else ""


def extract(page, *, posts=None, known_post=("", "")):
    snapshot = page.evaluate(SNAPSHOT)
    articles = [article_post(links) for links in snapshot["posts"]]
    known_url, known_date = known_post
    if posts is not None:
        for link, published in articles:
            if link and (published or link not in posts):
                posts[link] = published
    rows = []
    for item in snapshot["media"]:
        tag = item["tag"]
        try:
            link = media_permalink(item["href"])
            source = direct_url(item.get("src"))
        except ValueError:
            continue
        if tag == "a" and link:
            kind, permalink = link
        elif tag == "img" and source:
            if link:
                kind, permalink = link
                if kind != "image":  # Video thumbnails aren't separate photos.
                    continue
            elif item["feed"]:
                kind, permalink = "image", ""
            else:
                continue
        elif tag == "video" and (source or (link and link[0] == "video")):
            kind, permalink = "video", link[1] if link and link[0] == "video" else ""
        else:
            continue
        index = item["post"]
        post_link, published = articles[index] if index >= 0 else ("", "")
        if not published and known_date and (
            post_link == known_url or permalink == known_url or
            (index == 0 and not post_link) or not articles
        ):
            published = known_date
        rows.append(dict(kind=kind, facebook_url=permalink, media_url=source, alt=item.get("alt", ""),
                         post_date=published))
    return list(merge(rows))


def media_key(row):
    link = row["facebook_url"]
    if link:
        # Watch and Reel routes can point to the same video ID.
        return row["kind"], re.search(r"[0-9]+/?$", link)[0].rstrip("/")
    return source_key(row)


def source_key(row):
    parts = urlsplit(row["media_url"])
    return row["kind"], parts.netloc, parts.path


def collect(found, rows):
    for row in rows:
        key = media_key(row)
        if key not in found:
            found[key] = row.copy()
        else:
            if not found[key].get("post_date") and row.get("post_date"):
                found[key]["post_date"] = row["post_date"]
            for field in ("media_url", "alt"):
                if row[field]:
                    found[key][field] = row[field]


def merge(rows):
    found = {}
    collect(found, rows)
    linked = set()
    for row in found.values():
        if row["facebook_url"] and row["media_url"]:
            key = source_key(row)
            linked.add(key)
            if not row.get("post_date") and key in found and found[key].get("post_date"):
                row["post_date"] = found[key]["post_date"]
    return (row for key, row in found.items() if row["facebook_url"] or key not in linked)


def scan_page(page, url, args, albums, *, history=None, single_snapshot=False, navigate=True,
              month_start=None, post_date=""):
    if navigate:
        response = page.goto(url, wait_until="domcontentloaded", timeout=45000)
        if response and response.status >= 400:
            raise RuntimeError(f"Facebook returned HTTP {response.status}.")
    rows = {}
    step = idle = 0
    previous_scroll = None
    parts = urlsplit(url)
    find_albums = (parts.path.rstrip("/").endswith("/photos_albums") or
                   parse_qs(parts.query).get("sk") == ["photos_albums"])
    while True:
        page.wait_for_timeout(args.wait_ms)
        if not facebook_url(page.url) or re.match(r"^/(login|checkpoint)(?:[/.]|$)", urlsplit(page.url).path):
            raise RuntimeError("Facebook redirected to a login, checkpoint, or another site.")
        if month_start and page.get_by_text("No posts available", exact=True).first.is_visible():
            history["feed_stop"] = "Facebook reported no posts for this period"
            break
        old_count = len(rows) + len(albums)
        if history is not None:
            old_count += len(history["posts"])
        collect(rows, extract(page, posts=history["posts"] if history is not None else None,
                              known_post=(url, post_date)))
        if find_albums:
            albums.update(dict.fromkeys(album_urls(page)))
        if single_snapshot:
            break
        if month_start and any(published and published < month_start for published in history["posts"].values()):
            history["feed_stop"] = "reached posts before this month"
            break

        position = page.evaluate(SCROLL, 0)
        new_count = len(rows) + len(albums) + (len(history["posts"]) if history is not None else 0)
        if position == previous_scroll and old_count == new_count:
            idle += 1
        else:
            idle = 0
        previous_scroll = position
        if args.scrolls is None:
            if idle >= (8 if history is not None and not month_start else 3):
                if history is not None:
                    login_prompt = page.get_by_role("dialog").filter(has=page.locator('input[type="password"]'))
                    history["feed_stop"] = ("Facebook showed a login prompt" if login_prompt.first.is_visible()
                                            else "no more posts loaded")
                break
        elif step >= args.scrolls:
            if history is not None:
                history["feed_stop"] = "scroll limit reached"
            break
        page.evaluate(SCROLL, 850)
        step += 1
    return rows.values()


def scan_history_month(page, timeline, year, month, args, period, *, first_day=1):
    month_key = f"{year:04d}-{month:02d}"
    month_start = f"{month_key}-{first_day:02d}"
    ending_day = None
    period["windows"] = []
    while True:
        window = {"posts": {}, "feed_stop": ""}
        page.goto(timeline, wait_until="domcontentloaded", timeout=45000)
        select_period(page, year, month, ending_day)
        yield from scan_page(page, timeline, args, {}, history=window, navigate=False, month_start=month_start)
        period["feed_stop"] = window["feed_stop"]
        period["windows"].append({"ending_day": ending_day, "stop": window["feed_stop"]})
        for link, published in window["posts"].items():
            if published or link not in period["posts"]:
                period["posts"][link] = published

        days = [int(published[-2:]) for published in window["posts"].values()
                if published.startswith(month_key + "-") and (ending_day is None or int(published[-2:]) <= ending_day)]
        if args.scrolls is not None or not days:
            break
        # Revisit the oldest date once, then jump earlier if that window cannot go further.
        next_day = min(days) if ending_day is None else min(min(days), ending_day - 1)
        if next_day < first_day:
            break
        ending_day = next_day


def print_progress(message):
    print(message, flush=True)


def scan_history_months(page, url, args, history, *, log=print_progress):
    timeline = timeline_url(url)
    if not timeline:
        history["timeline_status"] = "a Facebook page URL is required for timeline filters"
        return
    try:
        created = creation_date(page, url)
    except (Error, RuntimeError) as error:
        history["timeline_status"] = f"could not read Page Transparency ({error})"
        return
    if not created:
        history["timeline_status"] = "Facebook did not show the page creation date"
        return
    history["page_created"] = created.isoformat()
    today = date.today()
    for index in range(created.year * 12 + created.month - 1, today.year * 12 + today.month):
        year, month = divmod(index, 12)
        month += 1
        month_key = f"{year:04d}-{month:02d}"
        log(f"Scanning timeline for {month_key}")
        period = {"posts": {}, "feed_stop": ""}
        failed = False
        try:
            first_day = created.day if (year, month) == (created.year, created.month) else 1
            yield from scan_history_month(page, timeline, year, month, args, period, first_day=first_day)
        except (Error, RuntimeError) as error:
            period["feed_stop"] = str(error)
            failed = True
        dates = [published for published in period["posts"].values() if published.startswith(month_key + "-")]
        history["periods"][month_key] = {
            "stop": period["feed_stop"], "failed": failed, "dated_posts": len(dates),
            "oldest_post_date": min(dates, default=None), "newest_post_date": max(dates, default=None),
            "windows": period["windows"],
        }
        for link, published in period["posts"].items():
            if published or link not in history["posts"]:
                history["posts"][link] = published


def scrape_pages(args, urls, *, history=None, context_options=None, log=print_progress):
    rows = []
    failures = {}
    albums = {}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=not args.headed)
        context = browser.new_context(viewport={"width": 1440, "height": 1000},
                                      locale="en-US", **(context_options or {}))
        page = context.new_page()
        for index, url in enumerate(urls):
            feed_history = history if index == 0 else None
            if len(urls) > 1:
                log(f"Scanning {url}")
            try:
                rows.extend(scan_page(page, url, args, albums, history=feed_history))
            except (Error, RuntimeError) as error:
                failures[url] = str(error)
                if feed_history is not None:
                    feed_history["feed_stop"] = str(error)
        if history is not None:
            rows.extend(scan_history_months(page, urls[0], args, history, log=log))
            posts = sorted(history["posts"], key=lambda url: history["posts"][url] or "9999")
            if posts:
                log(f"Checking attachments on {len(posts)} individual posts")
            for index, url in enumerate(posts, 1):
                try:
                    rows.extend(scan_page(page, url, args, {}, single_snapshot=True, post_date=history["posts"][url]))
                except (Error, RuntimeError) as error:
                    failures[url] = str(error)
                if index % 50 == 0:
                    log(f"Checked {index} of {len(posts)} posts")
        for index, url in enumerate(albums, 1):
            log(f"Scanning photo album {index} of {len(albums)}")
            try:
                rows.extend(scan_page(page, url, args, {}))
            except (Error, RuntimeError) as error:
                failures[url] = str(error)
    return list(merge(rows)), failures


def scrape(args):
    rows, failures = scrape_pages(args, [args.url])
    if args.url in failures:
        raise RuntimeError(failures[args.url])
    return rows


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def download(row, folder, limit):
    url = row["media_url"]
    parts = urlsplit(url)
    host = parts.hostname or ""
    if (parts.scheme != "https" or parts.netloc != host or
            not any(host == base or host.endswith("." + base) for base in ("fbcdn.net", "fbsbx.com"))):
        return "", "no supported direct CDN URL"
    extensions = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp",
                  "image/gif": ".gif", "image/avif": ".avif", "video/mp4": ".mp4",
                  "video/webm": ".webm", "video/quicktime": ".mov"}
    opener = build_opener(NoRedirect)
    temp = None
    try:
        request = Request(url, headers={"Referer": "https://www.facebook.com/"})
        with opener.open(request, timeout=30) as response:
            content_type = response.headers.get_content_type()
            if response.status != 200 or response.headers.get("Content-Range"):
                return "", "response was not a complete file"
            if not content_type.startswith(row["kind"] + "/") or content_type not in extensions:
                return "", f"unsupported content type: {content_type}"
            name = row["kind"] + "-" + hashlib.sha256(url.encode()).hexdigest()[:20]
            target = folder / (name + extensions[content_type])
            total = 0
            with NamedTemporaryFile(dir=folder, suffix=".part", delete=False) as output:
                temp = Path(output.name)
                while chunk := response.read(1024 * 1024):
                    total += len(chunk)
                    if limit is not None and total > limit:
                        return "", "file exceeds size limit"
                    output.write(chunk)
            expected = response.headers.get("Content-Length")
            if not total or (expected and expected.isdecimal() and total != int(expected)):
                return "", "empty or incomplete file"
            temp.replace(target)
            return str(Path(folder.name) / target.name), "downloaded"
    except (HTTPError, HTTPException, URLError, OSError, ValueError) as error:
        return "", f"download failed ({getattr(error, 'code', type(error).__name__)})"
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)


def page_urls(url):
    parts = urlsplit(url)
    path = parts.path.rstrip("/")
    tabs = ("photos", "photos_by", "photos_albums", "videos", "reels")
    if path == "/profile.php":
        query = parse_qs(parts.query)
        if "id" not in query or "sk" in query:
            return [url]
        return [url, *(urlunsplit(parts._replace(query=urlencode({"id": query["id"][0], "sk": tab}), fragment=""))
                       for tab in (*tabs[:-1], "reels_tab"))]
    if path.count("/") != 1 or path in {"/watch", "/photo", "/photo.php", "/video.php", "/reels", "/share"}:
        return [url]
    return [url, *(urlunsplit(parts._replace(path=f"{path}/{tab}/", query="", fragment="")) for tab in tabs)]


def download_media(rows, folder, args, *, context_options=None, log=print_progress, relative_to=None):
    folder.mkdir(exist_ok=True)
    relative_to = folder.parent if relative_to is None else relative_to
    limit = args.max_file_mb * 1024**2 if args.max_file_mb is not None else None
    log(f"Downloading {len(rows)} media entries to {folder.resolve()}")
    with (photo_resolver(args.headed, args.wait_ms, context_options=context_options) as resolve_photo,
          video_cookiefile(args.session) as cookies):
        for index, row in enumerate(rows, 1):
            month = row.get("post_date", "")[:7] or "undated"
            destination = folder / month
            destination.mkdir(exist_ok=True)
            file, status = download(row, destination, limit)
            if not file and row["facebook_url"] and status != "file exceeds size limit":
                if row["kind"] == "video":
                    file, status = download_video(row["facebook_url"], destination, limit, cookiefile=cookies)
                elif source := resolve_photo(row["facebook_url"]):
                    row["media_url"] = source
                    file, status = download(row, destination, limit)
            row["file"] = (destination.relative_to(relative_to) / Path(file).name).as_posix() if file else ""
            row["download_status"] = status
            if index % 25 == 0:
                log(f"Processed {index} of {len(rows)} media entries")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url", nargs="?", help="HTTPS Facebook page or media tab URL")
    parser.add_argument("--history", action="store_true", help="scan dated posts and check their media attachments")
    parser.add_argument("--login", action="store_true", help="open Facebook to save a local session, then exit")
    parser.add_argument("--session", nargs="?", type=Path, const=Path(__file__).resolve().with_name(".facebook-session.json"),
                        help="reuse a saved Facebook session, optionally from a custom path")
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parent / "output",
                        help="destination folder (default: output next to scrape.py)")
    parser.add_argument("--scrolls", type=int, help="maximum scrolls per tab (default: scroll until no more media loads)")
    parser.add_argument("--wait-ms", type=int, default=1200, help="milliseconds between snapshots")
    parser.add_argument("--headed", action="store_true", help="show the Chromium window")
    parser.add_argument("--download", action="store_true", help="save images and videos in yyyy-mm folders by post date")
    parser.add_argument("--max-file-mb", type=int, help="optional download limit per file in MiB")
    args = parser.parse_args()
    if args.login:
        try:
            save_login_session(args.session or Path(__file__).resolve().with_name(".facebook-session.json"))
        except (Error, OSError, RuntimeError) as error:
            print(f"Facebook login failed: {error}", file=sys.stderr)
            return 1
        return 0
    try:
        valid_url = args.url and facebook_url(args.url) and urlsplit(args.url).path.strip("/")
    except ValueError:
        valid_url = False
    if not valid_url:
        parser.error("supply an HTTPS Facebook page URL, for example https://www.facebook.com/PAGE_NAME")
    if ((args.scrolls is not None and args.scrolls < 0) or args.wait_ms < 0 or
            (args.max_file_mb is not None and args.max_file_mb < 1)):
        parser.error("scrolls and wait-ms must be nonnegative; max-file-mb must be positive")
    try:
        urls = page_urls(args.url)
        history = {"url": args.url, "posts": {}, "feed_stop": "", "periods": {}} if args.history else None
        context_options = browser_options(args.session)
        rows, failures = scrape_pages(args, urls, history=history, context_options=context_options)
        for url, error in failures.items():
            print(f"Could not scan {url}: {error}", file=sys.stderr)
        args.output.mkdir(parents=True, exist_ok=True)
        if history is not None:
            dates = sorted(date for date in history["posts"].values() if date)
            history.update(oldest_post_date=dates[0] if dates else None,
                           newest_post_date=dates[-1] if dates else None, failures=failures)
            with (args.output / "history.json").open("w", encoding="utf-8") as file:
                json.dump(history, file, indent=2, ensure_ascii=False)
                file.write("\n")
            if dates:
                print(f"Visible post dates: {dates[0]} through {dates[-1]} ({len(dates)} dated posts).", flush=True)
            else:
                print("Facebook exposed no readable post dates.", flush=True)
            if issue := history.get("timeline_status"):
                print(f"Timeline coverage: {issue}. See history.json.", file=sys.stderr)
            incomplete = sum(period["failed"] or any("login prompt" in window["stop"] or
                             window["stop"] == "scroll limit reached" for window in period["windows"])
                             for period in history["periods"].values())
            if incomplete:
                print(f"{incomplete} month(s) had scan limits or errors. See history.json.", file=sys.stderr)
        if not rows and all(url in failures for url in urls):
            return 1
        if args.download:
            download_media(rows, args.output / "media", args, context_options=context_options)
        fields = ["kind", "facebook_url", "media_url", "alt", "post_date", "file", "download_status"]
        with (args.output / "media.csv").open("w", newline="", encoding="utf-8-sig") as file:
            writer = csv.DictWriter(file, fieldnames=fields)
            writer.writeheader()
            for row in rows:
                # Prevent formula interpretation when opening scraped text in a spreadsheet.
                writer.writerow({key: "'" + value if value.startswith(("=", "+", "-", "@", "\t", "\r", "\n"))
                                 else value for key, value in row.items()})
        with (args.output / "media.json").open("w", encoding="utf-8") as file:
            json.dump(rows, file, indent=2, ensure_ascii=False)
            file.write("\n")
    except (Error, OSError, RuntimeError) as error:
        print(f"Scrape failed: {error}", file=sys.stderr)
        return 1
    images = sum(row["kind"] == "image" for row in rows)
    print(f"Found {images} image(s) and {len(rows) - images} video(s). Exports: {args.output.resolve()}")
    if args.download:
        saved_images = sum(row["kind"] == "image" and bool(row["file"]) for row in rows)
        saved_videos = sum(row["kind"] == "video" and bool(row["file"]) for row in rows)
        print(f"Saved {saved_images} of {images} image(s) and {saved_videos} of {len(rows) - images} video(s). "
              "See download_status in the exports for details.")
    if not rows:
        print("No media was exposed. Try the page's Photos or Videos tab or use --headed to inspect the page.",
              file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
