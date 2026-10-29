# Girl Life HTML Builder

Build and run a browser-playable package of the original English Girl Life QSP game using the official QSPFoundation qSpider player.

This repository contains the builder only. It does not include the game, images, sounds, or generated game package. You must provide the game and assets you are authorized to use.

## Requirements

- Python 3.10 or newer
- Internet access on the first build, to download the qSpider v1.3.1 standalone player and verify its SHA-256 checksum
- A local English `.qsp` game file
- An `images/` directory or a recognized image ZIP archive
- A `sound/` directory or, optionally, a recognized sound ZIP archive

No third-party Python packages are required.

## Folder layout

Put the builder files beside the English `.qsp` file and its assets:

```text
GirlLife/
├── Build-GirlLife-HTML.bat
├── Build-GirlLife-HTML.sh
├── build_girllife_html.py
├── Girl Life 0.9.9.2.qsp
├── images/
└── sound/                 # optional; audio will be absent if not provided
```

The builder selects an English `.qsp` file (one whose filename does not contain `_cn`). If there is more than one, it asks you to choose. It can also use a `sound/` folder in the parent directory when that is the only adjacent sound folder it finds.

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

Double-click `Build-GirlLife-HTML.bat`. It uses the Python launcher (`py -3`) when available, then falls back to `python`.

The builder creates a sibling folder named `<English game filename>_HTML`, reports missing media, starts a local web server on a dynamically assigned port, prints the local URL, and tries to open it in your browser. Keep the builder window open while playing. Press `Ctrl+C` in that window to stop the server.

If a valid output folder already exists, it is preserved. The builder audits and serves that existing package rather than overwriting it.

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
