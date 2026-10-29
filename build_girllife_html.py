#!/usr/bin/env python3
"""Package one local Girl Life QSP release with the official qSpider web player."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import sys
import tempfile
import urllib.request
import webbrowser
import zipfile
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath


PLAYER_URL = "https://github.com/QSPFoundation/qspider/releases/download/v1.3.1/qspider-player-standalone.zip"
PLAYER_SHA256 = "acedec0b5597c055559f95ac52a64518bf106d4dbef2262c1c8c7f4ec822a47c"
MEDIA_PATH_RE = re.compile(
    r"(?i)(?P<path>(?:images|sound)[/\\][^\"'<>|,;\r\n]*?\.(?:jpg|jpeg|png|gif|bmp|webp|mp3|ogg|wav|mp4|webm))(?=$|[\s\"'<>|,;)&])"
)
PLAYER_LICENSE = """MIT License

Copyright (c) 2020-present Sergii Kostyrko <srg.kostyrko@gmail.com>

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""


def download_player(destination: Path) -> None:
    request = urllib.request.Request(PLAYER_URL, headers={"User-Agent": "GirlLife-HTML-builder"})
    with urllib.request.urlopen(request, timeout=45) as response, destination.open("wb") as output:
        shutil.copyfileobj(response, output)
    digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    if digest != PLAYER_SHA256:
        raise RuntimeError("qSpider 下載檔的 SHA-256 不符；已中止建置。")


def unpack_player(archive_path: Path, destination: Path) -> None:
    seen: set[str] = set()
    total_size = 0
    with zipfile.ZipFile(archive_path) as archive:
        entries = archive.infolist()
        for entry in entries:
            relative = PurePosixPath(entry.filename)
            mode = entry.external_attr >> 16
            key = entry.filename.casefold()
            if relative.is_absolute() or ".." in relative.parts or stat.S_ISLNK(mode):
                raise RuntimeError(f"播放器 ZIP 含有不安全路徑：{entry.filename}")
            if key in seen:
                raise RuntimeError(f"播放器 ZIP 含有重複路徑：{entry.filename}")
            seen.add(key)
            total_size += entry.file_size
            if total_size > 64 * 1024 * 1024:
                raise RuntimeError("播放器 ZIP 解壓大小超出預期；已中止建置。")

        for entry in entries:
            target = destination.joinpath(*PurePosixPath(entry.filename).parts)
            if entry.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(entry) as source, target.open("xb") as output:
                shutil.copyfileobj(source, output)


def link_or_copy_tree(source: Path, destination: Path) -> None:
    destination.mkdir(parents=True)
    for entry in source.iterdir():
        target = destination / entry.name
        if entry.is_symlink():
            raise RuntimeError(f"素材目錄含有符號連結，請先確認後再建置：{entry}")
        if entry.is_dir():
            link_or_copy_tree(entry, target)
        elif entry.is_file():
            try:
                os.link(entry, target)
            except OSError:
                shutil.copy2(entry, target)
        else:
            raise RuntimeError(f"不支援的素材項目：{entry}")


def archive_candidates(root: Path, kind: str) -> tuple[list[Path], list[Path]]:
    terms = {
        "images": ("image", "media", "girllife", "girl life", "glife", "核心", "圖", "图片"),
        "sound": ("sound", "audio", "音"),
    }[kind]
    supported: list[Path] = []
    unsupported: list[Path] = []
    for path in root.iterdir():
        if not path.is_file() or not any(term in path.name.casefold() for term in terms):
            continue
        suffix = path.suffix.casefold()
        if suffix == ".zip":
            supported.append(path)
        elif suffix in {".7z", ".rar"}:
            unsupported.append(path)
    return sorted(supported), sorted(unsupported)


def image_patch_candidates(root: Path) -> list[Path]:
    candidates = sorted({
        path
        for folder in (root, root.parent)
        for path in folder.iterdir()
        if path.is_file() and path.suffix.casefold() == ".zip"
        and "image" in path.name.casefold()
        and any(word in path.name.casefold() for word in ("bugfix", "patch", "update"))
    })
    unique: dict[str, Path] = {}
    for path in candidates:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest in unique:
            print(f"忽略相同修正包副本：{path}")
        else:
            unique[digest] = path
    return sorted(unique.values())


def choose_archive(archives: list[Path], kind: str) -> Path:
    if len(archives) == 1:
        return archives[0]
    print(f"找到多個 {kind} ZIP，請選擇一個：")
    for number, archive in enumerate(archives, 1):
        print(f"  {number}. {archive.name}")
    while True:
        answer = input("輸入編號：").strip()
        if answer.isdigit() and 1 <= int(answer) <= len(archives):
            return archives[int(answer) - 1]
        print("編號無效，請重新輸入。")


def unpack_media_archive(archive_path: Path, destination: Path, kind: str) -> int:
    seen: set[str] = set()
    file_entries: list[tuple[zipfile.ZipInfo, tuple[str, ...]]] = []
    total_size = 0
    with zipfile.ZipFile(archive_path) as archive:
        for entry in archive.infolist():
            raw = entry.filename.replace("\\", "/")
            if raw.startswith("/") or re.match(r"^[A-Za-z]:", raw):
                raise RuntimeError(f"素材 ZIP 含有絕對路徑：{entry.filename}")
            parts = tuple(part for part in raw.split("/") if part not in ("", "."))
            if ".." in parts:
                raise RuntimeError(f"素材 ZIP 含有不安全路徑：{entry.filename}")
            mode = entry.external_attr >> 16
            if stat.S_ISLNK(mode):
                raise RuntimeError(f"素材 ZIP 含有符號連結：{entry.filename}")
            if not parts or entry.is_dir():
                continue
            kind_index = next((index for index, part in enumerate(parts) if part.casefold() == kind), None)
            if kind_index is not None:
                parts = parts[kind_index + 1 :]
            if not parts:
                continue
            key = "/".join(parts).casefold()
            if key in seen:
                raise RuntimeError(f"素材 ZIP 有重複檔案路徑：{'/'.join(parts)}")
            seen.add(key)
            total_size += entry.file_size
            if total_size > 20 * 1024 * 1024 * 1024:
                raise RuntimeError("素材 ZIP 解壓大小超過 20 GB；已中止建置。")
            file_entries.append((entry, parts))

        if not file_entries:
            raise RuntimeError(f"{archive_path.name} 裡沒有可用的 {kind} 檔案。")
        destination.mkdir(parents=True)
        for entry, parts in file_entries:
            target = destination.joinpath(*parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(entry) as source, target.open("xb") as output:
                shutil.copyfileobj(source, output)
    return len(file_entries)


def overlay_image_patch(archive_path: Path, destination: Path) -> tuple[int, int, int]:
    staging = Path(tempfile.mkdtemp(prefix="girllife-image-patch-", dir=destination.parent))
    try:
        staged_images = staging / "images"
        count = unpack_media_archive(archive_path, staged_images, "images")
        added = replaced = unchanged = 0
        for staged in sorted(path for path in staged_images.rglob("*") if path.is_file()):
            relative = staged.relative_to(staged_images)
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                old = target.stat()
                new = staged.stat()
                if old.st_size == new.st_size and hashlib.sha256(target.read_bytes()).digest() == hashlib.sha256(staged.read_bytes()).digest():
                    unchanged += 1
                    continue
                replaced += 1
            else:
                added += 1
            os.replace(staged, target)
        if added + replaced + unchanged != count:
            raise RuntimeError("圖片修正包檔案數量不一致。")
        return added, replaced, unchanged
    finally:
        shutil.rmtree(staging)


def decode_qsp_string(value: str) -> str:
    return "".join(
        chr(5) if ord(char) == 0xFFFB else char if ord(char) == 0 else chr((ord(char) + 5) & 0xFFFF)
        for char in value
    )


def strip_qsp_comments(source: str) -> str:
    output: list[str] = []
    index = 0
    quote = ""
    brace_depth = 0
    while index < len(source):
        char = source[index]
        if quote:
            output.append(char)
            if quote == "{":
                if char == "{":
                    brace_depth += 1
                elif char == "}":
                    brace_depth -= 1
                    if brace_depth == 0:
                        quote = ""
            elif char == quote:
                if index + 1 < len(source) and source[index + 1] == quote:
                    output.append(source[index + 1])
                    index += 1
                else:
                    quote = ""
            index += 1
            continue
        if char in "\"'{":
            quote = char
            brace_depth = 1 if char == "{" else 0
            output.append(char)
            index += 1
            continue
        if char == "!":
            line_start = max(source.rfind("\n", 0, index), source.rfind("\r", 0, index)) + 1
            prefix = source[line_start:index].rstrip()
            if not prefix or prefix.endswith(("&", ":")):
                newline = min((pos for pos in (source.find("\n", index), source.find("\r", index)) if pos >= 0), default=len(source))
                opener = index + 1
                while opener < newline and source[opener] in " \t":
                    opener += 1
                delimiter = source[opener] if opener < newline and source[opener] in "\"'{" else ""
                if delimiter:
                    depth = 1
                    cursor = opener + 1
                    while cursor < len(source) and depth:
                        current = source[cursor]
                        if delimiter in "\"'" and current == delimiter:
                            if cursor + 1 < len(source) and source[cursor + 1] == delimiter:
                                cursor += 2
                                continue
                            depth = 0
                        elif delimiter == "{" and current == "{":
                            depth += 1
                        elif delimiter == "{" and current == "}":
                            depth -= 1
                        cursor += 1
                    end = min((pos for pos in (source.find("\n", cursor), source.find("\r", cursor)) if pos >= 0), default=len(source))
                    output.append("\n")
                    index = end + 1
                    continue
                output.append("\n")
                index = newline + 1
                continue
        output.append(char)
        index += 1
    return "".join(output)


def audit_missing_media(game: Path, image_root: Path, sound_root: Path) -> tuple[dict[str, int], int]:
    present = {
        f"{folder}/{path.relative_to(root).as_posix()}".casefold()
        for folder, root in (("images", image_root), ("sound", sound_root))
        if root.is_dir()
        for path in root.rglob("*")
        if path.is_file()
    }
    references: dict[str, int] = {}
    dynamic: set[str] = set()
    with game.open("r", encoding="utf-16le", newline="") as source:
        for line_number, encoded in enumerate(source):
            if line_number < 4:
                continue
            code = strip_qsp_comments(decode_qsp_string(encoded.rstrip("\r\n")))
            for match in MEDIA_PATH_RE.finditer(code):
                path = match.group("path").replace("\\", "/")
                prefix = code[max(0, match.start() - 24) : match.start()]
                if re.search(r"https?://[^\s\"']*$", prefix, re.IGNORECASE):
                    continue
                if "$" in path or "<<" in path or ">>" in path:
                    dynamic.add(path.casefold())
                    continue
                key = path.casefold()
                references[key] = references.get(key, 0) + 1
    missing = {path: count for path, count in references.items() if path not in present}
    by_kind = {"圖片": 0, "影片": 0, "音效": 0}
    for path in missing:
        suffix = Path(path).suffix.casefold()
        kind = "音效" if suffix in {".mp3", ".ogg", ".wav"} else "影片" if suffix in {".mp4", ".webm"} else "圖片"
        by_kind[kind] += 1
    return by_kind, sum(missing.values())


def choose_game(games: list[Path]) -> Path:
    if len(games) == 1:
        return games[0]
    print("找到多個 QSP 遊戲檔，請選擇這次要打包的一個：")
    for number, game in enumerate(games, 1):
        print(f"  {number}. {game.name}")
    while True:
        answer = input("輸入編號：").strip()
        if answer.isdigit() and 1 <= int(answer) <= len(games):
            return games[int(answer) - 1]
        print("編號無效，請重新輸入。")


def find_sound_directory(root: Path) -> Path | None:
    local = root / "sound"
    if local.is_dir() and any(local.iterdir()):
        return local
    candidates = sorted(
        child / "sound"
        for child in root.parent.iterdir()
        if child.is_dir() and child.resolve() != root.resolve()
        and (child / "sound").is_dir() and any((child / "sound").iterdir())
    )
    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1:
        print("遊戲資料夾沒有 sound/，找到多個相鄰音效目錄：")
        for number, candidate in enumerate(candidates, 1):
            print(f"  {number}. {candidate}")
        while True:
            answer = input("輸入音效目錄編號（直接 Enter 略過）：").strip()
            if not answer:
                return None
            if answer.isdigit() and 1 <= int(answer) <= len(candidates):
                return candidates[int(answer) - 1]
            print("編號無效，請重新輸入。")
    return None


def write_file(path: Path, contents: str, executable: bool = False) -> None:
    path.write_text(contents, encoding="utf-8")
    if executable:
        path.chmod(path.stat().st_mode | 0o111)


def serve_local_site(output: Path) -> None:
    handler = partial(SimpleHTTPRequestHandler, directory=str(output))
    with ThreadingHTTPServer(("127.0.0.1", 0), handler) as server:
        url = f"http://127.0.0.1:{server.server_port}/"
        print(f"本機網站已啟動：{url}", flush=True)
        print("按 Ctrl+C 停止網站服務。", flush=True)
        try:
            opened = webbrowser.open_new_tab(url)
            if not opened:
                print("瀏覽器未自動開啟，請手動複製上方網址。", flush=True)
        except webbrowser.Error:
            print("瀏覽器未自動開啟，請手動複製上方網址。", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("本機網站已停止。", flush=True)


def main() -> int:
    root = Path(__file__).resolve().parent
    games = sorted(
        (path for path in root.iterdir() if path.is_file() and path.suffix.casefold() == ".qsp"),
        key=lambda path: ("_cn" in path.stem.casefold(), path.name.casefold()),
    )
    english_games = [path for path in games if "_cn" not in path.stem.casefold()]
    images = root / "images"
    image_archives, image_unsupported = archive_candidates(root, "images")
    image_patches = image_patch_candidates(root)
    image_archives = [path for path in image_archives if path not in image_patches]
    sound = find_sound_directory(root)
    sound_archives, sound_unsupported = archive_candidates(root, "sound")
    recognized_archives = set(image_archives + image_unsupported + image_patches + sound_archives + sound_unsupported)
    unmatched_archives = sorted(
        path for path in root.iterdir()
        if path.is_file() and path.suffix.casefold() in {".zip", ".7z", ".rar"}
        and path not in recognized_archives
    )
    if not english_games:
        raise RuntimeError("此資料夾找不到英文 .qsp 遊戲本體。")
    has_images = images.is_dir() and any(images.iterdir())
    image_archive: Path | None = None
    if image_unsupported:
        print("警告：圖片 RAR／7z 不支援：" + ", ".join(p.name for p in image_unsupported))
    if has_images and image_archives:
        print(f"警告：同時找到 images/ 與圖片 ZIP；只使用資料夾，不解壓或覆寫：{', '.join(p.name for p in image_archives)}")
    elif not has_images and image_archives:
        image_archive = choose_archive(image_archives, "圖片")
    elif not has_images:
        suffixes = ", ".join(p.name for p in image_unsupported)
        extra = f" 找到但不支援的壓縮檔：{suffixes}。" if suffixes else ""
        raise RuntimeError("此資料夾找不到 images/ 或可用的圖片 ZIP。" + extra)

    if sound_unsupported:
        print("警告：音效 RAR／7z 不支援：" + ", ".join(p.name for p in sound_unsupported))
    if sound is not None and sound_archives:
        print(f"警告：同時找到 sound/ 與音效 ZIP；只使用資料夾，不解壓或覆寫：{', '.join(p.name for p in sound_archives)}")
    elif sound is None and sound_archives:
        sound = choose_archive(sound_archives, "音效")

    if unmatched_archives:
        print("警告：以下壓縮檔名稱無法判斷用途，未納入本次產物：" + ", ".join(p.name for p in unmatched_archives))

    if len(english_games) > 1:
        game = choose_game(english_games)
    else:
        game = english_games[0]
    output = root / f"{game.stem}_HTML"
    if output.exists():
        if not (output / "index.html").is_file() or not (output / "game" / game.name).is_file():
            raise RuntimeError(f"既有輸出資料夾不完整，為避免覆蓋已停止：{output.name}")
        print(f"找到既有產物，不覆蓋：{output}")
        missing_by_kind, missing_references = audit_missing_media(
            game, output / "game" / "images", output / "game" / "sound"
        )
        missing_count = sum(missing_by_kind.values())
        breakdown = "、".join(f"{kind}{count}" for kind, count in missing_by_kind.items() if count)
        print(f"素材缺失：{missing_count} 個固定路徑（{breakdown}；{missing_references} 次引用）。")
        print("由變數或字串組合出的動態路徑不列入此數字。")
        serve_local_site(output)
        return 0

    print(f"\n準備打包：{game.name}")
    if sound is None:
        print("警告：找不到 sound/ 音效目錄；本次產物不會有遊戲原音效。")
    elif sound.is_dir():
        print(f"使用音效目錄：{sound}")
    else:
        print(f"使用音效 ZIP：{sound.name}")
    print("下載並驗證官方 qSpider v1.3.1 播放器……")

    with tempfile.TemporaryDirectory(prefix="girllife-qspider-") as temp_dir:
        player_zip = Path(temp_dir) / "qspider-player-standalone.zip"
        download_player(player_zip)

        output.mkdir()
        try:
            unpack_player(player_zip, output)
            game_dir = output / "game"
            game_dir.mkdir(exist_ok=True)
            shutil.copy2(game, game_dir / game.name)
            config = (
                "[[game]]\n"
                f"id = {json.dumps('girllife-' + hashlib.sha256(game.name.encode()).hexdigest()[:16])}\n"
                f"title = {json.dumps(game.stem)}\n"
                f"file = {json.dumps(game.name)}\n"
            )
            write_file(game_dir / "game.cfg", config)
            image_source_note = "來源 images/ 資料夾。"
            if image_archive is not None:
                count = unpack_media_archive(image_archive, game_dir / "images", "images")
                image_source_note = f"從 {image_archive.name} 安全解壓 {count} 個圖片檔。"
            else:
                link_or_copy_tree(images, game_dir / "images")
            patch_note = ""
            if image_patches:
                if len(image_patches) > 1:
                    raise RuntimeError("找到多個內容不同的圖片修正包，為避免套用錯誤版本，請只保留要用的一個：" + ", ".join(p.name for p in image_patches))
                added, replaced, unchanged = overlay_image_patch(image_patches[0], game_dir / "images")
                patch_note = f"圖片修正：套用 `{image_patches[0].name}`（新增 {added}、更新 {replaced}、相同 {unchanged} 個檔案）；遊戲引用但素材不存在的檔案會略過。"
            if sound is not None:
                if sound.is_dir():
                    link_or_copy_tree(sound, game_dir / "sound")
                    sound_note = f"已包含來源目錄 `{sound}`。"
                else:
                    count = unpack_media_archive(sound, game_dir / "sound", "sound")
                    sound_note = f"從 {sound.name} 安全解壓 {count} 個音效檔。"
            else:
                sound_note = "找不到 sound/ 來源；遊戲音效不在此包內。"

            (output / "QSPIDER-LICENSE.txt").write_text(PLAYER_LICENSE, encoding="utf-8")
            write_file(output / "serve.py", '''from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import webbrowser

root = Path(__file__).resolve().parent
handler = partial(SimpleHTTPRequestHandler, directory=str(root))
with ThreadingHTTPServer(("127.0.0.1", 0), handler) as server:
    url = f"http://127.0.0.1:{server.server_port}/"
    print(f"本機網站已啟動：{url}", flush=True)
    print("按 Ctrl+C 停止網站服務。", flush=True)
    try:
        if not webbrowser.open_new_tab(url):
            print("瀏覽器未自動開啟，請手動複製上方網址。", flush=True)
        server.serve_forever()
    except KeyboardInterrupt:
        print("本機網站已停止。", flush=True)
''')
            write_file(output / "Launch.command", '''#!/bin/sh
cd "$(dirname "$0")"
exec python3 serve.py
''', executable=True)
            write_file(output / "Launch.bat", '''@echo off
cd /d "%~dp0"
where py >nul 2>nul
if not errorlevel 1 (
  py -3 serve.py
) else (
  python serve.py
)
if errorlevel 1 pause
''')
            write_file(output / "README.md", f'''# {game.stem} HTML 包

本包用 QSPFoundation qSpider v1.3.1 執行原始 QSP 遊戲，並將來源 images/ 與 sound/ 素材放入遊戲資料目錄。它不會把 QSP 改寫成另一套遊戲程式，也不會修補原始 QSP 內的遊戲邏輯缺陷。

## 啟動

- macOS：雙擊 `Launch.command`。
- Windows：雙擊 `Launch.bat`。
- 兩者需要 Python 3，並經由啟動器開啟；直接雙擊 index.html 無法載入 WebAssembly。

## 素材

{image_source_note}
{patch_note}
{sound_note}
圖片與音效在同一磁碟時會以檔案硬連結加入輸出，無法硬連結時改為複製。qSpider 只讀取這些素材。

## 播放器授權

qSpider v1.3.1 standalone player 來源：{PLAYER_URL}
MIT License 全文見 `QSPIDER-LICENSE.txt`。
''')
        except Exception:
            shutil.rmtree(output)
            raise

    print(f"已建立：{output}")
    missing_by_kind, missing_references = audit_missing_media(
        game, output / "game" / "images", output / "game" / "sound"
    )
    missing_count = sum(missing_by_kind.values())
    breakdown = "、".join(f"{kind}{count}" for kind, count in missing_by_kind.items() if count)
    print(f"素材缺失：{missing_count} 個固定路徑（{breakdown}；{missing_references} 次引用）。")
    print("由變數或字串組合出的動態路徑不列入此數字。")
    serve_local_site(output)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, ValueError, zipfile.BadZipFile) as error:
        print(f"建置失敗：{error}", file=sys.stderr)
        raise SystemExit(1)
