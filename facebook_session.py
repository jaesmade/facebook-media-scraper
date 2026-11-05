"""Optional login state for a dedicated Facebook scraper browser."""

from contextlib import contextmanager
import json
from pathlib import Path
from tempfile import NamedTemporaryFile, TemporaryDirectory
from time import time
from urllib.parse import urlsplit

from playwright.sync_api import Error, sync_playwright


def _facebook_cookies(cookies):
    now = time()
    for cookie in cookies:
        domain = cookie["domain"].lower().lstrip(".")
        expires = cookie["expires"]
        if (domain == "facebook.com" or domain.endswith(".facebook.com")) and (expires < 0 or expires > now):
            yield cookie


def _logged_in(cookies):
    names = {cookie["name"] for cookie in _facebook_cookies(cookies) if cookie["value"]}
    return {"c_user", "xs"} <= names


def is_logged_in(context):
    return _logged_in(context.cookies())


def _read_state(path):
    try:
        with Path(path).open(encoding="utf-8") as file:
            state = json.load(file)
        cookies = list(_facebook_cookies(state["cookies"]))
        valid = _logged_in(cookies)
        required = {"domain", "expires", "httpOnly", "name", "path", "sameSite", "secure", "value"}
        if any(not required.issubset(cookie) for cookie in cookies):
            raise ValueError("Invalid cookie record")
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as error:
        raise RuntimeError(f"Could not read Facebook session at {path}. Run the login command first.") from error
    if not valid:
        raise RuntimeError(f"Facebook session at {path} has expired. Run the login command again.")
    return {"cookies": cookies, "origins": []}


def browser_options(path):
    """Return kwargs for browser.new_context, using only the requested state file."""
    return {"storage_state": _read_state(path)} if path is not None else {}


def save_login_session(path, *, log=None):
    """Save a session from a new Chromium window after the user signs in."""
    path = Path(path)
    log = log or print
    log("Sign in to Facebook in the browser window. It will close when sign-in is complete.")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()
        page.goto("https://www.facebook.com/login/", wait_until="domcontentloaded", timeout=45000)
        for _ in range(300):
            try:
                page.wait_for_timeout(1000)
            except Error as error:
                raise RuntimeError("The login window closed before Facebook sign-in completed.") from error
            location = urlsplit(page.url)
            route = location.path.lstrip("/").split("/", 1)[0]
            if (location.hostname in {"www.facebook.com", "facebook.com"} and
                    route not in {"login", "login.php", "checkpoint", "two_factor"} and
                    is_logged_in(context)):
                state = context.storage_state()
                break
        else:
            raise RuntimeError("Facebook sign-in did not complete within five minutes.")
        browser.close()

    # Store Facebook cookies only. They are sufficient to restore the login in both tools.
    state = {"cookies": list(_facebook_cookies(state["cookies"])), "origins": []}
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as file:
            temporary = Path(file.name)
            json.dump(state, file)
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    log(f"Saved Facebook session to {path.resolve()}. Keep this file private.")


@contextmanager
def video_cookiefile(path):
    """Supply a temporary Netscape cookie jar for yt-dlp."""
    if path is None:
        yield None
        return
    cookies = _facebook_cookies(_read_state(path)["cookies"])
    with TemporaryDirectory(prefix="facebook-cookies-") as directory:
        jar = Path(directory) / "cookies.txt"
        with jar.open("w", encoding="utf-8", newline="\n") as file:
            file.write("# Netscape HTTP Cookie File\n")
            for cookie in cookies:
                domain = cookie["domain"]
                fields = (
                    ("#HttpOnly_" if cookie["httpOnly"] else "") + domain,
                    "TRUE" if domain.startswith(".") else "FALSE",
                    cookie["path"],
                    "TRUE" if cookie["secure"] else "FALSE",
                    str(int(cookie["expires"])) if cookie["expires"] >= 0 else "",
                    cookie["name"],
                    cookie["value"],
                )
                if not any(any(char in field for char in "\t\r\n") for field in fields):
                    file.write("\t".join(fields) + "\n")
        yield jar
