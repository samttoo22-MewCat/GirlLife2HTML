# Builder maintenance guide

## Scope

- This repository is an unofficial builder, not Girl Life itself. Upstream release, source, and community links are in both READMEs.
- Do not commit the QSP, media packs, generated package, credentials, databases, or player saves.
- Keep original game/media and existing account/save behavior intact. Player licensing does not grant game-content or media rights.

## cloud-22 browser interface

- `build_girllife_html.py` remains a standalone builder. `BROWSER_UI_CSS` and `BROWSER_UI_JS` are embedded in it and written as `browser-ui.css` / `browser-ui.js` by `apply_cloud_saves()`.
- `CLOUD_SAVE_CACHE_VERSION` versions the player bundle, shell assets, account UI, and theme links. Reapplying mobile/cloud/loading patches must be idempotent.
- `window.girllifeBrowserCommand` is a thin bridge to existing gated cloud saves, account, preferences and resume operations. Do not emulate game actions or invent a second save format.
- Play reveals the loaded game. Home does not save/restart/load; progress remains in memory in that tab only. Do not imply a reload preserves unsaved play.
- Display settings use `girllife.browser.interface.v1`; cloud sessions keep their existing separate key. Reset affects only display settings.
- Default mobile story/actions split is 70/30 **of the space between the header and toolbar**, not the whole viewport. Preserve full-width inventory-mounted Classic layout and horizontal creation-table columns.
- Both Classic and Aero need actual gameplay tests. Aero absolute positioning retains `left` and min/max sizing; explicitly normalize those when changing to fixed layout.
- Radix may hide descendants of a native account dialog because it preserves `aria-live` nodes. Restore only Radix-marked hidden nodes within an open account dialog; keep intentional hidden fields hidden.
- Native-dialog Escape must be handled ahead of the engine's document-capture listeners so it closes only that dialog.
- Diagnostic reports are local, manually copied, and omit accounts, tokens, saves and story content.

## Verification and documentation

```sh
python3 -m unittest discover -s tests -p 'test_*.py' -v
```

See `docs/launcher-workflows.md` for browser test setup. Tests require an isolated package and throwaway SQLite database; production API writes are prohibited. Local test coverage includes both themes, six viewports, real gameplay, controls, settings, media taps and save/load/sign-out.

Keep `README.md`, `README.zh-TW.md`, screenshots and workflow notes aligned with verified behavior. Label local vs live captures and simulated vs physical-device results. An exploratory test encountered a save HTTP 503; frontend success is not a backend reliability guarantee.

Any future translation work must use a separate, permission-reviewed, QSP-aware pipeline; the launcher UI does not implement translation.
