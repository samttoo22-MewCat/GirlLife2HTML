# Launcher design and workflow review

Reviewed 2026-11-01. This is an independent browser implementation, not a port of Girl Life Launcher's source or assets.

## References

- [Girl Life Launcher releases](https://github.com/glife-launcher/GLreleases/releases)
- [v0.85.5 user guide](https://github.com/glife-launcher/GLreleases/releases/download/v0.85.5/GUIDE.html)
- [Reference screenshots](https://github.com/glife-launcher/GLreleases/tree/main/screenshots)

## Adopted in cloud-22 / corrected in cloud-23

| Reference concept | Browser adaptation | Boundary |
| --- | --- | --- |
| Play / Settings / Help | Three keyboard-accessible launcher tabs | Play only reveals the loaded QSP game; no automatic new game or save restore. |
| Dark launcher and gold primary action | Original CSS, SVG city drawing and icons | No copied artwork, screenshots, or launcher executable code. |
| Mobile tool strip | Home, Items, Stats, Saves, Menu | Opens engine panels rather than duplicating game logic. |
| Readable game layout | Desktop story/actions columns; mobile full-width split | Keeps original creation tables horizontally scrollable. |
| Respect game display preferences | Use FCOLOR/BCOLOR/LCOLOR for the game; mirror the effective palette into chrome | Never replace engine color variables with fixed shell colors. cloud-22 violated this and cloud-23 corrects it. |
| Side panels without losing choices | Wider desktop right column; Items/Stats above actions | Wider tables remain scrollable; mobile retains its pane-replacement behavior. |
| Immediate UI settings | Text, picture height and mobile split sliders | Browser-local preferences, not QSP variables or cloud account data. |
| Reset UI | Reset these three display values | Does not erase account, progress, saves, or engine preferences. |
| Save entry points | Reuse the existing authenticated save panel | Existing numbered/named/quick-save storage unchanged. |
| Diagnostic report | Local display and explicit copy action | No automatic upload; no account name, token, story or save contents. |

## Useful next steps, not implemented

| Workflow | Potential benefit | What must be established first |
| --- | --- | --- |
| Guided game-folder discovery | Easier first build and clearer missing-media recovery | Desktop launcher workflow differs from browser file permissions. |
| Translation export/import | Incremental, reviewable language packs without overwriting originals | Upstream translation/distribution permission, QSP-aware extraction, protected code tokens, version mapping and tests. |
| Old-save conversion | Easier migration from other players | Actual compatible fixtures, engine serialization support, backups and explicit user action. No promise of conversion compatibility. |
| Map / travel | Faster navigation | Must follow current QSP state, routes and event rules; cannot emulate by guessing links. |
| Update / rollback | Safer version upgrades | Integrity manifests, game/media/save compatibility, transactional deployment and tested recovery. |

Adding a link to upstream or keeping media separate does not itself resolve translation or hosting rights.

## Verification

### Builder-only checks

No game files, network calls, or third-party Python dependencies:

```sh
python3 -m unittest discover -s tests -p 'test_*.py' -v
```

Tests cover fresh injection, repeat-application idempotence, version refresh even if the same version appears elsewhere in the HTML, changed-upstream rejection, and generated JavaScript syntax (requires Node).

### Browser regression

Use an **isolated copy** of an authorized game package. Copy `girllife_server.py` into that package and serve it with `GIRLLIFE_SAVE_DATABASE` pointing to a new throwaway SQLite path and `GIRLLIFE_PORT=8094`. Do not point at production storage or an existing player's database.

With an existing Playwright installation and headless Edge:

```sh
GL_TEST_URL=http://127.0.0.1:8094 node tests/browser_ui.cjs
GL_TEST_URL=http://127.0.0.1:8094 node tests/theme_colors.cjs
```

If Playwright is not on Node's module search path, set `PLAYWRIGHT_MODULE` to its installed module directory. `GL_SCREENSHOTS` optionally selects a screenshot output directory. No dependency installation is performed by the script.

- The script rejects non-loopback test URLs and intercepts all production save-service API requests, forwarding them to the local test fixture from Node, not from a page-to-localhost fetch.
- It creates a local test account and save; only disposable storage may be used.
- Run once with `mode = "classic"`, then with `mode = "aero"` in the isolated package's `game/game.cfg`. Never change the live or original configuration just to test a theme.
- Both themes passed the complete flow at six viewports: 320×568, 390×844, 430×932, 768×1024, 901×700 and 1440×1000.
- Coverage: launcher tabs/keyboard, persistent/reset display preferences, creation table columns, real gameplay/inventory, panel exclusivity, Home preservation, image inside/outside taps, native preferences, authenticated save/load, sign-out, and Escape isolation.
- Color regression: select real Frappé → local save → real Latte → load; verify background/text/link colors and chrome match the saved game in both themes. Also check desktop Stats/actions separation and phone-width palette rendering.
- Scope caveat: the local Aero fixture logged HTTP 404 for `/aero/*.png` theme icons, plus optional game configuration probes. The passing color/layout suites do not establish complete theme-asset coverage; those missing-resource paths were not repaired in cloud-23.
- An exploratory Aero run received a storage HTTP 503; later runs passed. Backend storage reliability is not fixed or certified by this frontend change.
- Physical Android/iPhone/Safari, Windows launchers, every inventory interaction and full-game coverage remain unverified.
