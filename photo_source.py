"""Find the displayed image on a public Facebook photo permalink."""
from contextlib import ExitStack, contextmanager
import re
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import Error, sync_playwright


PHOTO_SOURCE = """() => {
    const images = document.querySelectorAll('[data-pagelet="MediaViewerPhoto"] img');
    let best = '';
    let bestArea = 0;
    for (const image of images) {
        const {width, height, top, bottom} = image.getBoundingClientRect();
        if (width <= 0 || height <= 0 || bottom <= 0 || top >= innerHeight) continue;
        if (!image.complete || !image.naturalWidth || !image.currentSrc) continue;
        const source = new URL(image.currentSrc);
        if (source.protocol !== 'https:' || source.username || source.password || source.port ||
            !['fbcdn.net', 'fbsbx.com'].some(host => source.hostname === host ||
                source.hostname.endsWith('.' + host))) continue;
        const area = width * height;
        if (area > bestArea) {
            bestArea = area;
            best = source.href;
        }
    }
    return best;
}"""


def photo_id(url):
    try:
        parts = urlsplit(url)
        if (parts.scheme != "https" or parts.netloc not in
                {"facebook.com", "www.facebook.com", "m.facebook.com", "web.facebook.com"}):
            return ""
    except ValueError:
        return ""
    if parts.path.rstrip("/") not in {"/photo", "/photo.php"}:
        return ""
    value = parse_qs(parts.query).get("fbid", [""])[0]
    return value if re.fullmatch(r"[0-9]+", value) else ""


def resolve_photo(page, permalink, wait_ms=1200):
    expected = photo_id(permalink)
    if not expected:
        return ""
    try:
        response = page.goto(permalink, wait_until="domcontentloaded", timeout=20000)
        if not response or response.status >= 400 or photo_id(page.url) != expected:
            return ""
        source = page.wait_for_function(PHOTO_SOURCE, timeout=max(5000, wait_ms))
        try:
            return source.json_value() if photo_id(page.url) == expected else ""
        finally:
            source.dispose()
    except Error:
        return ""


@contextmanager
def photo_resolver(headed=False, wait_ms=1200, *, context_options=None):
    """Reuse one page for photo fallbacks, opening it only when needed."""
    with ExitStack() as stack:
        page = None

        def resolve(permalink):
            nonlocal page
            if page is None:
                playwright = stack.enter_context(sync_playwright())
                browser = playwright.chromium.launch(headless=not headed)
                stack.callback(browser.close)
                page = browser.new_page(viewport={"width": 1440, "height": 1000}, **(context_options or {}))
            return resolve_photo(page, permalink, wait_ms)

        yield resolve
