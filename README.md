# Girl Life HTML Builder

繁體中文說明：[README.zh-TW.md](README.zh-TW.md)

Build and run a browser-playable package of the original English Girl Life QSP game using the official QSPFoundation qSpider player.

> **Builder only:** this repository does not include the game, images, sounds, or generated game package. Provide the game and assets you are authorized to use.

## Contents

- [Screenshots](#screenshots)
- [Requirements](#requirements)
- [Folder layout](#folder-layout)
- [Build and run](#build-and-run)
- [Loading and mobile interface](#loading-and-mobile-interface)
- [Validation status](#validation-status)
- [Cloud saves](#cloud-saves)
- [Server configuration](#server-configuration)
- [Asset discovery and missing files](#asset-discovery-and-missing-files)
- [Scope and limitations](#scope-and-limitations)
- [License](#license)

## Screenshots

- **Source:** live cloud-21 site, captured on 2026-11-01.
- **Mobile:** simulated 390×844 touch viewport, not a physical phone.
- **Account dialog:** shared registration/sign-in screen, with no credentials entered; background game images are hidden in this screenshot.
- **Credits:** game content belongs to its respective creators.

### Mobile interface and account dialog

<img src="docs/screenshots/mobile-interface.png" alt="Mobile interface with scrollable character-creation table and Items/Stats controls" width="300"> <img src="docs/screenshots/account-interface.png" alt="Cloud account registration and sign-in dialog" width="300">

### Desktop website

![Desktop website at 1440×1000](docs/screenshots/website-interface.png)

## Requirements

- macOS or Windows 10/11
- An English `.qsp` game file
- An `images/` directory or a recognized image ZIP archive
- A `sound/` directory or, optionally, a recognized sound ZIP archive
- Internet access for the first build, to download the qSpider v1.3.1 player

### Python setup

1. The launcher checks for **Python 3.10 or newer**.
2. If missing, it asks before downloading the official installer from [python.org](https://www.python.org/downloads/).
3. Answer `y` to continue, or press Enter to cancel.
4. The installer signature is checked before opening or running it.

| Platform | Installation behavior |
| --- | --- |
| macOS | Opens the official installer UI. |
| Windows | Installs for the current user; the install command does not request administrator access. |

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

- **Game selection:** the English `.qsp` filename must not contain `_cn`.
- **Multiple games:** the builder asks you to choose one.
- **Sound fallback:** it can use the parent directory's `sound/` folder when that is the only adjacent sound folder found.

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

1. Double-click **`Build-GirlLife-HTML.bat`**.
2. The included PowerShell launcher checks Python and asks for consent before any required download/install.
3. The builder starts after the prerequisite check.

PowerShell is included with supported Windows versions. **Windows execution remains unverified.**

### What happens next

1. Creates a sibling folder named `<English game filename>_HTML`.
2. Audits assets and reports missing media.
3. Starts a local server on a dynamically assigned port.
4. Prints the URL and tries to open it in your browser.

> **Keep the launcher window open while playing.** Press `Ctrl+C` in that window to stop the server.

If a valid output folder already exists, it is preserved: the builder audits and serves it instead of overwriting it.

## Loading and mobile interface

### Loading performance

- **Download progress:** shows game/media bytes as they download, then elapsed seconds while the game is being prepared.
- **Compressed transfer:** creates a reusable `.qsp.gz` copy; supporting servers reduce the current game transfer from about **91 MB to 11 MB**. The original `.qsp` stays intact.
- **Scene media warming:** uses a bounded queue for images/videos referenced by the current scene and visible actions, rather than preloading the entire library.
- **Video limits:** warms metadata, not necessarily the entire video. Other assets load when requested.

### Mobile layout

Applies to **Classic and Aero at widths up to 900 CSS px**.

| Element | Behavior |
| --- | --- |
| Main / actions | Full-width panes with a 70% / 30% vertical split. |
| Main text viewport | Approximately 60% of screen height after reserving space for the Items control; varies with viewport size. |
| Items | Upper-right toggle; opens an overlay in the main-pane area. |
| Stats | Lower-right toggle; opens an overlay in the actions-pane area. |
| Side panels | Only one can be open at a time. |
| Content and actions | Independently scrollable, with touch-sized controls. |
| Creation tables | Preserve horizontal columns; scroll sideways on narrow screens. |
| Images and videos | Scale to available space. |
| Media popup | Centered on a dark backdrop; tapping outside closes only that popup. |

## Validation status

**Last deployment verification: 2026-10-30.** This is a recorded result, not a continuous uptime check.

| Check | Recorded result |
| --- | --- |
| Live version | **cloud-21** at `https://girllife.mewcat.me/` |
| Source | Builder/docs on GitHub `main`; layout fix `0ac3645` |
| Service | Restarted; `active` |
| Homepage | HTTP 200 |
| Deployed frontend files | All four SHA-256 hashes matched local files |

### Latest fix: full-width gameplay

- **Cause:** mounting inventory added a 215px right grid track in Classic. Hiding the inventory pane alone left that space reserved.
- **Fix:** cloud-21 collapses that track; main/actions use the full width while Items still opens as an overlay. The 70/30 vertical split is unchanged.
- **Local verification:** reached the cottage and guest room with inventory mounted at **320×568, 390×844, and 430×932**, with no page overflow.

- Public-site validation reached the grandparents' cottage with inventory mounted: at a 390px viewport, the main pane measures the full 390px and the right grid track is 0px. Local Items overlay opening/closing was also checked.
- Earlier prologue-only checks missed the inventory-triggered grid change; changing the vertical split did not fix the reported blank area on the right.
- These checks do not verify the exact Pavlovsk bedroom shown in the user's screenshot, every inventory interaction, or all gameplay paths. Physical Android/iPhone/Safari and Windows execution remain unverified.
- This update changed only frontend layout files; the original QSP/media and production accounts, saves, and database content were not modified.

> **Still unverified:** physical Android/iPhone/Safari, Windows execution, the exact Pavlovsk bedroom from the report, all inventory interactions, and full gameplay coverage.

## Cloud saves

Supports **numbered slots, named saves, and quick saves** through `girllife.mewcat.me`.

### Register and sign in

1. Open **Saves** or the title-screen **LOAD** button.
2. On a device never signed in before, **Create account** is offered first; existing users can choose **Sign in**.
3. Enter an account name and one password; **no confirmation field** is required.
4. If that name already exists, the dialog switches to sign-in mode and preserves your input.
5. Sign in on another device with the same account to access the same saves.

| Setting | Requirement |
| --- | --- |
| Account name | 3–32 ASCII letters, digits, dots, dashes, or underscores |
| Password | Non-empty; up to 1024 characters; no additional minimum length |
| Network | Required for cloud saves; the game can still be served locally |

### Remembered sessions

- Browser `localStorage` remembers the session under `girllife.cloud.session` across reloads and browser restarts.
- Server-side sessions expire after **7 days**.
- To forget the local session, choose **Cloud account → Sign out** in the pause menu.
- Sign-out clears this device's record only; the server session remains until expiry. Remember to sign out on shared computers.

### Storage, privacy, and limits

| Item | Storage / limit |
| --- | --- |
| Passwords | Salted scrypt hashes |
| Session tokens | Hashed in the database; raw token in browser `localStorage` |
| Save contents | Server SQLite database; **no application-level encryption** |
| One save | Up to **4 MB** |
| One account | Up to **512 saves** or **256 MB** total |

> Keep a separate copy of important saves. Availability depends on the hosted service.

## Server configuration

`girllife_server.py` serves the game package and `/api/...` from one process.

| Environment variable | Default / purpose |
| --- | --- |
| `GIRLLIFE_PORT` | `8081` |
| `GIRLLIFE_SAVE_DATABASE` | SQLite database path |
| `GIRLLIFE_MAX_CONCURRENT_REQUESTS` | `96`; excess work gets HTTP `503` with `Retry-After` |
| `GIRLLIFE_REQUEST_TIMEOUT` | `65` seconds; closes idle connections |

**Request and database handling:**

- HTTP/1.1 keep-alive, with bounded concurrency rather than unlimited threads.
- SQLite schema initialization once at startup, not per request.
- One reusable WAL connection per worker thread.
- Trigger-maintained `account_usage` counts/byte totals instead of scanning an account's saves for every write.

**Deployment notes:**

- A reverse proxy must enable **HTTP/1.1 upstream keep-alive** to preserve connection reuse.
- Older databases are upgraded in place on first startup, including usage-counter backfill.

## Asset discovery and missing files

- An existing `images/` folder takes precedence over image ZIP archives. If no image folder exists, a matching ZIP can be selected and safely extracted.
- A `sound/` folder is used when present. If it is absent, the builder can select a matching sound ZIP; without either source, the game runs without its audio files.
- Image update ZIPs with `image` and one of `bugfix`, `patch`, or `update` in the filename are detected in the game folder and its parent. Identical copies are deduplicated. Multiple different update archives stop the build so the wrong patch is not silently applied.
- Missing media references are skipped; the builder does not create placeholder files.
- The final report counts unique, statically recognizable paths under `images/` and `sound/`, reference occurrences, and image/video/audio categories. Paths assembled dynamically from QSP variables or strings cannot be counted by this static scan.
- ZIP extraction rejects unsafe paths, symlinks, duplicate paths, and archives exceeding the configured expanded-size limit. RAR and 7z archives are not supported.

The original `.qsp`, asset folders, and source archives are preserved. Assets are hard-linked into the output when possible and copied when hard links are unavailable. Image patches are applied to the output package only.

## Scope and limitations

- **Does:** packages the original QSP game with qSpider and reports missing media.
- **Does not:** rewrite the game or repair its internal QSP gameplay logic.
- **Missing assets:** reported and skipped; not replaced with placeholder files.
- **Testing:** browser checks do not establish full-game, physical-device, or Windows compatibility.

## License

The builder is provided without an additional license grant. qSpider is distributed under the MIT License; its license text is included in generated packages as `QSPIDER-LICENSE.txt`. See the [qSpider v1.3.1 release](https://github.com/QSPFoundation/qspider/releases/tag/v1.3.1).
