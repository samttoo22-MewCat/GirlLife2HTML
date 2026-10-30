#!/usr/bin/env python3
"""Package one local Girl Life QSP release with the official qSpider web player."""

from __future__ import annotations

import hashlib
import gzip
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
from urllib.parse import urlsplit


PLAYER_URL = "https://github.com/QSPFoundation/qspider/releases/download/v1.3.1/qspider-player-standalone.zip"
PLAYER_SHA256 = "acedec0b5597c055559f95ac52a64518bf106d4dbef2262c1c8c7f4ec822a47c"
CLOUD_SAVE_API_URL = "https://girllife.mewcat.me/api"
CLOUD_SAVE_CACHE_VERSION = "girllife-cloud-17"
LOAD_PROGRESS_VERSION = "girllife-loading-progress-v4"
GZIP_QSP_HANDLER = '''class GzipQSPHandler(SimpleHTTPRequestHandler):
    def send_head(self):
        self._serve_qsp_gzip = False
        parsed = urlsplit(self.path)
        if parsed.path.lower().endswith(".qsp") and accepts_gzip(self.headers.get("Accept-Encoding", "")):
            original_path = self.path
            compressed_path = parsed.path + ".gz"
            original_file = Path(self.translate_path(parsed.path))
            if original_file.is_file() and Path(self.translate_path(compressed_path)).is_file():
                self._original_qsp_size = original_file.stat().st_size
                self.path = compressed_path + (f"?{parsed.query}" if parsed.query else "")
                self._serve_qsp_gzip = True
                try:
                    return super().send_head()
                finally:
                    self.path = original_path
        return super().send_head()

    def guess_type(self, path):
        if getattr(self, "_serve_qsp_gzip", False):
            return "application/octet-stream"
        return super().guess_type(path)

    def end_headers(self):
        if getattr(self, "_serve_qsp_gzip", False):
            self.send_header("Content-Encoding", "gzip")
            self.send_header("X-Original-Content-Length", str(self._original_qsp_size))
        super().end_headers()

def accepts_gzip(header):
    for item in header.split(","):
        coding, *parameters = item.strip().lower().split(";")
        if coding not in {"gzip", "*"}:
            continue
        try:
            if all(not parameter.strip().startswith("q=") or float(parameter.strip()[2:]) > 0 for parameter in parameters):
                return True
        except ValueError:
            continue
    return False
'''

LOAD_PROGRESS_STYLE = """
#girllife-download-progress {
  position: fixed; z-index: 2147483647; top: max(10px, env(safe-area-inset-top));
  left: 50%; transform: translateX(-50%); width: min(440px, calc(100vw - 24px));
  padding: 11px 14px; border-radius: 9px; background: rgb(22 22 28 / 94%);
  color: white; box-shadow: 0 3px 18px rgb(0 0 0 / 32%);
  font: 14px/1.4 system-ui, sans-serif; pointer-events: none;
}
#girllife-download-progress[hidden] { display: none; }
#girllife-download-label { display: block; overflow-wrap: anywhere; }
#girllife-download-meter { display: block; width: 100%; height: 10px; margin-top: 7px; accent-color: #7189ff; }
"""

LOAD_PROGRESS_MARKUP = r'''<div id="girllife-download-progress" data-version="__VERSION__" role="status" aria-live="polite">
  <span id="girllife-download-label">Preparing game…</span>
  <progress id="girllife-download-meter" max="100" value="0"></progress>
</div>
<script>
(() => {
  const panel = document.getElementById("girllife-download-progress");
  const label = document.getElementById("girllife-download-label");
  const meter = document.getElementById("girllife-download-meter");
  const requests = new Map();
  const nativeFetch = window.fetch.bind(window);
  let nextId = 0;
  let hasStartedRequest = false;
  let renderPending = false;
  let startedAt = 0;
  const gameReady = () => document.title !== "qSpider" || document.querySelector("qsp-main-content");
  const formatBytes = value => value < 1048576
    ? `${Math.max(1, Math.round(value / 1024))} KB`
    : `${(value / 1048576).toFixed(1)} MB`;
  const render = () => {
    const active = [...requests.values()];
    panel.hidden = active.length === 0 && hasStartedRequest && gameReady();
    if (!active.length) {
      meter.removeAttribute("value");
      label.textContent = hasStartedRequest ? "Preparing game… 0s" : "Preparing game…";
      return;
    }
    const loaded = active.reduce((sum, item) => sum + item.loaded, 0);
    const total = active.reduce((sum, item) => sum + item.total, 0);
    const resource = active.length === 1 ? active[0].name : `${active.length} game resources`;
    if (active.every(item => item.total > 0)) {
      const percent = Math.min(100, Math.floor(loaded * 100 / total));
      meter.max = total;
      meter.value = loaded;
      label.textContent = `Downloading ${resource} — ${percent}% (${formatBytes(loaded)} / ${formatBytes(total)})`;
    } else {
      meter.removeAttribute("value");
      label.textContent = `Downloading ${resource} — ${formatBytes(loaded)}`;
    }
  };
  const scheduleRender = () => {
    if (renderPending) return;
    renderPending = true;
    requestAnimationFrame(() => { renderPending = false; render(); });
  };
  window.girllifeTrackedFetch = async (requestUrl, originalUrl = requestUrl, init) => {
    const response = await nativeFetch(requestUrl, init);
    let path = "";
    try { path = new URL(typeof originalUrl === "string" ? originalUrl : originalUrl.url, location.href).pathname; } catch (_) {}
    if (!response.ok || !response.body || (!/\.qsp$/i.test(path) && !/\/(?:images|sound)\//i.test(path))) return response;
    const filename = decodeURIComponent(path.split("/").pop() || "game resource");
    const item = {name: filename, loaded: 0, total: Number(response.headers.get("X-Original-Content-Length") || response.headers.get("Content-Length")) || 0};
    const id = ++nextId;
    hasStartedRequest = true;
    startedAt = performance.now();
    requests.set(id, item);
    render();
    const reader = response.body.getReader();
    const stream = new ReadableStream({
      async pull(controller) {
        try {
          const chunk = await reader.read();
          if (chunk.done) { controller.close(); requests.delete(id); render(); return; }
          item.loaded += chunk.value.byteLength;
          scheduleRender();
          controller.enqueue(chunk.value);
        } catch (error) {
          controller.error(error);
          requests.delete(id);
          render();
        }
      },
      cancel(reason) { reader.cancel(reason); requests.delete(id); render(); }
    });
    return new Response(stream, {status: response.status, statusText: response.statusText, headers: response.headers});
  };
  window.fetch = (requestUrl, init) => window.girllifeTrackedFetch(requestUrl, requestUrl, init);
  const readyObserver = new MutationObserver(() => {
    if (gameReady()) { render(); readyObserver.disconnect(); clearInterval(ticker); }
  });
  readyObserver.observe(document.documentElement, {subtree: true, childList: true, characterData: true});
  const ticker = setInterval(() => {
    if (!hasStartedRequest || gameReady()) return;
    if (requests.size === 0) label.textContent = `Preparing game… ${Math.round((performance.now() - startedAt) / 1000)}s`;
  }, 1000);
})();
</script>'''.replace("__VERSION__", LOAD_PROGRESS_VERSION)

CLOUD_SAVE_UI_JS = r'''(() => {
  const MAX_PASSWORD_CHARACTERS = 1024;
  const dialog = document.createElement("dialog");
  dialog.className = "cloud-account-dialog";
  dialog.innerHTML = `
    <form novalidate>
      <h2 data-role="title">Cloud save account</h2>
      <p class="cloud-account-intro" data-role="intro">Cloud saves belong to an account. Sign in, or create one with a single password.</p>
      <div class="cloud-account-fields">
        <label>Account name<input name="username" autocomplete="username" spellcheck="false" maxlength="32" required></label>
        <label>Password<input name="password" type="password" autocomplete="current-password" required></label>
      </div>
      <p class="cloud-account-status" aria-live="polite"></p>
      <div class="cloud-account-actions">
        <button type="submit" value="login">Sign in</button>
        <button type="submit" value="create">Create account</button>
        <button type="button" data-role="signout" hidden>Sign out</button>
        <button type="button" data-role="close">Cancel</button>
      </div>
    </form>`;
  const style = document.createElement("style");
  style.textContent = `
    .cloud-account-dialog { width:min(92vw, 420px); border:0; border-radius:12px; padding:22px; color:#171717; background:#fff; }
    /* qSpider sets body{pointer-events:none} while the pause screen is open;
       the dialog lives in body, so it must opt back in to stay usable. */
    .cloud-account-dialog, .cloud-account-dialog * { pointer-events:auto; }
    .cloud-account-dialog::backdrop { background:rgb(0 0 0 / 65%); }
    .cloud-account-dialog h2 { margin:0 0 10px; font:600 1.3rem/1.3 system-ui,sans-serif; }
    .cloud-account-dialog .cloud-account-intro { margin:0 0 4px; color:#444; font:14px/1.45 system-ui,sans-serif; }
    .cloud-account-dialog label { display:block; margin:12px 0; font:500 1rem/1.4 system-ui,sans-serif; }
    .cloud-account-dialog input { display:block; box-sizing:border-box; width:100%; min-height:44px; margin-top:5px; padding:9px; font:16px system-ui,sans-serif; }
    .cloud-account-dialog .cloud-account-status { min-height:1.4em; margin:8px 0; color:#a11; font:14px/1.45 system-ui,sans-serif; }
    .cloud-account-dialog[data-mode="status"] .cloud-account-fields { display:none; }
    .cloud-account-dialog[data-mode="status"] .cloud-account-status { color:#164; }
    .cloud-account-actions { display:flex; flex-wrap:wrap; gap:8px; }
    .cloud-account-actions button { min-height:44px; padding:8px 12px; font:600 15px system-ui,sans-serif; }
    .cloud-account-actions button[hidden] { display:none; }
    .cloud-account-dialog[data-intent="register"] .cloud-account-actions button[value="create"] { order:-1; background:#146; color:#fff; border:1px solid #146; }
  `;
  document.head.append(style);
  document.body.append(dialog);
  const form = dialog.querySelector("form");
  const fields = dialog.querySelector(".cloud-account-fields");
  const status = dialog.querySelector(".cloud-account-status");
  const title = dialog.querySelector("[data-role='title']");
  const intro = dialog.querySelector("[data-role='intro']");
  const usernameInput = form.elements.username;
  const passwordInput = form.elements.password;
  const loginButton = dialog.querySelector('button[value="login"]');
  const createButton = dialog.querySelector('button[value="create"]');
  const closeButton = dialog.querySelector("button[data-role='close']");
  const signOutButton = dialog.querySelector("button[data-role='signout']");
  let finish = null;
  let request = null;
  let busy = false;

  function setIntent(intent) {
    const register = intent === "register";
    dialog.dataset.intent = register ? "register" : "signin";
    title.textContent = register ? "Create a cloud account" : "Cloud save account";
    intro.textContent = register
      ? "Loading and saving needs a cloud account. Choose a name and a password to create one; there is no email and no minimum password length. Already have an account? Use Sign in."
      : "Cloud saves belong to an account. Sign in, or create one with a single password.";
    createButton.textContent = "Create account";
    loginButton.textContent = "Sign in";
  }

  function setBusy(value) {
    busy = value;
    loginButton.disabled = value;
    createButton.disabled = value;
    usernameInput.readOnly = value;
    passwordInput.readOnly = value;
  }

  function settle(value) {
    const resolve = finish;
    finish = null;
    request = null;
    setBusy(false);
    if (dialog.open) dialog.close();
    resolve?.(value);
  }

  async function attempt(mode) {
    if (busy || !finish) return;
    const username = String(usernameInput.value || "").trim();
    const password = String(passwordInput.value || "");
    if (username.length < 3) {
      status.textContent = "Account names need 3 to 32 letters, numbers, dots, dashes, or underscores.";
      usernameInput.focus();
      return;
    }
    if (!password) {
      status.textContent = "Enter a password.";
      passwordInput.focus();
      return;
    }
    if (password.length > MAX_PASSWORD_CHARACTERS) {
      status.textContent = `Passwords must be ${MAX_PASSWORD_CHARACTERS} characters or fewer.`;
      passwordInput.focus();
      return;
    }
    if (typeof request !== "function") {
      settle({mode, username, password});
      return;
    }
    setBusy(true);
    status.textContent = mode === "create" ? "Creating the account…" : "Signing in…";
    let outcome = null;
    try {
      outcome = await request({mode, username, password});
    } catch (_) {
      outcome = {ok: false, error: "The cloud save service could not be reached."};
    }
    if (!finish) return;
    if (outcome && outcome.ok) {
      settle({mode, username, password});
      return;
    }
    setBusy(false);
    if (outcome && outcome.conflict && dialog.dataset.intent === "register") setIntent("signin");
    status.textContent = (outcome && outcome.error) || "Cloud account request failed.";
    passwordInput.focus();
    passwordInput.select();
  }

  form.addEventListener("submit", event => {
    event.preventDefault();
    attempt(event.submitter && event.submitter.value === "create" ? "create" : "login");
  });
  closeButton.addEventListener("click", () => settle(null));
  signOutButton.addEventListener("click", () => {
    window.girllifeCloudSignOut?.();
    settle({signedOut: true});
  });
  dialog.addEventListener("close", () => {
    if (finish) settle(null);
  });

  function openCredentials(options) {
    return new Promise(resolve => {
      request = options && typeof options.submit === "function" ? options.submit : null;
      finish = resolve;
      dialog.dataset.mode = "credentials";
      loginButton.hidden = false;
      createButton.hidden = false;
      signOutButton.hidden = true;
      closeButton.textContent = "Cancel";
      setIntent(options && options.register ? "register" : "signin");
      usernameInput.value = options && options.username ? String(options.username) : "";
      passwordInput.value = "";
      status.textContent = (options && options.message) || "";
      setBusy(false);
      if (!dialog.open) dialog.showModal();
      (usernameInput.value ? passwordInput : usernameInput).focus();
    });
  }

  function openStatus(info) {
    return new Promise(resolve => {
      request = null;
      finish = resolve;
      dialog.dataset.mode = "status";
      loginButton.hidden = true;
      createButton.hidden = true;
      signOutButton.hidden = false;
      closeButton.textContent = "Close";
      fields.querySelectorAll("input").forEach(input => { input.value = ""; });
      const name = info && info.username ? info.username : "your account";
      status.textContent = `Signed in as ${name}. Cloud saves are ready; this device stays signed in for 7 days.`;
      setBusy(false);
      if (!dialog.open) dialog.showModal();
      closeButton.focus();
    });
  }

  // Warm media already referenced by the current scene and visible actions.
  // Do not walk the full game asset tree: it contains tens of thousands of files.
  const prefetchedMedia = new Set();
  const mediaQueue = [];
  let mediaActive = 0;
  const mediaConcurrency = 6;
  function pumpMediaQueue() {
    while (mediaActive < mediaConcurrency && mediaQueue.length) {
      const item = mediaQueue.shift();
      mediaActive++;
      let settled = false;
      const finish = () => {
        if (settled) return;
        settled = true;
        mediaActive--;
        pumpMediaQueue();
      };
      if (item.kind === "video") {
        const video = document.createElement("video");
        video.preload = "metadata";
        video.onloadedmetadata = finish;
        video.onerror = finish;
        video.src = item.url;
        video.load();
        setTimeout(finish, 12000);
      } else {
        const image = new Image();
        image.decoding = "async";
        image.fetchPriority = item.priority || "low";
        image.onload = finish;
        image.onerror = finish;
        image.src = item.url;
        if (image.complete) finish();
        else setTimeout(finish, 12000);
      }
    }
  }
  function enqueueMedia(raw, kind, priority) {
    if (!raw || typeof raw !== "string") return;
    let url;
    try { url = new URL(raw, document.baseURI); } catch (_) { return; }
    if (url.origin !== location.origin || !/^https?:$/.test(url.protocol)) return;
    if (!/\.(?:avif|bmp|gif|jpe?g|png|webp|mp4|webm|ogv)(?:$|[?#])/i.test(url.href)) return;
    const key = `${kind}:${url.href}`;
    if (prefetchedMedia.has(key)) return;
    prefetchedMedia.add(key);
    mediaQueue.push({url: url.href, kind, priority});
    pumpMediaQueue();
  }
  function warmCurrentScene() {
    const roots = [
      document.querySelector("qsp-main-content"),
      document.querySelector("qsp-actions-list"),
      document.querySelector("qsp-objects-list"),
      document.querySelector("qsp-stats-content"),
    ].filter(Boolean);
    for (const root of roots) {
      root.querySelectorAll("img, video, source, [data-src], [data-lazy-src], [data-original], [data-poster], [data-image], [data-video], [data-media]").forEach(media => {
        const raw = media.currentSrc || media.src || media.getAttribute("poster") || media.getAttribute("src") ||
          media.getAttribute("data-src") || media.getAttribute("data-lazy-src") || media.getAttribute("data-original") ||
          media.getAttribute("data-poster") || media.getAttribute("data-image") || media.getAttribute("data-video") || media.getAttribute("data-media");
        const isVideo = media.tagName === "VIDEO" || media.tagName === "SOURCE" && (/video/i.test(media.type || "") || media.closest("video"));
        const priority = root.matches("qsp-main-content") ? "high" : "low";
        enqueueMedia(raw, isVideo ? "video" : "image", priority);
        const srcset = media.getAttribute("srcset");
        if (srcset) {
          for (const candidate of srcset.split(",")) enqueueMedia(candidate.trim().split(/\s+/)[0], "image", priority);
        }
      });
      root.querySelectorAll("[style*='url(']").forEach(element => {
        const match = getComputedStyle(element).backgroundImage.match(/url\([\"']?(.*?)[\"']?\)/i);
        if (match) enqueueMedia(match[1], "image", root.matches("qsp-main-content") ? "high" : "low");
      });
    }
  }
  let mediaWarmTimer = 0;
  const mediaObserver = new MutationObserver(() => {
    clearTimeout(mediaWarmTimer);
    mediaWarmTimer = setTimeout(warmCurrentScene, 100);
  });
  mediaObserver.observe(document.documentElement, {subtree: true, childList: true, attributes: true, attributeFilter: ["src", "poster", "style"]});
  warmCurrentScene();

  let outsideTouchPending = false;
  function dismissViewFromOutside(event, x, y, pointerType = "touch") {
    const view = document.querySelector("#portal-container .qsp-dialog-container qsp-view[modal]") ||
      document.querySelector("#portal-container qsp-view[modal]");
    if (view) {
      const media = view.querySelector("img, video");
      const rect = media && media.getBoundingClientRect();
      const inside = rect && x >= rect.left && x <= rect.right && y >= rect.top && y <= rect.bottom;
      if (inside) return false;
      const dialog = view.closest(".qsp-dialog-container");
      const backdrop = dialog && dialog.previousElementSibling &&
        dialog.previousElementSibling.matches(".qsp-overlay") ? dialog.previousElementSibling : null;
      if (!backdrop) return false;
      // Let Radix's own DismissableLayer handle an outside pointerdown. Escape
      // bubbles into the game-level pause handler and can close both dialogs.
      backdrop.dispatchEvent(new PointerEvent("pointerdown", {
        bubbles: true, cancelable: true, pointerType, pointerId: 1,
        isPrimary: true, button: 0, buttons: 1, clientX: x, clientY: y
      }));
      return true;
    }
    const target = event.target;
    const nonModalView = target && target.closest && target.closest("qsp-view");
    if (!nonModalView || nonModalView.hidden || getComputedStyle(nonModalView).display === "none") return false;
    if (target.closest("img, video, button, a, input, select, textarea")) return false;
    nonModalView.click();
    return true;
  }
  // The full-screen Radix content may be the hit target instead of its sibling
  // backdrop. Forward an outside pointerdown to that backdrop so Radix closes
  // only the topmost image layer (not an underlying pause panel).
  document.addEventListener("pointerdown", event => {
    if (event.pointerType === "mouse") {
      dismissViewFromOutside(event, event.clientX, event.clientY, "mouse");
    }
  }, true);
  document.addEventListener("pointerup", event => {
    if (event.pointerType !== "touch" || outsideTouchPending) return;
    outsideTouchPending = dismissViewFromOutside(event, event.clientX, event.clientY, "touch");
    if (outsideTouchPending) setTimeout(() => { outsideTouchPending = false; }, 800);
  }, true);
  document.addEventListener("touchend", event => {
    if (outsideTouchPending) return;
    const touch = event.changedTouches && event.changedTouches[0];
    if (touch) {
      outsideTouchPending = dismissViewFromOutside(event, touch.clientX, touch.clientY, "touch");
      if (outsideTouchPending) setTimeout(() => { outsideTouchPending = false; }, 800);
    }
  }, {capture: true, passive: false});

  document.addEventListener("click", event => {
    const button = event.target && event.target.closest && event.target.closest("[data-mobile-panel]");
    if (!button) return;
    const player = button.closest("qsp-player");
    if (!player) return;
    const selected = button.getAttribute("aria-pressed") !== "true";
    player.querySelectorAll("[data-mobile-panel]").forEach(item => {
      item.setAttribute("aria-pressed", item === button && selected ? "true" : "false");
    });
  });

  window.girllifeCloudCredentials = options => openCredentials(options || {});
  window.girllifeCloudAccountStatus = info => openStatus(info || {});
})();
'''

CLOUD_SAVE_PLAYER_JS = r'''/* GirlLife cloud saves v4 */
let girllifeCloudToken = null;
let girllifeCloudUser = "";
const girllifeCloudSessionKey = "girllife.cloud.session";
// A token restored from storage stays untested until the service confirms it.
let girllifeCloudRestored = false;
// True once this device has ever held an account, so a fresh visitor is pushed to register.
let girllifeCloudKnownAccount = false;
function girllifeCloudSignedIn() { return Boolean(girllifeCloudToken); }
function girllifeCloudWriteSession() {
  try {
    if (girllifeCloudToken) {
      window.localStorage.setItem(girllifeCloudSessionKey, JSON.stringify({token: girllifeCloudToken, username: girllifeCloudUser}));
    } else {
      window.localStorage.removeItem(girllifeCloudSessionKey);
    }
  } catch (_) {
    // Private browsing and blocked storage must not break cloud saves.
  }
}
function girllifeCloudForget() {
  girllifeCloudToken = null;
  girllifeCloudUser = "";
  girllifeCloudRestored = false;
  girllifeCloudWriteSession();
}
function girllifeCloudRemember(token, username) {
  girllifeCloudToken = token;
  girllifeCloudUser = username || "";
  girllifeCloudRestored = false;
  girllifeCloudKnownAccount = true;
  girllifeCloudWriteSession();
}
(function girllifeCloudRestore() {
  let saved = null;
  try {
    const raw = window.localStorage.getItem(girllifeCloudSessionKey);
    if (!raw) return;
    saved = JSON.parse(raw);
  } catch (_) {
    return;
  }
  if (!saved || typeof saved.token !== "string" || !saved.token) {
    try { window.localStorage.removeItem(girllifeCloudSessionKey); } catch (_) {}
    return;
  }
  girllifeCloudToken = saved.token;
  girllifeCloudUser = typeof saved.username === "string" ? saved.username : "";
  girllifeCloudRestored = true;
  girllifeCloudKnownAccount = true;
})();
async function girllifeCloudVerify() {
  try {
    const response = await fetch("__API_URL__/saves?game_id=" + encodeURIComponent("girllife-session-check"), {
      headers: {Authorization: "Bearer " + girllifeCloudToken}
    });
    if (response.status === 401) { girllifeCloudForget(); return false; }
    if (response.ok) { girllifeCloudRestored = false; return true; }
    return true;
  } catch (_) {
    // Offline: keep the remembered session and let the save request report the failure.
    return true;
  }
}
async function girllifeCloudAuthenticate(credentials) {
  const route = credentials.mode === "create" ? "/accounts" : "/sessions";
  let response;
  try {
    response = await fetch("__API_URL__" + route, {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({username: credentials.username, password: credentials.password})
    });
  } catch (_) { return {ok: false, error: "The cloud save service could not be reached."}; }
  const result = await response.json().catch(() => ({}));
  if (!response.ok) return {ok: false, error: result.error || "Cloud account request failed.", conflict: response.status === 409};
  girllifeCloudRemember(result.token, result.username || credentials.username);
  return {ok: true};
}
async function cloudEnsureSession() {
  if (girllifeCloudSignedIn() && !girllifeCloudRestored) return true;
  if (girllifeCloudSignedIn() && (await girllifeCloudVerify())) return true;
  const credentials = await window.girllifeCloudCredentials?.({
    submit: girllifeCloudAuthenticate,
    register: !girllifeCloudKnownAccount
  });
  return Boolean(credentials);
}
async function girllifeCloudAccount() {
  if (girllifeCloudSignedIn()) {
    const result = await window.girllifeCloudAccountStatus?.({username: girllifeCloudUser});
    if (result && result.signedOut) {
      girllifeCloudForget();
      return false;
    }
    return true;
  }
  return cloudEnsureSession();
}
async function girllifeOpenPausePanel(name) {
  if (name !== "saves") {
    isPauseScreenVisible$.value || openPauseScreen();
    pauseScreenCurrentPanel$.set(name);
    return;
  }
  if (!(await cloudEnsureSession())) {
    closePauseScreen();
    return;
  }
  await loadSaveList();
  isPauseScreenVisible$.value || openPauseScreen();
  pauseScreenCurrentPanel$.set(name);
}
window.girllifeCloudSignedIn = girllifeCloudSignedIn;
window.girllifeCloudAccount = girllifeCloudAccount;
window.girllifeCloudSignOut = girllifeCloudForget;
function cloudBase64(data) {
  const bytes = new Uint8Array(data);
  let binary = "";
  for (let i = 0; i < bytes.length; i += 32768) binary += String.fromCharCode(...bytes.subarray(i, i + 32768));
  return btoa(binary);
}
function cloudFromBase64(value) {
  const binary = atob(value), bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return bytes.buffer;
}
async function cloudRequest(method, game, identity, data, quiet) {
  const query = new URLSearchParams({game_id: game, ...identity});
  let response;
  try {
    response = await fetch("__API_URL__/saves?" + query, {
      method, headers: {Authorization: "Bearer " + girllifeCloudToken, ...(data ? {"Content-Type": "application/json"} : {})},
      ...(data ? {body: JSON.stringify({data: cloudBase64(data)})} : {})
    });
  } catch (_) { window.alert("The cloud save service could not be reached."); throw new Error("Cloud save service unavailable"); }
  if (response.status === 404) return null;
  if (response.status === 401) {
    girllifeCloudForget();
    // Background save-list reads must not interrupt play with a sign-in dialog.
    if (quiet) return null;
    if (await cloudEnsureSession()) return cloudRequest(method, game, identity, data, quiet);
    return null;
  }
  if (!response.ok) { const result = await response.json().catch(() => ({})); window.alert(result.error || "Cloud save request failed."); throw new Error(result.error || "Cloud save request failed."); }
  return response.status === 204 ? true : response.json();
}
async function cloudReady() { return await cloudEnsureSession(); }
WebStorage.prototype.saveBySlot = async function(game, slot, data) {
  if (await cloudReady()) await cloudRequest("PUT", game, {slot: String(slot)}, data);
};
WebStorage.prototype.saveByKey = async function(game, key, data) {
  if (await cloudReady()) await cloudRequest("PUT", game, {key}, data);
};
WebStorage.prototype.getSavedSlots = async function(game) {
  if (!girllifeCloudToken) return [];
  const rows = await cloudRequest("GET", game, {}, null, true);
  return (rows || []).filter(row => row.slot > 0 && !row.key).map(row => ({game_id: game, slot: row.slot, key: "", timestamp: row.timestamp}));
};
WebStorage.prototype.getNamedSaves = async function(game) {
  if (!girllifeCloudToken) return [];
  const rows = await cloudRequest("GET", game, {}, null, true);
  return (rows || []).filter(row => row.key).map(row => ({game_id: game, slot: -1, key: row.key, timestamp: row.timestamp}));
};
WebStorage.prototype.hasSaveBySlot = async function(game, slot) {
  if (!(await cloudReady())) return false;
  return (await cloudRequest("GET", game, {slot: String(slot)}, null)) !== null;
};
WebStorage.prototype.hasSaveByKey = async function(game, key) {
  if (!(await cloudReady())) return false;
  return (await cloudRequest("GET", game, {key}, null)) !== null;
};
WebStorage.prototype.getSaveDataBySlot = async function(game, slot) {
  if (!(await cloudReady())) return null;
  const result = await cloudRequest("GET", game, {slot: String(slot)}, null);
  return result ? cloudFromBase64(result.data) : null;
};
WebStorage.prototype.getSaveDataByKey = async function(game, key) {
  if (!(await cloudReady())) return null;
  const result = await cloudRequest("GET", game, {key}, null);
  return result ? cloudFromBase64(result.data) : null;
};
WebStorage.prototype.clearSaveSlot = async function(game, slot) {
  if (await cloudReady()) await cloudRequest("DELETE", game, {slot: String(slot)}, null);
};
WebStorage.prototype.clearSaveKey = async function(game, key) {
  if (await cloudReady()) await cloudRequest("DELETE", game, {key}, null);
};
'''

MOBILE_LAYOUT_CSS = """

/* Reflow the Aero interface for readable phone screens. */
.mobile-panel-control {
  display: none;
}

@media (max-width: 900px) {
  .aero-theme {
    position: relative;
    display: block;
    width: 100vw;
    height: 100dvh;
    overflow: hidden;
  }
  .main-block {
    width: 100vw;
    min-width: 0;
    max-width: 100vw;
    height: 100dvh;
    min-height: 0;
    max-height: 100dvh;
    background-size: contain;
  }
  .mobile-panel-control {
    display: block;
    position: fixed;
    z-index: 1300;
    inset: 0;
    pointer-events: none;
  }
  .mobile-panel-toggle {
    position: absolute;
    right: 8px;
    display: block;
    min-width: 76px;
    min-height: 44px;
    padding: 8px 12px;
    border: 1px solid var(--border-color, #777);
    border-radius: 6px;
    background: var(--background-color, #fff);
    color: var(--background-color-contrast, #111);
    font: 16px system-ui, sans-serif;
    pointer-events: auto;
    touch-action: manipulation;
  }
  .mobile-panel-toggle[data-mobile-panel="objects"] {
    top: max(8px, env(safe-area-inset-top));
  }
  .mobile-panel-toggle[data-mobile-panel="stats"] {
    bottom: max(8px, env(safe-area-inset-bottom));
  }
  .mobile-panel-toggle[aria-pressed="true"] {
    background: var(--background-color-inverted, #333);
    color: var(--background-color-inverted-contrast, #fff);
  }
  qsp-main {
    left: 0;
    top: 0;
    box-sizing: border-box;
    width: 100vw;
    height: 50dvh;
    padding-top: calc(56px + env(safe-area-inset-top));
    overflow: auto;
    background-size: 100% 100%;
  }
  qsp-main qsp-scrollable,
  qsp-actions qsp-scrollable,
  qsp-objects qsp-scrollable,
  qsp-stats qsp-scrollable {
    height: 100%;
  }
  qsp-main-content {
    font-size: clamp(16px, 4vw, 20px) !important;
    line-height: 1.45;
    overflow-wrap: anywhere;
  }
  qsp-main-content img,
  qsp-main-content video,
  qsp-view img,
  qsp-view video {
    display: block;
    max-width: 100%;
    max-height: 55dvh;
    width: auto;
    height: auto;
    object-fit: contain;
    margin-inline: auto;
  }
  qsp-view img,
  qsp-view video {
    max-width: calc(100vw - 24px);
    max-height: calc(100dvh - 48px);
  }
  qsp-view {
    position: fixed;
    inset: 0;
    left: 0;
    top: 0;
    width: 100vw;
    height: 100dvh;
    display: flex;
    align-items: center;
    justify-content: center;
    z-index: 900;
    background: rgb(0 0 0 / 55%);
    background-image: none !important;
    touch-action: manipulation;
    cursor: pointer;
  }
  qsp-view img,
  qsp-view video {
    position: relative;
    z-index: 1;
    pointer-events: auto;
  }
  .regular-actions {
    left: 0;
    right: 0;
    top: 50dvh;
    bottom: 0;
    box-sizing: border-box;
    width: 100vw;
    height: 50dvh;
    overflow: auto;
    background-size: 100% 100%;
  }
  .integrated-actions {
    width: 100%;
    height: auto;
    max-height: 30dvh;
    overflow: auto;
  }
  .action-item {
    min-height: 44px;
    padding: 8px 4px;
    font-size: 16px;
    line-height: 1.35;
    overflow-wrap: anywhere;
  }
  qsp-cmd {
    left: 8px;
    right: 8px;
    bottom: max(6px, env(safe-area-inset-bottom));
    top: auto;
    width: auto;
    height: 38px;
  }
  qsp-cmd input {
    font-size: 16px;
  }
  qsp-stats,
  qsp-objects {
    display: none !important;
    box-sizing: border-box;
    position: absolute;
    z-index: 100;
    left: 8px;
    right: 8px;
    top: 50dvh;
    bottom: 0;
    width: auto;
    height: auto;
    padding: 12px;
    overflow: auto;
    background: var(--background-color, #fff);
    box-shadow: 0 4px 20px rgb(0 0 0 / 35%);
  }
  .aero-theme:has(.mobile-panel-toggle[data-mobile-panel="objects"][aria-pressed="true"]) qsp-objects,
  .aero-theme:has(.mobile-panel-toggle[data-mobile-panel="stats"][aria-pressed="true"]) qsp-stats {
    display: block !important;
  }
  .aero-theme:has(.mobile-panel-toggle[data-mobile-panel="objects"][aria-pressed="true"]) qsp-objects {
    top: 0;
    bottom: 50dvh;
    padding-top: calc(56px + env(safe-area-inset-top));
  }
  .aero-theme:has(.mobile-panel-toggle[data-mobile-panel="stats"][aria-pressed="true"]) qsp-stats {
    top: 50dvh;
    bottom: 0;
    padding-bottom: calc(56px + env(safe-area-inset-bottom));
  }
  qsp-stats-content,
  qsp-objects-list {
    font-size: clamp(16px, 4vw, 20px);
    line-height: 1.45;
    overflow-wrap: anywhere;
  }
  .qsp-dialog-container {
    width: 100vw;
    height: 100dvh;
  }
  .qsp-dialog-container > * {
    max-width: calc(100vw - 24px);
    max-height: calc(100dvh - 24px);
  }
}
"""

MOBILE_COMMON_CSS = """

/* GirlLife mobile layout shared by qSpider Classic and Aero themes. */
.mobile-panel-control {
  display: none;
}

@media (max-width: 900px) {
  .mobile-panel-control {
    display: block;
    position: fixed;
    z-index: 1300;
    inset: 0;
    pointer-events: none;
  }
  .mobile-panel-toggle {
    position: absolute;
    right: 8px;
    display: block;
    min-width: 76px;
    min-height: 44px;
    padding: 8px 12px;
    border: 1px solid var(--border-color, #777);
    border-radius: 6px;
    background: var(--background-color, #fff);
    color: var(--background-color-contrast, #111);
    font: 16px system-ui, sans-serif;
    pointer-events: auto;
    touch-action: manipulation;
  }
  .mobile-panel-toggle[data-mobile-panel="objects"] {
    top: max(8px, env(safe-area-inset-top));
  }
  .mobile-panel-toggle[data-mobile-panel="stats"] {
    bottom: max(8px, env(safe-area-inset-bottom));
  }
  .mobile-panel-toggle[aria-pressed="true"] {
    background: var(--background-color-inverted, #333);
    color: var(--background-color-inverted-contrast, #fff);
  }
  qsp-player,
  qsp-cl,
  qsp-cl-layer,
  qsp-cl-dock,
  qsp-cl-pane {
    box-sizing: border-box;
    min-width: 0;
    max-width: 100vw;
  }
  qsp-cl-layer,
  qsp-cl-dock,
  qsp-cl-pane {
    min-height: 0;
  }
  qsp-cl-layer {
    height: 100%;
    overflow: hidden;
  }
  qsp-cl {
    position: relative;
    display: block;
    width: 100vw;
    height: 100dvh;
    overflow: hidden;
  }
  qsp-cl > qsp-cl-layer {
    position: absolute;
    inset: 0;
    width: 100%;
    height: 100%;
  }
  qsp-cl qsp-main > .panel-background {
    box-sizing: border-box;
    padding-top: calc(56px + env(safe-area-inset-top));
  }
  qsp-cl-layer:has(> qsp-cl-dock[data-place="bottom"] qsp-cl-pane > qsp-actions) {
    grid-template-rows: minmax(0, 1fr) minmax(0, 1fr) !important;
  }
  qsp-cl qsp-main,
  qsp-cl qsp-actions,
  qsp-cl qsp-stats,
  qsp-cl qsp-objects {
    min-width: 0;
    max-width: 100%;
    overflow: hidden;
  }
  qsp-player [data-radix-scroll-area-viewport] > div {
    display: block !important;
    box-sizing: border-box;
    width: 100% !important;
    min-width: 0 !important;
  }
  qsp-main-content,
  qsp-stats-content,
  qsp-objects-list {
    display: block;
    box-sizing: border-box;
    width: 100%;
    min-width: 0;
    max-width: 100%;
    overflow-wrap: anywhere;
  }
  qsp-main-content table.classic-table {
    width: max-content !important;
    min-width: max-content;
    max-width: none;
    margin-left: 0 !important;
    margin-right: 0 !important;
  }
  qsp-main-content center:has(> table.classic-table) {
    text-align: left !important;
  }
  qsp-main-content table.classic-table th,
  qsp-main-content table.classic-table td {
    overflow-wrap: normal;
    word-break: normal;
  }
  qsp-main-content,
  qsp-stats-content,
  qsp-objects-list,
  qsp-actions-list {
    font-size: clamp(16px, 4vw, 20px);
    line-height: 1.4;
  }
  qsp-main-content img,
  qsp-main-content video,
  qsp-stats-content img,
  qsp-objects-list img,
  .view-container img,
  qsp-view img,
  qsp-view video {
    display: block;
    box-sizing: border-box;
    max-width: min(100%, calc(100vw - 32px));
    max-height: 52dvh;
    width: auto;
    height: auto;
    object-fit: contain;
    margin-inline: auto;
  }
  qsp-player .action-item,
  #portal-container .action-item,
  qsp-player .qsp-button,
  #portal-container .qsp-button {
    box-sizing: border-box;
    min-height: 44px;
    padding: 8px 10px;
    font-size: 16px;
    line-height: 1.35;
    overflow-wrap: anywhere;
  }
  qsp-cmd input {
    box-sizing: border-box;
    min-height: 44px;
    font-size: 16px;
  }
  #portal-container .qsp-dialog-container > * {
    box-sizing: border-box;
    max-width: calc(100vw - 24px);
    max-height: calc(100dvh - 24px - env(safe-area-inset-top) - env(safe-area-inset-bottom));
  }
  #portal-container qsp-view .view-container {
    width: min(calc(100vw - 48px), 80vw);
    height: min(calc(100dvh - 48px - env(safe-area-inset-top) - env(safe-area-inset-bottom)), 80vh);
  }

  /* With side panels closed, let the main/actions panes use the full width.
     Items replaces the upper main pane; Stats replaces the lower actions pane. */
  qsp-player:has(.mobile-panel-control) qsp-cl-pane:has(> qsp-objects),
  qsp-player:has(.mobile-panel-control) qsp-cl-pane:has(> qsp-stats) {
    display: none !important;
  }
  qsp-player:has(.mobile-panel-toggle[data-mobile-panel="objects"][aria-pressed="true"]) qsp-cl-dock[data-place="right"] {
    display: block !important;
    position: fixed !important;
    z-index: 1200;
    top: 0;
    left: 8px;
    right: 8px;
    bottom: 50dvh;
    width: auto !important;
    height: auto !important;
    box-sizing: border-box;
    padding-top: calc(56px + env(safe-area-inset-top));
    overflow: auto;
    background: var(--background-color, #fff);
    box-shadow: 0 4px 20px rgb(0 0 0 / 35%);
  }
  qsp-player:has(.mobile-panel-toggle[data-mobile-panel="objects"][aria-pressed="true"]) qsp-cl-pane:has(> qsp-objects),
  qsp-player:has(.mobile-panel-toggle[data-mobile-panel="objects"][aria-pressed="true"]) qsp-objects {
    display: block !important;
    box-sizing: border-box;
    width: 100%;
    height: 100%;
  }
  qsp-player:has(.mobile-panel-toggle[data-mobile-panel="stats"][aria-pressed="true"]) qsp-cl-pane:has(> qsp-stats) {
    display: block !important;
    position: fixed !important;
    z-index: 1200;
    left: 8px;
    right: 8px;
    top: 50dvh;
    bottom: 0;
    width: auto !important;
    height: auto !important;
    box-sizing: border-box;
    padding-top: 0;
    padding-bottom: calc(56px + env(safe-area-inset-bottom));
    overflow: auto;
    background: var(--background-color, #fff);
    box-shadow: 0 4px 20px rgb(0 0 0 / 35%);
  }
  qsp-player:has(.mobile-panel-toggle[data-mobile-panel="stats"][aria-pressed="true"]) qsp-stats {
    display: block !important;
    width: 100%;
    height: 100%;
  }
}
"""

MOBILE_PANEL_CONTROL = '''<div class="mobile-panel-control" aria-label="Side panels">
  <button type="button" class="mobile-panel-toggle" data-mobile-panel="objects" aria-pressed="false">Items</button>
  <button type="button" class="mobile-panel-toggle" data-mobile-panel="stats" aria-pressed="false">Stats</button>
</div>'''

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


def apply_mobile_layout(output: Path) -> None:
    shared = output / "themes" / "common.css"
    if not shared.is_file():
        raise RuntimeError("qSpider common stylesheet is missing; cannot enable shared phone layout.")
    shared_css = shared.read_text(encoding="utf-8")
    common_marker = "/* GirlLife mobile layout shared by qSpider Classic and Aero themes. */"
    if common_marker in shared_css:
        shared_css = shared_css.split(common_marker, 1)[0].rstrip()
    shared.write_text(shared_css + MOBILE_COMMON_CSS, encoding="utf-8")

    stylesheet = output / "themes" / "aero.css"
    if not stylesheet.is_file():
        raise RuntimeError("qSpider Aero stylesheet is missing; cannot enable phone layout.")
    current = stylesheet.read_text(encoding="utf-8")
    legacy_marker = "/* Fit the fixed-coordinate Aero game canvas"
    if legacy_marker in current:
        current = current.split(legacy_marker, 1)[0].rstrip()
    mobile_marker = "/* Reflow the Aero interface for readable phone screens. */"
    if mobile_marker in current:
        current = current.split(mobile_marker, 1)[0].rstrip()
    old_control_rule = ".mobile-panel-control {\n  display: none;\n}"
    while current.endswith(old_control_rule):
        current = current[: -len(old_control_rule)].rstrip()
    stylesheet.write_text(current + MOBILE_LAYOUT_CSS, encoding="utf-8")

    theme = output / "themes" / "aero.html"
    if not theme.is_file():
        raise RuntimeError("qSpider Aero theme is missing; cannot add the phone panel menu.")
    markup = theme.read_text(encoding="utf-8")
    control_marker = '<div class="mobile-panel-control">'
    if control_marker not in markup:
        control = '''<div class="mobile-panel-control">
          <select class="mobile-panel-select" aria-label="Open a side panel">
            <option value="closed" selected>Side panel: closed</option>
            <option value="objects">Items</option>
            <option value="stats">Stats</option>
          </select>
        </div>
        '''
        markup = markup.replace('<div class="main-block">', control + '<div class="main-block">', 1)
    css_link = '<css-link src="qspider:themes/aero.css"></css-link>'
    versioned_css_link = f'<css-link src="qspider:themes/aero.css?v={CLOUD_SAVE_CACHE_VERSION}"></css-link>'
    markup = re.sub(
        r'<css-link src="qspider:themes/aero\.css(?:\?v=[^"]*)?"></css-link>',
        versioned_css_link,
        markup,
        count=1,
    )
    if css_link in markup:
        markup = markup.replace(css_link, versioned_css_link, 1)
    if markup != theme.read_text(encoding="utf-8"):
        theme.write_text(markup, encoding="utf-8")


def apply_cloud_saves(output: Path) -> None:
    bundle_files = list((output / "assets").glob("index-*.js"))
    if len(bundle_files) != 1:
        raise RuntimeError("Could not identify the qSpider player bundle for cloud saves.")
    bundle = bundle_files[0]
    source = bundle.read_text(encoding="utf-8")
    marker = "let instance$1=null;function getStorage()"
    if "__API_URL__" not in CLOUD_SAVE_PLAYER_JS:
        raise RuntimeError("Cloud-save API placeholder is missing from the player hook.")
    if '"' in CLOUD_SAVE_API_URL or "\\" in CLOUD_SAVE_API_URL:
        raise RuntimeError("Cloud-save API URL contains characters that need escaping.")
    player_patch = CLOUD_SAVE_PLAYER_JS.replace("__API_URL__", CLOUD_SAVE_API_URL)
    if '""http' in player_patch:
        raise RuntimeError("Cloud-save hook would inject malformed string quoting.")
    if "girllifeCloudToken" in source:
        start = source.find("let girllifeCloudToken = null;")
        end = source.find(marker, start)
        if start < 0 or end < start:
            raise RuntimeError("Could not update the existing qSpider cloud-save hook.")
        header_start = source.rfind("/* GirlLife cloud saves", 0, start)
        if header_start >= 0 and source.find("*/", header_start, start) >= 0:
            start = header_start
        source = source[:start] + player_patch + source[end:]
    else:
        if source.count(marker) != 1:
            raise RuntimeError("qSpider storage hook changed; cloud saves were not installed.")
        source = source.replace(marker, player_patch + marker, 1)
    panel_marker = 'isPauseScreenVisible$.value||openPauseScreen(),pauseScreenCurrentPanel$.set(_e)'
    panel_replacement = 'girllifeOpenPausePanel(_e)'
    if panel_replacement not in source:
        if source.count(panel_marker) != 1:
            raise RuntimeError("qSpider pause panel hook changed; cloud save gating was not installed.")
        source = source.replace(panel_marker, panel_replacement, 1)
    command_marker = 'case"quit":stopCurrentGame();break;'
    cloud_command = 'case"cloud-account":girllifeCloudAccount().then(ok=>{if(ok)loadSaveList();else closePauseScreen()});break;'
    if cloud_command not in source:
        existing = re.search(r'case"cloud-account":[^\n]*?break;', source)
        if existing is not None:
            source = source.replace(existing.group(0), cloud_command, 1)
        elif source.count(command_marker) == 1:
            source = source.replace(command_marker, cloud_command + command_marker, 1)
        else:
            raise RuntimeError("qSpider command hook changed; cloud account access was not installed.")
    bundle.write_text(source, encoding="utf-8")

    index = output / "index.html"
    markup = index.read_text(encoding="utf-8")
    original_markup = markup
    script = f'<script src="./cloud-saves-ui.js?v={CLOUD_SAVE_CACHE_VERSION}" defer></script>'
    if CLOUD_SAVE_CACHE_VERSION not in markup:
        pattern = r'(<script type="module" crossorigin src="[^"]+?\.js)(?:\?v=[^"]*)?(">)'
        if len(re.findall(pattern, markup)) != 1:
            raise RuntimeError("Could not refresh the cached qSpider player bundle URL.")
        markup = re.sub(pattern, rf'\1?v={CLOUD_SAVE_CACHE_VERSION}\2', markup, count=1)
    if script not in markup:
        ui_pattern = r'<script src="\./cloud-saves-ui\.js(?:\?v=[^"]*)?" defer></script>'
        if re.search(ui_pattern, markup):
            markup = re.sub(ui_pattern, script, markup, count=1)
        elif markup.count("</head>") == 1:
            markup = markup.replace("</head>", f"    {script}\n  </head>", 1)
        else:
            raise RuntimeError("Could not add the cloud account dialog to the player page.")
    if markup != original_markup:
        index.write_text(markup, encoding="utf-8")

    readme = output / "README.md"
    if readme.is_file():
        contents = readme.read_text(encoding="utf-8")
        if "## 下載進度" not in contents and "## 素材\n" in contents:
            progress_note = (
                "## 下載進度\n\n"
                "遊戲本體或素材正在下載時，頁面會顯示下載進度；圖片、影片和音效只在遊戲要求時才由瀏覽器下載。"
                "建置器會建立可重用的 QSP gzip 壓縮副本，支援的伺服器會自動使用，原始 `.qsp` 保持不變。\n\n"
            )
            readme.write_text(contents.replace("## 素材\n", progress_note + "## 素材\n", 1), encoding="utf-8")
    ui = output / "cloud-saves-ui.js"
    if not ui.is_file() or ui.read_text(encoding="utf-8") != CLOUD_SAVE_UI_JS:
        write_file(ui, CLOUD_SAVE_UI_JS)

    for theme_name in ("aero.html", "classic.html"):
        theme = output / "themes" / theme_name
        if not theme.is_file():
            continue
        markup = theme.read_text(encoding="utf-8")
        common_link = f'<css-link src="qspider:themes/common.css?v={CLOUD_SAVE_CACHE_VERSION}"></css-link>'
        markup = re.sub(
            r'<css-link src="qspider:themes/common\.css(?:\?v=[^"]*)?"></css-link>',
            common_link,
            markup,
            count=1,
        )
        if 'class="mobile-panel-control"' in markup:
            markup = re.sub(
                r'<div class="mobile-panel-control"[^>]*>.*?</div>',
                MOBILE_PANEL_CONTROL,
                markup,
                count=1,
                flags=re.DOTALL,
            )
        else:
            anchor = "<qsp-player>"
            if markup.count(anchor) != 1:
                raise RuntimeError(f"Could not add the phone panel menu to {theme_name}.")
            markup = markup.replace(anchor, anchor + "\n      " + MOBILE_PANEL_CONTROL, 1)
        if theme_name == "classic.html":
            markup = re.sub(
                r'<css-link src="qspider:themes/classic\.css(?:\?v=[^"]*)?"></css-link>',
                f'<css-link src="qspider:themes/classic.css?v={CLOUD_SAVE_CACHE_VERSION}"></css-link>',
                markup,
                count=1,
            )
        if markup != theme.read_text(encoding="utf-8"):
            theme.write_text(markup, encoding="utf-8")

    for theme_name in ("aero.html", "classic.html"):
        theme = output / "themes" / theme_name
        if not theme.is_file():
            continue
        markup = theme.read_text(encoding="utf-8")
        marker = '<qsp-pause-screen-panel name="saves">'
        button = '<button qsp-command="cloud-account" class="qsp-button">Cloud account</button>'
        legacy_button = '<button qsp-command="cloud-account" class="qsp-button">Cloud account / Sign in</button>'
        if legacy_button in markup:
            markup = markup.replace(legacy_button, button, 1)
            theme.write_text(markup, encoding="utf-8")
        if button not in markup:
            if markup.count(marker) != 1:
                raise RuntimeError(f"Could not add cloud account access to {theme_name}.")
            markup = markup.replace(marker, marker + "\n              " + button, 1)
            theme.write_text(markup, encoding="utf-8")


def apply_loading_progress(output: Path) -> None:
    bundle_files = list((output / "assets").glob("index-*.js"))
    if len(bundle_files) != 1:
        raise RuntimeError("Could not identify the qSpider player bundle for download progress.")
    bundle = bundle_files[0]
    source = bundle.read_text(encoding="utf-8")
    original = "function fetchProxyFallback($){return fetch($).catch(()=>fetch(PROXY_URL+window.btoa($)))}"
    replacement = "function fetchProxyFallback($){return window.girllifeTrackedFetch($,$).catch(()=>window.girllifeTrackedFetch(PROXY_URL+window.btoa($),$))}"
    if replacement in source:
        source = source.replace(replacement, original, 1)
        bundle.write_text(source, encoding="utf-8")

    index = output / "index.html"
    markup = index.read_text(encoding="utf-8")
    original_markup = markup
    style_marker = "/* GirlLife download progress */"
    if style_marker not in markup:
        if markup.count("</head>") != 1:
            raise RuntimeError("Could not add download progress styling to the player page.")
        markup = markup.replace(
            "</head>",
            f"<style>{style_marker}{LOAD_PROGRESS_STYLE}</style>\n  </head>",
            1,
        )
    if LOAD_PROGRESS_VERSION not in markup and 'id="girllife-download-progress"' in markup:
        start = markup.find('<div id="girllife-download-progress"')
        end = markup.find("</script>", start)
        if start < 0 or end < start:
            raise RuntimeError("Could not update the existing download progress bar.")
        markup = markup[:start] + LOAD_PROGRESS_MARKUP + markup[end + len("</script>"):]
    elif 'id="girllife-download-progress"' not in markup:
        if markup.count("<body>") != 1:
            raise RuntimeError("Could not add the download progress bar to the player page.")
        markup = markup.replace("<body>", "<body>\n    " + LOAD_PROGRESS_MARKUP, 1)
    if markup != original_markup:
        index.write_text(markup, encoding="utf-8")


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


class GzipQSPHandler(SimpleHTTPRequestHandler):
    def send_head(self):
        self._serve_qsp_gzip = False
        parsed = urlsplit(self.path)
        if parsed.path.lower().endswith(".qsp") and accepts_gzip(self.headers.get("Accept-Encoding", "")):
            original_path = self.path
            compressed_path = parsed.path + ".gz"
            original_file = Path(self.translate_path(parsed.path))
            if original_file.is_file() and Path(self.translate_path(compressed_path)).is_file():
                self._original_qsp_size = original_file.stat().st_size
                self.path = compressed_path + (f"?{parsed.query}" if parsed.query else "")
                self._serve_qsp_gzip = True
                try:
                    return super().send_head()
                finally:
                    self.path = original_path
        return super().send_head()

    def guess_type(self, path):
        if getattr(self, "_serve_qsp_gzip", False):
            return "application/octet-stream"
        return super().guess_type(path)

    def end_headers(self):
        if getattr(self, "_serve_qsp_gzip", False):
            self.send_header("Content-Encoding", "gzip")
            self.send_header("X-Original-Content-Length", str(self._original_qsp_size))
        super().end_headers()


def accepts_gzip(header: str) -> bool:
    for item in header.split(","):
        coding, *parameters = item.strip().lower().split(";")
        if coding not in {"gzip", "*"}:
            continue
        try:
            if all(not parameter.strip().startswith("q=") or float(parameter.strip()[2:]) > 0 for parameter in parameters):
                return True
        except ValueError:
            continue
    return False


def ensure_compressed_qsp(output: Path) -> None:
    for game in (output / "game").glob("*.qsp"):
        compressed = game.with_suffix(game.suffix + ".gz")
        if compressed.is_file() and compressed.stat().st_mtime_ns >= game.stat().st_mtime_ns:
            continue
        temporary = compressed.with_suffix(compressed.suffix + ".tmp")
        try:
            with game.open("rb") as source, temporary.open("wb") as raw_target:
                with gzip.GzipFile(filename="", mode="wb", compresslevel=6, fileobj=raw_target, mtime=0) as target:
                    shutil.copyfileobj(source, target, length=1024 * 1024)
            os.replace(temporary, compressed)
        finally:
            temporary.unlink(missing_ok=True)


def apply_gzip_qsp_server(output: Path) -> None:
    script = output / "serve.py"
    source = script.read_text(encoding="utf-8")
    if "class GzipQSPHandler" not in source:
        source = source.replace("from pathlib import Path\n", "from pathlib import Path\nfrom urllib.parse import urlsplit\n", 1)
        source = source.replace("root = Path(__file__).resolve().parent", GZIP_QSP_HANDLER + "\nroot = Path(__file__).resolve().parent", 1)
        source = source.replace("handler = partial(SimpleHTTPRequestHandler,", "handler = partial(GzipQSPHandler,", 1)
        script.write_text(source, encoding="utf-8")
    readme = output / "README.md"
    if readme.is_file():
        contents = readme.read_text(encoding="utf-8")
        old = "遊戲本體或素材正在下載時，頁面會顯示下載進度；圖片、影片和音效只在遊戲要求時才由瀏覽器下載。"
        new = old + "建置器會建立可重用的 QSP gzip 壓縮副本，支援的伺服器會自動使用，原始 `.qsp` 保持不變。"
        if old in contents and new not in contents:
            readme.write_text(contents.replace(old, new, 1), encoding="utf-8")


def serve_local_site(output: Path) -> None:
    handler = partial(GzipQSPHandler, directory=str(output))
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
        apply_mobile_layout(output)
        apply_cloud_saves(output)
        apply_loading_progress(output)
        ensure_compressed_qsp(output)
        apply_gzip_qsp_server(output)
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
            apply_mobile_layout(output)
            apply_cloud_saves(output)
            apply_loading_progress(output)
            game_dir = output / "game"
            game_dir.mkdir(exist_ok=True)
            shutil.copy2(game, game_dir / game.name)
            ensure_compressed_qsp(output)
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
from urllib.parse import urlsplit
import webbrowser

class GzipQSPHandler(SimpleHTTPRequestHandler):
    def send_head(self):
        self._serve_qsp_gzip = False
        parsed = urlsplit(self.path)
        if parsed.path.lower().endswith(".qsp") and "gzip" in self.headers.get("Accept-Encoding", "").lower():
            original_path = self.path
            compressed_path = parsed.path + ".gz"
            original_file = Path(self.translate_path(parsed.path))
            if original_file.is_file() and Path(self.translate_path(compressed_path)).is_file():
                self._original_qsp_size = original_file.stat().st_size
                self.path = compressed_path + (f"?{parsed.query}" if parsed.query else "")
                self._serve_qsp_gzip = True
                try:
                    return super().send_head()
                finally:
                    self.path = original_path
        return super().send_head()

    def guess_type(self, path):
        if getattr(self, "_serve_qsp_gzip", False):
            return "application/octet-stream"
        return super().guess_type(path)

    def end_headers(self):
        if getattr(self, "_serve_qsp_gzip", False):
            self.send_header("Content-Encoding", "gzip")
            self.send_header("X-Original-Content-Length", str(self._original_qsp_size))
        super().end_headers()

root = Path(__file__).resolve().parent
handler = partial(GzipQSPHandler, directory=str(root))
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
            apply_gzip_qsp_server(output)
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
圖片與音效在同一磁碟時會以檔案硬連結加入輸出，無法硬連結時改為複製。qSpider 只會在遊戲要求圖片、影片或音效時下載該項素材，不會預先下載整個素材庫。伺服器支援 gzip 時會傳送預壓縮的 QSP，原始 `.qsp` 保留不變。

## 下載進度

遊戲本體或素材正在下載時，頁面會顯示下載進度；網頁初次啟動仍需完整載入 QSP 遊戲本體。建置時會建立可重用的 `.qsp.gz`，支援 gzip 的伺服器傳輸量較小。

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
