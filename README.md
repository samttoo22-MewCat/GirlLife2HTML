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

The builder creates a sibling folder named `<English game filename>_HTML`, reports missing media, starts a local web server on a dynamically assigned port, prints the local URL, and tries to open it in your browser. A progress bar reports the game file and media bytes as they download. The builder creates a reusable gzip copy of the QSP; supporting servers send it automatically, reducing the current 91 MB game file to about 11 MB over the network. The original `.qsp` remains intact. The browser fetches images, video, and sound only when the QSP game requests them; the builder does not preload the entire media library into the browser. On narrow phone screens, the Aero layout reflows into readable text, touch sized actions, and media that fits the available space. A side panel menu switches between Items and Stats, with only one open at a time. Tap the blank area around a popup image to close it. Keep the launcher window open while playing. Press `Ctrl+C` in that window to stop the server.

If a valid output folder already exists, it is preserved. The builder audits and serves that existing package rather than overwriting it.

## Cloud saves

The generated player stores numbered slots, named saves, and quick saves on the GirlLife cloud service at `girllife.mewcat.me`. Open the game's **Saves** panel and choose **Cloud account / Sign in**. Create one account, then sign in with that account on other devices to access the same saves. Sign in again after reloading the game page because the session token is held only in browser memory. Account names are 3–32 ASCII letters, numbers, dots, dashes, or underscores. New passwords must be at least 12 characters. An internet connection is required for cloud saves; the game itself can still be served locally.

Passwords are stored as salted scrypt hashes; bearer session tokens are also hashed in the database and held in browser memory. Save contents are stored in the server's SQLite database without application-level encryption. Each save is limited to 4 MB, and each account can store up to 512 saves or 256 MB total. Keep a separate copy of important saves; availability depends on the hosted service.

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
