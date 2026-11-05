"""Scan and save Facebook media from the desktop app."""

from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

from facebook_session import browser_options, save_login_session
from scrape import download_media, facebook_url, page_urls, print_progress, scrape_pages


def scan(url, *, history=False, session=None, log=None):
    """Return the visible media rows and any per-URL scan failures."""
    url = url.strip()
    if "://" not in url:
        url = "https://" + url
    try:
        valid_url = facebook_url(url) and urlsplit(url).path.strip("/")
    except ValueError:
        valid_url = False
    if not valid_url:
        raise ValueError("Enter a Facebook page, post, or media link, such as https://www.facebook.com/PAGE_NAME.")

    log = log or print_progress
    log("Starting Facebook scan. This may take a few minutes.")
    args = SimpleNamespace(headed=False, wait_ms=1200, scrolls=None)
    report = {"url": url, "posts": {}, "feed_stop": "", "periods": {}} if history else None
    rows, failures = scrape_pages(args, page_urls(url), history=report,
                                  context_options=browser_options(session), log=log)
    if report is not None:
        if issue := report.get("timeline_status"):
            log(f"Timeline coverage: {issue}.")
        incomplete = sum(period["failed"] for period in report["periods"].values())
        if incomplete:
            log(f"The timeline scan could not complete {incomplete} month(s).")
    return rows, failures


def save(rows, destination, *, session=None, log=None):
    """Download a copy of the selected rows and return paths relative to the chosen folder."""
    results = [row.copy() for row in rows]
    if not results:
        return results
    folder = Path(destination).resolve()
    if not folder.is_dir():
        raise ValueError("Choose an existing download folder.")
    args = SimpleNamespace(headed=False, wait_ms=1200, max_file_mb=None, session=session)
    download_media(results, folder, args, context_options=browser_options(session),
                   log=log or print_progress, relative_to=folder)
    return results


def login(session_path, *, session=None, log=None):
    """Open a dedicated browser and save the user's Facebook sign-in state."""
    save_login_session(session_path, log=log)
