"""Read Facebook post links and calendar dates from the rendered feed."""

import re
from datetime import date
from urllib.parse import parse_qs, urlencode, urlsplit


FACEBOOK_HOSTS = {"facebook.com", "www.facebook.com", "m.facebook.com", "web.facebook.com"}
ROOT = "https://www.facebook.com"
MONTHS = {
    name.casefold(): index
    for names in (
        "January February March April May June July August September October November December",
        "Enero Pebrero Marso Abril Mayo Hunyo Hulyo Agosto Setyembre Oktubre Nobyembre Disyembre",
    )
    for index, name in enumerate(names.split(), 1)
}
DATE_TEXT = re.compile(r"\b([^\W\d_]+)\s+(\d{1,2}),?\s+(\d{4})\b")
POST_PATH = re.compile(r"/(?:[^/]+/)?posts/([A-Za-z0-9]+)$")
VIDEO_PATH = re.compile(r"/(?:reels?|videos)/(?:[^/]+/)?[0-9]+$")


ARTICLE_LINKS = """article => {
    const links = [];
    for (const link of article.querySelectorAll('a[href]')) {
        if (link.closest('[role=article], article') !== article) continue;
        const descriptions = (link.getAttribute('aria-describedby') || '').split(/\\s+/);
        const times = [...link.querySelectorAll('time[datetime]')];
        links.push({
            href: link.href,
            dated: !!link.getAttribute('aria-describedby') || times.length > 0,
            labels: [
                ...times.map(time => time.getAttribute('datetime')),
                ...descriptions.map(id => document.getElementById(id)?.textContent),
                link.getAttribute('title'),
                link.getAttribute('aria-label'),
            ],
        });
    }
    return links;
}"""


def post_url(url, timestamp_link=False):
    try:
        parts = urlsplit(url)
    except ValueError:
        return ""
    if parts.scheme != "https" or parts.netloc not in FACEBOOK_HOSTS:
        return ""
    path = parts.path.rstrip("/")
    query = parse_qs(parts.query)
    if POST_PATH.fullmatch(path):
        return ROOT + path
    if path in {"/permalink.php", "/story.php"}:
        post = query.get("story_fbid", [""])[0]
        owner = query.get("id", [""])[0]
        if re.fullmatch(r"[A-Za-z0-9]+", post) and re.fullmatch(r"[0-9]+", owner):
            return ROOT + "/permalink.php?" + urlencode({"story_fbid": post, "id": owner})
    if timestamp_link:
        for routes, parameter, canonical in (
            ({"/photo", "/photo.php"}, "fbid", "/photo/"),
            ({"/watch", "/video.php"}, "v", "/watch/"),
        ):
            value = query.get(parameter, [""])[0]
            if path in routes and re.fullmatch(r"[0-9]+", value):
                return ROOT + canonical + "?" + urlencode({parameter: value})
        if VIDEO_PATH.search(path):
            return ROOT + path
    return ""


def exact_date(labels):
    for label in labels:
        if not label:
            continue
        iso = re.match(r"^\s*(\d{4}-\d{2}-\d{2})(?:[T\s]|$)", label)
        if iso:
            try:
                return date.fromisoformat(iso[1]).isoformat()
            except ValueError:
                continue
        match = DATE_TEXT.search(label)
        if match and (month := MONTHS.get(match[1].casefold())):
            try:
                return date(int(match[3]), month, int(match[2])).isoformat()
            except ValueError:
                continue
    return ""


def article_post(links):
    """Return a post permalink and its ISO date when available."""
    fallback = ""
    for link in links:
        url = post_url(link["href"], timestamp_link=link["dated"])
        if not url:
            continue
        if posted := exact_date(link["labels"]):
            return url, posted
        fallback = fallback or url
    return fallback, ""
