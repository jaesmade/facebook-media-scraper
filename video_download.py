"""Resolve and download a video from its public Facebook permalink."""

import hashlib
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory


class _DownloadLog:
    def __init__(self):
        self.size_limited = False

    def debug(self, message):
        if "larger than max-filesize" in message:
            self.size_limited = True

    def warning(self, message):
        pass

    def error(self, message):
        pass


def download_video(url, folder, limit, *, cookiefile=None):
    try:
        from yt_dlp import YoutubeDL
        from yt_dlp.utils import DownloadError
    except ImportError:
        return "", "video download requires yt-dlp (install requirements.txt)"

    stem = "video-" + hashlib.sha256(url.encode()).hexdigest()[:20]
    extensions = {".mp4", ".mkv", ".webm", ".mov", ".flv"}
    ffmpeg = shutil.which("ffmpeg")
    log = _DownloadLog()
    try:
        for existing in sorted(folder.glob(stem + ".*")):
            if existing.suffix in extensions and existing.is_file():
                size = existing.stat().st_size
                if size and (limit is None or size <= limit):
                    return str(Path(folder.name) / existing.name), "downloaded"

        with TemporaryDirectory(prefix=".video-", dir=folder) as temporary:
            options = {
                "outtmpl": "video.%(ext)s",
                "paths": {"home": temporary},
                "format": "bv+ba/b" if ffmpeg else "b",
                "max_filesize": limit,
                "noplaylist": True,
                "socket_timeout": 20,
                "retries": 1,
                "fragment_retries": 1,
                "skip_unavailable_fragments": False,
                "logger": log,
                "quiet": True,
            }
            if ffmpeg:
                options["ffmpeg_location"] = ffmpeg
            if cookiefile is not None:
                options["cookiefile"] = str(cookiefile)
            with YoutubeDL(options) as downloader:
                downloader.extract_info(url, download=True)
            files = [file for file in Path(temporary).glob("video.*") if file.suffix in extensions]
            if log.size_limited:
                return "", "file exceeds size limit"
            if len(files) != 1 or not files[0].stat().st_size:
                return "", "video downloader produced no complete file"
            if limit is not None and files[0].stat().st_size > limit:
                return "", "file exceeds size limit"
            target = folder / (stem + files[0].suffix)
            files[0].replace(target)
            return str(Path(folder.name) / target.name), "downloaded"
    except DownloadError as error:
        if log.size_limited:
            return "", "file exceeds size limit"
        message = str(error).lower()
        if any(word in message for word in ("login", "log in", "sign in")):
            return "", "Facebook requires login for this video"
        if "requested format is not available" in message and not ffmpeg:
            return "", "no combined video format available (install FFmpeg)"
        return "", "video download failed (Facebook video unavailable)"
    except OSError as error:
        return "", f"video download failed ({type(error).__name__})"
