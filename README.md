# Girl Life HTML Builder

繁體中文說明：[README.zh-TW.md](README.zh-TW.md)

Build and run a browser-playable package of the original English Girl Life QSP game using the official QSPFoundation qSpider player.

This repository contains the builder only. It does not include the game, images, sounds, or generated game package. Provide the game and assets you are authorized to use.

## Requirements

- macOS or Windows 10/11
- An English `.qsp` game file
- An `images/` directory or a recognized image ZIP archive
- A `sound/` directory or, optionally, a recognized sound ZIP archive
- Internet access for the first build, to download the qSpider v1.3.1 player

The launchers check for Python 3.10 or newer. If none is available, they ask before downloading the official installer from [python.org](https://www.python.org/downloads/). Answer `y` to continue or press Enter to cancel. The downloaded installer is signature-checked before it is opened or run. macOS then opens its installer UI; Windows installs Python for the current user. No administrator access is requested by the Windows install command.

No third-party Python packages are required.

## Folder layout

Put the builder files beside the English `.qsp` file and its assets:

```text
GirlLife/
├── Build-GirlLife-HTML.bat
├── Build-GirlLife-HTML.ps1
├── Build-GirlLife-HTML.sh
├── build_girllife_html.py
├── girllife_server.py       # cloud-save service (hosted by the project owner)
├── Girl Life 0.9.9.2.qsp
├── images/
└── sound/                 # optional; audio is absent if not provided
```

The builder selects an English `.qsp` file (its filename must not contain `_cn`). If there is more than one, it asks you to choose. It can also use a `sound/` folder in the parent directory when that is the only adjacent sound folder it finds.

## Build and run

### macOS

Run from Terminal:

```sh
./Build-GirlLife-HTML.sh
```

If macOS blocks the script because it is not marked executable, run:

```sh
chmod +x Build-GirlLife-HTML.sh
./Build-GirlLife-HTML.sh
```

### Windows

Double-click `Build-GirlLife-HTML.bat`. It uses the included PowerShell launcher to check, request consent for, and if needed install Python before running the builder. PowerShell is included with supported Windows versions.

The builder creates a sibling folder named `<English game filename>_HTML`, reports missing media, starts a local web server on a dynamically assigned port, prints the local URL, and tries to open it in your browser. A progress bar reports the game file and media bytes as they download, then shows elapsed seconds while the loaded game is being prepared. The builder creates a reusable gzip copy of the QSP; supporting servers send it automatically, reducing the current 91 MB game file to about 11 MB over the network. The original `.qsp` remains intact. The browser does not preload the entire media library: it warms images and video referenced by the current scene and currently displayed action buttons, with a small bounded queue; videos are primed for metadata, and the game still loads other assets when requested. On narrow screens (up to 900 CSS px), both Classic and Aero layouts fit the viewport, keep text and actions scrollable, and use touch-sized controls; images and videos scale to the available space. In mobile Classic and Aero, the main window uses 70% of the viewport and the action window the remaining 30%; the main text viewport is about 60% after reserving room for the upper-right Items control. Opening Items replaces the main-window area, while opening Stats replaces the action-window area. The panels have toggle buttons at the upper-right and lower-right edges, and only one panel opens at a time. Creation tables preserve horizontal cell layout and can scroll horizontally on narrow screens rather than squeezing words into vertical-looking columns. Popup media is centered over a dark backdrop; tap outside the actual image/video to close only that popup. These changes have been browser-tested at phone and tablet viewport sizes, but not on a physical iPhone/Safari device. Keep the launcher window open while playing. Press `Ctrl+C` in that window to stop the server.

### Current mobile validation status (2026-10-30)

The Classic split layout and Stats toggle were tested in the browser at 320×568, 390×844, 430×932, and 768×1024. The Items panel's layout was checked with a test DOM fixture because the tested game-start state did not contain Items; populated in-game Items content is not yet verified. The cloud-16 layout is deployed at `https://girllife.mewcat.me/` and the builder/docs are on GitHub `main` (commit `43e16d4`). Physical iPhone/Safari and Windows builds remain untested.

The cloud-20 mobile follow-up enlarges the main pane to 70% in both Classic and Aero, reserving approximately 60% of the viewport for its scrollable game content after the Items control inset; the action pane occupies 30%. This follow-up specifically checks layouts after entering story gameplay, not just the title/create screens. The previous cloud-19 split is deployed at `https://girllife.mewcat.me/`; this cloud-20 follow-up is being verified before deployment. Physical iPhone/Safari and Windows builds are not yet verified.

If a valid output folder already exists, it is preserved. The builder audits and serves that existing package rather than overwriting it.

## Cloud saves

The generated player stores numbered slots, named saves, and quick saves on the GirlLife cloud service at `girllife.mewcat.me`. Cloud saves require an account, so opening the **Saves** panel or the title-screen **LOAD** button asks for one first. On a device that has never been signed in, the dialog leads with **Create account** and the button is offered before **Sign in**, so a new player is pointed at registration; **Sign in** stays available for anyone who already has an account, and picking **Create account** with a name that already exists switches the dialog back to **Sign in** and keeps what you typed. Creating an account needs a name and a single password (no confirmation field). Sign in with that account on other devices to access the same saves. The signed-in session is remembered on the device with a `localStorage` entry, so reloading the page or reopening the browser keeps you signed in until the 7-day server session expires; press **Cloud account** in the pause menu and choose **Sign out** to forget it, for example before using a shared computer. Account names are 3–32 ASCII letters, numbers, dots, dashes, or underscores. Passwords have no minimum length; any non-empty password up to 1024 characters is accepted and stored only as a salted scrypt hash. An internet connection is required for cloud saves; the game itself can still be served locally.

Passwords are stored as salted scrypt hashes; bearer session tokens are also hashed in the database and the raw token is kept in the browser's `localStorage` so a reload keeps you signed in. Save contents are stored in the server's SQLite database without application-level encryption. Each save is limited to 4 MB, and each account can store up to 512 saves or 256 MB total. Keep a separate copy of important saves; availability depends on the hosted service.

### Running the cloud-save service

`girllife_server.py` serves both the package and `/api/...` from one process. It keeps HTTP/1.1 connections alive, creates the SQLite schema once at start-up instead of on every request, reuses one WAL connection per worker thread, and keeps per-account save counts and byte totals in an `account_usage` table maintained by database triggers, so saving never has to scan an account's rows. Work beyond `GIRLLIFE_MAX_CONCURRENT_REQUESTS` (default 96) is rejected with `503` and `Retry-After` instead of spawning unbounded threads, and idle connections are dropped after `GIRLLIFE_REQUEST_TIMEOUT` seconds (default 65). Other settings: `GIRLLIFE_SAVE_DATABASE` (database path) and `GIRLLIFE_PORT` (default 8081). A reverse proxy in front of it needs HTTP/1.1 upstream keep-alive to pass the connection reuse through. Older databases are upgraded in place on the first start, including the usage counters.

## Asset discovery and missing files

- An existing `images/` folder takes precedence over image ZIP archives. If no image folder exists, a matching ZIP can be selected and safely extracted.
- A `sound/` folder is used when present. If it is absent, the builder can select a matching sound ZIP; without either source, the game runs without its audio files.
- Image update ZIPs with `image` and one of `bugfix`, `patch`, or `update` in the filename are detected in the game folder and its parent. Identical copies are deduplicated. Multiple different update archives stop the build so the wrong patch is not silently applied.
- Missing media references are skipped; the builder does not create placeholder files.
- The final report counts unique, statically recognizable paths under `images/` and `sound/`, reference occurrences, and image/video/audio categories. Paths assembled dynamically from QSP variables or strings cannot be counted by this static scan.
- ZIP extraction rejects unsafe paths, symlinks, duplicate paths, and archives exceeding the configured expanded-size limit. RAR and 7z archives are not supported.

The original `.qsp`, asset folders, and source archives are preserved. Assets are hard-linked into the output when possible and copied when hard links are unavailable. Image patches are applied to the output package only.

## What this does and does not repair

This packages the original QSP game with the qSpider web player. It does not rewrite the game or fix bugs in the original QSP logic. Missing media is reported and skipped; gameplay defects in the source game remain.

## License

The builder is provided without an additional license grant. qSpider is distributed under the MIT License; its license text is included in generated packages as `QSPIDER-LICENSE.txt`. See the [qSpider v1.3.1 release](https://github.com/QSPFoundation/qspider/releases/tag/v1.3.1).
