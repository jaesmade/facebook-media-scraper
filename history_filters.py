"""Navigate Facebook's visible timeline and post date controls."""

import re
from calendar import month_name
from datetime import datetime
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

from playwright.sync_api import Error, TimeoutError


def _section_url(url, section):
    try:
        parts = urlsplit(url)
    except ValueError:
        return ""
    if parts.scheme != "https" or parts.netloc not in {"facebook.com", "www.facebook.com",
                                                       "m.facebook.com", "web.facebook.com"}:
        return ""
    path = parts.path.rstrip("/")
    if path == "/profile.php":
        query = parse_qs(parts.query)
        profile = query.get("id", [""])[0]
        if "sk" not in query and re.fullmatch(r"[0-9]+", profile):
            return urlunsplit(parts._replace(netloc="www.facebook.com",
                                            query=urlencode({"id": profile, "sk": section}), fragment=""))
        return ""
    reserved = {"watch", "photo", "photo.php", "video.php", "videos", "reel", "reels", "share", "search", "login"}
    if not (re.fullmatch(r"/[A-Za-z0-9._-]+", path) and path[1:].lower() not in reserved or
            re.fullmatch(r"/(?:pages|people)/[^/]+/[0-9]+", path)):
        return ""
    return urlunsplit(parts._replace(netloc="www.facebook.com", path=f"{path}/{section}/", query="", fragment=""))


def timeline_url(url):
    return _section_url(url, "timeline")


def _login_close_button(page):
    dialog = page.get_by_role("dialog").filter(has=page.locator('input[type="password"]'))
    return dialog.get_by_role("button", name="Close", exact=True).first


def close_login_dialog(page):
    """Use Facebook's visible close control on a dismissible sign-in prompt."""
    close = _login_close_button(page)
    if not close.is_visible():
        return False
    close.click(timeout=3000)
    return True


def creation_date(page, url):
    """Read the Page Transparency creation date using an English browser locale."""
    transparency = _section_url(url, "about_profile_transparency")
    if not transparency:
        return None
    try:
        response = page.goto(transparency, wait_until="domcontentloaded", timeout=30000)
        if response and response.status >= 400:
            return None
        close_login_dialog(page)
        label = page.get_by_text(re.compile(r"^(Date created|Creation date)$", re.I)).first
        text = label.evaluate(r"""label => {
            for (let i = 0; label && i < 7; i++, label = label.parentElement) {
                const text = label.innerText.trim();
                if (text.includes('\n') && text.length < 100) return text;
            }
            return '';
        }""", timeout=6000)
        match = re.search(r"\b[A-Za-z]+\s+\d{1,2},\s+\d{4}\b", text)
        return datetime.strptime(" ".join(match[0].split()), "%B %d, %Y").date() if match else None
    except (Error, ValueError):
        return None


def select_period(page, year, month=None, day=None):
    """Apply Facebook's visible Go to filter on a timeline already open in the page."""
    filters = page.get_by_role("button", name="Filters", exact=True).first
    filters.or_(_login_close_button(page)).first.wait_for(state="visible", timeout=8000)
    close_login_dialog(page)
    try:
        filters.click(timeout=3500)
    except TimeoutError:
        if not close_login_dialog(page):
            raise
        filters.click(timeout=3500)
    dialog = page.get_by_role("dialog").filter(has_text="Post filters")
    menus = dialog.locator('[aria-haspopup="listbox"]')
    menus.first.click(timeout=6000)
    page.get_by_role("option", name=str(year), exact=True).click(timeout=6000)
    if month is not None or menus.nth(1).inner_text(timeout=6000) != "Month":
        menus.nth(1).click(timeout=6000)
        page.get_by_role("option", name=month_name[month] if month else "Month", exact=True).click(timeout=6000)
    if month is not None and (day is not None or menus.nth(2).inner_text(timeout=6000) != "Day"):
        menus.nth(2).click(timeout=6000)
        page.get_by_role("option", name=str(day) if day else "Day", exact=True).click(timeout=6000)
    dialog.get_by_role("button", name="Done", exact=True).click(timeout=6000)
    dialog.wait_for(state="hidden", timeout=6000)
