# Builder maintenance guide

## Scope

- This repository is an unofficial builder, not Girl Life itself. Upstream release, source, and community links are in both READMEs.
- Do not commit the QSP binary, media packs, generated playable package, credentials, databases, or player saves.
- Explicit 2026-11-02 publication exception: the user requested Story Atlas workflow code, ALL extracted source/node information and current translation progress in this existing repository. `story-atlas/snapshot/` contains gzip extraction data and a consistent, dated JSONL progress export, not raw SQLite or credentials. Preserve source-content rights; the code license does not relicense game text. No public game-service deployment/restart is required for docs/data publication.
- Keep progress labelled as a snapshot, not live/finished or directly resumable production state. Preserve historical model labels; new stages all use DeepSeek V4.1 Flash. Do not publish operational AGENTS notes, private OAuth/configured-key files, raw requests/responses, logs or backups.
- `python3 story-atlas/verify_snapshot.py` verifies manifest hashes, all747,993 literal spans,1,397 locations,19,168 nodes/141,109 edges,248,199 catalog units and exported progress row counts. Static edge IDs are array indices (`enumerate(graph['edges'])`), not an edge `id` field; the first publication verifier assumed the latter and failed before publishing, then was corrected against `static_tree.py` and passed the full data check. Do not change source IDs to satisfy a verification fixture.
- Keep original game/media and existing account/save behavior intact. Player licensing does not grant game-content or media rights.

## cloud-23 browser interface

- `build_girllife_html.py` remains a standalone builder. `BROWSER_UI_CSS` and `BROWSER_UI_JS` are embedded in it and written as `browser-ui.css` / `browser-ui.js` by `apply_cloud_saves()`.
- `CLOUD_SAVE_CACHE_VERSION` versions the player bundle, shell assets, account UI, and theme links. Reapplying mobile/cloud/loading patches must be idempotent.
- `window.girllifeBrowserCommand` is a thin bridge to existing gated cloud saves, account, preferences and resume operations. Do not emulate game actions or invent a second save format.
- Play reveals the loaded game. Home does not save/restart/load; progress remains in memory in that tab only. Do not imply a reload preserves unsaved play.
- Display settings use `girllife.browser.interface.v1`; cloud sessions keep their existing separate key. Reset affects only display settings.
- Default mobile story/actions split is 70/30 **of the space between the header and toolbar**, not the whole viewport. Preserve full-width inventory-mounted Classic layout and horizontal creation-table columns.
- Both Classic and Aero need actual gameplay tests. Aero absolute positioning retains `left` and min/max sizing; explicitly normalize those when changing to fixed layout.
- **Never shadow engine `--color`, `--background-color`, `--link-color`, or their `--code-*` inputs with a shell palette.** Classic derives them from FCOLOR/BCOLOR/LCOLOR; Aero supplies them directly. Read effective values into shell-only `--gl-*` variables. Observe stylesheet text mutations as well as DOM changes so save restoration and dynamic game colors update chrome.
- Desktop Items/Stats occupy the top of a 340–460px right column; actions remain visible underneath. Keep content constrained and original wider tables horizontally scrollable rather than clipping them.
- Radix may hide descendants of a native account dialog because it preserves `aria-live` nodes. Restore only Radix-marked hidden nodes within an open account dialog; keep intentional hidden fields hidden.
- Native-dialog Escape must be handled ahead of the engine's document-capture listeners so it closes only that dialog.
- Diagnostic reports are local, manually copied, and omit accounts, tokens, saves and story content.

## Verification and documentation

```sh
python3 -m unittest discover -s tests -p 'test_*.py' -v
```

See `docs/launcher-workflows.md` for browser test setup. Tests require an isolated package and throwaway SQLite database; production API writes are prohibited. Local test coverage includes both themes, six viewports, real gameplay, controls, settings, media taps and save/load/sign-out.

`tests/theme_colors.cjs` additionally selects real Frappé/Latte game settings, saves the dark palette locally, changes it to light, and verifies load restores engine + rendered background/text/link colors + chrome. It checks non-overlapping desktop Stats/actions and phone widths. Do not replace this with a synthetic CSS-only test.

Keep `README.md`, `README.zh-TW.md`, screenshots and workflow notes aligned with verified behavior. Label local vs live captures and simulated vs physical-device results. An exploratory test encountered a save HTTP 503; frontend success is not a backend reliability guarantee.

Any future translation work must use a separate, permission-reviewed, QSP-aware pipeline; the launcher UI does not implement translation.
