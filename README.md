# Facebook media scraper

This repository contains a Python media scraper with a Windows desktop wrapper. It downloads photos and videos visible on a Facebook page using Playwright Chromium and yt-dlp. Use a page you have permission to scrape.

`scrape.py` provides the command-line script. `app.py` adds a Tkinter desktop interface for signing in, scanning, filtering results, and choosing downloads, with `app_backend.py` connecting the interface to the same scraper functions. You can run either interface from source.

`build-windows.ps1` packages the desktop wrapper, Python runtime, dependencies, and Chromium browser into a Windows app folder with `FacebookMediaDownloader.exe`, so users of the built app do not need to install Python separately.

From this folder in PowerShell, set up the environment:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m playwright install chromium
```

After building, double-click `release\FacebookMediaDownloader\FacebookMediaDownloader.exe` (or `launch-app.cmd`) to open the standalone app. To run from source, use:

```powershell
.\.venv\Scripts\python.exe -B app.py
```

## Build a standalone Windows app

On Windows, run `build-windows.ps1` from PowerShell:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\build-windows.ps1
```

The build creates `release\FacebookMediaDownloader\FacebookMediaDownloader.exe`. Copy the entire
`FacebookMediaDownloader` folder when moving the app to another PC; it includes the Python runtime,
dependencies, and the matching Playwright Chromium browser. The release folder is around 900 MB on disk.
The built app runs without a separate Python install and saves an optional Facebook sign-in session under
the current Windows user's Local AppData folder.

1. To access older photos and videos, click **Sign in (optional)** and log in in the browser window that opens. Then paste a Facebook page, post, or media link and click **Scan media**. Enable **Scan older posts** to check the page's history.
2. Choose a **From date** and **Through date** from the available post dates, or enter them as `YYYY-MM-DD`. Set both to the same day for an exact match. Use **Media type** to show only videos or photos. With a date filter active, items without a readable date appear only if you enable **Include undated in range**.
3. Click **Browse** to pick an existing download folder. Click **Download matches** to save the filtered results or select rows and click **Download selected**. Files go into `yyyy-mm` subfolders under your chosen directory, with `undated` for items without a post date.

In the desktop app, click **Sign in (optional)** to open a dedicated browser and save a session for later scans. The standalone Windows build stores it under `%LOCALAPPDATA%\FacebookMediaDownloader`; when running from source, it stores `.facebook-session.json` beside `app.py`.

Replace `PAGE_NAME` with the page's actual name or paste its full URL:

```powershell
.\.venv\Scripts\python.exe scrape.py "https://www.facebook.com/PAGE_NAME" --download
```

For a page URL, the scraper scans the main feed, Photos, public photo albums, Videos, and Reels. It scrolls until Facebook stops loading more media and combines the results. Supply a tab URL directly to scan only that section. Use `--scrolls 20` to cap each tab or album at 20 scrolls.

```powershell
.\.venv\Scripts\python.exe scrape.py "https://www.facebook.com/PAGE_NAME/videos" --output videos --scrolls 20 --download
```

- `media.csv` and `media.json` contain the media type, Facebook permalink, direct media URL, image alt text, and post date when available. For downloaded files, `file` gives the path relative to the output folder.
- `--download` groups photos and videos by post month in `media/yyyy-mm/` (for example, `media/2026-11/`). Files without a readable post date go in `media/undated/`. When a direct download fails, the scraper opens the photo's permalink or passes the video's permalink to yt-dlp. Install FFmpeg and FFprobe on your `PATH` to allow yt-dlp to combine separate video and audio streams. Without them, it uses a format that already contains both.
- Use `--headed` to see the browser, or increase `--wait-ms` for slower page loads. The default destination is `output/` next to `scrape.py`, with downloads in `output/media/`.

Facebook can limit how much history a logged-out browser sees. Sign in before scanning for more complete access to older photos, videos, and posts. Signing in does not guarantee every file is available; the scraper downloads only the media Facebook makes available to that session. Check `download_status` in either export for entries it found but couldn't save. Direct CDN links can expire.

To check older posts and their attachments, run with `--history --download`. The scraper reads the Page Transparency creation date and uses Facebook's timeline filters to visit each month through the current month. It tries earlier day cutoffs within each month to reach posts beyond the first scroll limit. This can take time on older pages. `output/history.json` records the dates and links found, where each window stopped, and any scan errors. Months Facebook leaves inaccessible can still have gaps.

For more complete access to older media, save a sign-in session in a dedicated browser window and run the history scan with it:

```powershell
.\.venv\Scripts\python.exe scrape.py --login
.\.venv\Scripts\python.exe scrape.py "https://www.facebook.com/PAGE_NAME" --history --session --download
```

The session is saved to `.facebook-session.json` beside the script. Keep it private and run `--login` again if it expires. The scan can only see posts the account has access to.

Run the offline Chromium tests with:

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -v
```
