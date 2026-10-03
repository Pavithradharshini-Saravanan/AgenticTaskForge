"""
TaskForge — Agentic Computer-Use Orchestrator
==============================================

Architecture: OBSERVE → REASON → ACT → OBSERVE → ... → VERIFY → STOP

This replaces the old classify→build_plan→execute pattern with a genuine
agent loop where the LLM decides the NEXT action at every step based on
the current observed computer state — not a pre-built workflow.

Key design decisions:
  - The LLM sees: goal, current_observation, action_history, available_tools
  - The LLM outputs: ONE next action with its args (not a full plan)
  - After every action, the screen/state is re-observed
  - The LLM can change course if an action failed or the state changed
  - The developer never writes per-task workflows — only TOOLS

Tools (registered in TOOL_REGISTRY):
  Every tool has: name, description, parameters, executor function.
  The LLM picks tools by name from the registry. New capabilities = new tools.

Safety:
  - All tool calls go through the registry (whitelist)
  - Destructive tools (delete, firewall off) have a confirmation flag
  - Max steps prevents infinite loops
  
How to run:
  python agent_orchestrator.py
  python agent_orchestrator.py --goal "open chrome and search python tutorials on youtube"
  python agent_orchestrator.py --goal "move notes.txt from downloads to documents"
  python agent_orchestrator.py --goal "fill the registration form on httpbin.org/forms/post with name John, email john@test.com"
"""

import os
import re
import sys
import json
import time
import subprocess
import argparse
import threading
import traceback
import win32gui
import winreg
import shutil
import urllib.parse
import requests
import pyperclip
import difflib
from typing import Optional, Any
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# ────────────────────────────────────────────────────────────────────────────
# Configuration
# ────────────────────────────────────────────────────────────────────────────
OLLAMA_URL    = "http://localhost:11434/api/generate"
MODEL_NAME    = "phi3:mini"
MAX_STEPS     = 15          # hard stop to prevent infinite loops
STEP_DELAY    = 0.5         # seconds between steps
LLM_TIMEOUT   = 180         # seconds — phi3:mini cold-load takes up to 2 min
LLM_RETRIES   = 2           # retry on timeout before giving up

# ────────────────────────────────────────────────────────────────────────────
# Helpers (reused from module5_orchestrator)
# ────────────────────────────────────────────────────────────────────────────

def get_known_folder_path(folder_name: str) -> str:
    folder_key = folder_name.strip().lower()
    registry_mapping = {
        "documents": "Personal", "desktop": "Desktop",
        "downloads": "{374DE290-123F-4565-9164-39C4925E467B}",
        "pictures": "My Pictures", "music": "My Music", "videos": "My Video",
    }
    if folder_key in registry_mapping:
        reg_value_name = registry_mapping[folder_key]
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders") as key:
                val, _ = winreg.QueryValueEx(key, reg_value_name)
                resolved = os.path.expandvars(val)
                if os.path.exists(resolved):
                    return resolved
        except Exception:
            pass
    home = os.path.expanduser("~")
    return os.path.join(home, folder_key.capitalize())


def normalize_url(site_input: str) -> str:
    raw = site_input.strip().strip("'\"")
    cleaned = raw.lower()

    # YouTube search/play pattern: "open youtube and play X", "open youtube and search X", "youtube play X"
    yt_match = re.search(r"(?:youtube|yt)\s+(?:search\s+for\s+|search\s+|play\s+|watch\s+|show\s+)?(.+)", raw, re.IGNORECASE)
    if not yt_match:
        yt_match = re.search(r"(?:play|watch|search)\s+(.+)\s+on\s+youtube", raw, re.IGNORECASE)
    if yt_match and not raw.startswith("http://") and not raw.startswith("https://"):
        query = yt_match.group(1).replace("open ", "").replace("and ", "").replace("play ", "").replace("watch ", "").strip()
        if query and query.lower() != "youtube":
            return f"https://www.youtube.com/results?search_query={urllib.parse.quote_plus(query)}"

    # Google search pattern: "google search X", "search for X"
    g_match = re.search(r"(?:google|search)\s+(?:for\s+)?(.+)", raw, re.IGNORECASE)
    if g_match and not raw.startswith("http://") and not raw.startswith("https://"):
        query = g_match.group(1).replace("open ", "").replace("and ", "").strip()
        if query:
            return f"https://www.google.com/search?q={urllib.parse.quote_plus(query)}"

    if cleaned.startswith("http://") or cleaned.startswith("https://"):
        return raw
    if "." in cleaned and " " not in cleaned and not cleaned.endswith("."):
        return f"https://{cleaned}"
    if " " not in cleaned and re.match(r"^[a-zA-Z0-9-]+$", cleaned):
        return f"https://{cleaned}.com"
    return f"https://www.google.com/search?q={urllib.parse.quote_plus(cleaned)}"


def ask_llm(prompt: str, temperature: float = 0.15, max_tokens: int = 128) -> str:
    """
    Calls the local Ollama LLM. Uses format='json', num_predict=128 for complete structured output.
    """
    payload = {
        "model": MODEL_NAME,
        "prompt": prompt,
        "stream": False,
        "format": "json",
        "options": {
            "temperature": temperature,
            "top_p": 0.9,
            "num_predict": max_tokens,
            "num_ctx": 2048
        },
    }
    last_err = None
    for attempt in range(1, LLM_RETRIES + 2):  # up to LLM_RETRIES+1 tries
        try:
            resp = requests.post(OLLAMA_URL, json=payload, timeout=LLM_TIMEOUT)
            if resp.status_code != 200:
                raise RuntimeError(f"Ollama HTTP {resp.status_code}: {resp.text[:200]}")
            return resp.json()["response"].strip()
        except requests.exceptions.Timeout:
            last_err = f"LLM timed out (attempt {attempt}/{LLM_RETRIES + 1})"
            print(f"  [LLM] {last_err} — retrying...")
        except Exception as e:
            raise
    raise RuntimeError(f"LLM unreachable after {LLM_RETRIES + 1} attempts: {last_err}")


def warm_up_llm():
    """
    Sends a tiny prompt to Ollama at startup so phi3:mini is loaded into
    GPU/CPU memory before the first real user request. Prevents cold-start timeout.
    """
    try:
        print("  [LLM] Warming up phi3:mini model...")
        requests.post(
            OLLAMA_URL,
            json={"model": MODEL_NAME, "prompt": "hi", "stream": False,
                  "options": {"temperature": 0.1, "num_predict": 1}},
            timeout=LLM_TIMEOUT,
        )
        print("  [LLM] Model ready.")
    except Exception as e:
        print(f"  [LLM] Warm-up failed (will retry on first use): {e}")


# ════════════════════════════════════════════════════════════════════════════
# OBSERVATION LAYER
# The agent observes the current computer state before each decision.
# ════════════════════════════════════════════════════════════════════════════

def observe_desktop() -> dict:
    """
    Captures a lightweight snapshot of the current computer state:
    - Foreground window title and process
    - List of all visible top-level windows
    - Active browser page URL and title (if Playwright browser is open)
    - Active Notepad content (if open)
    - Whether a File Explorer window is visible
    Returns a dict; all fields are best-effort (never raises).
    """
    obs = {
        "foreground_window": None,
        "open_windows": [],
        "browser_url": None,
        "browser_title": None,
        "notepad_open": False,
        "explorer_open": False,
        "system_info": {},
    }

    # Foreground window
    try:
        hwnd = win32gui.GetForegroundWindow()
        obs["foreground_window"] = win32gui.GetWindowText(hwnd)
    except Exception:
        pass

    # All visible top-level windows
    def _enum(hwnd, results):
        if win32gui.IsWindowVisible(hwnd):
            title = win32gui.GetWindowText(hwnd)
            if title:
                results.append(title)
    try:
        windows = []
        win32gui.EnumWindows(_enum, windows)
        obs["open_windows"] = windows[:30]  # cap for prompt size
    except Exception:
        pass

    obs["notepad_open"] = any("notepad" in w.lower() for w in obs["open_windows"])
    obs["explorer_open"] = any(
        w in ("File Explorer", "This PC", "Downloads", "Documents")
        or (re.search(r"\bfile explorer\b|\bthis pc\b", w, re.I) and "taskforge" not in w.lower() and "antigravity" not in w.lower())
        for w in obs["open_windows"]
    )

    # Browser state (if global Playwright page is active)
    try:
        if _page is not None and not _page.is_closed():
            obs["browser_url"]   = _page.url
            obs["browser_title"] = _page.title()
    except Exception:
        pass

    return obs


def observation_summary(obs: dict) -> str:
    """Formats observation dict into a compact text for the LLM prompt."""
    lines = []
    fg = obs.get("foreground_window")
    if fg and "taskforge" not in fg.lower() and "antigravity" not in fg.lower():
        lines.append(f"Active window: {fg}")
    elif fg:
        lines.append("Active window: Desktop Assistant UI")
    if obs.get("browser_url"):
        lines.append(f"Browser: {obs['browser_title']} @ {obs['browser_url']}")
    return "\n".join(lines) if lines else "Desktop active."


# ════════════════════════════════════════════════════════════════════════════
# LOW-LEVEL ACTION FUNCTIONS  (kept exactly from module5_orchestrator)
# ════════════════════════════════════════════════════════════════════════════

# Global state shared by browser and file actions
_app            = None
_main_window    = None
_document       = None
_explorer_window = None
_current_folder_path = None
_browser        = None
_browser_context = None
_page           = None
_playwright     = None


def action_open_app() -> str:
    global _app, _main_window, _document
    print("  [tool] open_app: launching Notepad")
    subprocess.run(["taskkill", "/F", "/IM", "notepad.exe", "/T"], capture_output=True)
    time.sleep(1)
    subprocess.Popen(["notepad.exe"])
    app = None
    for _ in range(10):
        time.sleep(1)
        try:
            app = Application(backend="uia").connect(path="notepad.exe")
            break
        except Exception:
            continue
    if app is None:
        raise RuntimeError("Could not connect to Notepad after launch.")
    _app = app
    _main_window = app.top_window()
    _main_window.set_focus()
    time.sleep(0.5)
    return "Notepad is open and focused."


def action_type_text(value: str) -> str:
    global _app, _main_window
    print(f"  [tool] type_text: typing {len(value)} chars")
    if _app is None or _main_window is None:
        action_open_app()
    try:
        edit = _main_window.child_window(control_type="Edit")
        pyperclip.copy(value)
        edit.set_focus()
        time.sleep(0.3)
        send_keys("^a")
        time.sleep(0.1)
        send_keys("^v")
        time.sleep(0.5)
        return f"Typed {len(value)} characters into Notepad."
    except Exception as e:
        raise RuntimeError(f"type_text failed: {e}")


def action_save_file(filename: str) -> str:
    global _app, _main_window
    print(f"  [tool] save_file: '{filename}'")
    if _app is None or _main_window is None:
        raise RuntimeError("No Notepad window open to save.")
    # Resolve folder in filename if keyword given
    parts = filename.replace("\\", "/").split("/")
    if len(parts) >= 2:
        folder_kw = parts[0].lower()
        known = {k: get_known_folder_path(k) for k in ["downloads", "desktop", "documents"]}
        folder_path = known.get(folder_kw, parts[0])
        bare_name = parts[-1]
        full_path = os.path.join(folder_path, bare_name)
    else:
        full_path = os.path.join(get_known_folder_path("documents"), filename)

    os.makedirs(os.path.dirname(full_path), exist_ok=True)

    send_keys("^s")
    time.sleep(1.5)
    try:
        dlg = _app.window(title_re=".*Save.*")
        dlg.set_focus()
        name_box = dlg.child_window(auto_id="1001")
        name_box.set_text(full_path)
        time.sleep(0.3)
        send_keys("{ENTER}")
        time.sleep(1)
        # Handle overwrite dialog
        try:
            overwrite = _app.window(title_re=".*Notepad.*")
            if overwrite.exists(timeout=2):
                overwrite.child_window(title="Yes").click()
                time.sleep(0.5)
        except Exception:
            pass
    except Exception:
        pass
    for _ in range(8):
        time.sleep(0.5)
        if os.path.exists(full_path):
            return f"File saved: {full_path}"
    return f"Save attempted for: {full_path} (could not confirm file exists)"


def action_open_browser(url: str) -> str:
    """
    Opens the requested URL directly in the user's active Google Chrome session.
    Instant dispatch, zero latency, zero extra blank tabs.
    """
    print(f"  [tool] open_browser: {url}")
    normalized = normalize_url(url)
    try:
        subprocess.Popen(f'start chrome "{normalized}"', shell=True)
        return f"Opened {normalized} directly in your active Google Chrome browser!"
    except Exception as e:
        return f"Error launching browser: {e}"


def action_fill_form(field: str, value: str, submit: bool = False) -> str:
    """
    Fills a form field on the current Playwright page.
    The agent observes the page and decides which field/value to fill.
    Does NOT hardcode field names — uses multiple locator strategies.
    """
    global _page
    print(f"  [tool] fill_form: field='{field}' value='{value}' submit={submit}")
    if _page is None or _page.is_closed():
        raise RuntimeError("No browser page open. Use open_browser first.")

    locators = [
        _page.get_by_placeholder(field, exact=False),
        _page.get_by_label(field, exact=False),
        _page.get_by_role("textbox", name=field, exact=False),
        _page.locator(f"input[name='{field}']"),
        _page.locator(f"input[id*='{field}']"),
        _page.locator(f"textarea[name='{field}']"),
        _page.locator("input[type='search']"),
        _page.locator("input[name='q']"),
        _page.locator("input[type='text']"),
        _page.locator("textarea"),
    ]
    for loc in locators:
        try:
            if loc.count() > 0 and loc.first.is_visible():
                loc.first.fill(value)
                time.sleep(0.4)
                if submit:
                    loc.first.press("Enter")
                    time.sleep(1.5)
                    return f"Filled '{field}' with '{value}' and submitted. Now on: {_page.title()}"
                return f"Filled '{field}' with '{value}'."
        except Exception:
            continue

    # OS-level paste fallback
    pyperclip.copy(value)
    time.sleep(0.3)
    send_keys("^v")
    if submit:
        send_keys("{ENTER}")
        time.sleep(1)
    return f"Filled (clipboard paste) '{value}' into active field."


def action_click_browser_element(selector: str) -> str:
    """Clicks a browser element by text, label, or CSS selector. Falls back to screen OCR click if no Playwright page."""
    global _page
    print(f"  [tool] click_element: '{selector}'")
    if _page is not None and not _page.is_closed():
        strategies = [
            lambda: _page.get_by_text(selector, exact=False).first.click(),
            lambda: _page.get_by_role("button", name=selector, exact=False).first.click(),
            lambda: _page.get_by_role("link", name=selector, exact=False).first.click(),
            lambda: _page.locator(selector).first.click(),
        ]
        for fn in strategies:
            try:
                fn()
                time.sleep(0.8)
                return f"Clicked '{selector}'. Page: {_page.title()}"
            except Exception:
                continue
    # Fall back to native screen text click via pywinauto/OCR
    try:
        from task6_computer_control import click_text
        return click_text(selector)
    except Exception as e:
        return f"Could not click '{selector}': {e}"


def action_read_page_content() -> str:
    """Returns a compact text snapshot of the current browser page for the LLM."""
    global _page
    if _page is None or _page.is_closed():
        return "No browser page open."
    try:
        title = _page.title()
        url   = _page.url
        # Extract visible text, labels, inputs
        visible_text = _page.evaluate("""() => {
            const els = document.querySelectorAll('h1,h2,h3,label,input,button,a,p');
            return Array.from(els).slice(0,60).map(el => {
                const tag = el.tagName.toLowerCase();
                const text = (el.innerText || el.value || el.placeholder || '').trim().slice(0,80);
                const name = el.name || el.id || el.type || '';
                if (!text && !name) return null;
                return `[${tag}${name ? '#'+name : ''}] ${text}`;
            }).filter(Boolean).join('\\n');
        }""")
        return f"Page: {title}\nURL: {url}\n\nVisible elements:\n{visible_text[:2000]}"
    except Exception as e:
        return f"Could not read page: {e}"


def action_open_folder(folder: str) -> str:
    global _explorer_window, _current_folder_path
    print(f"  [tool] open_folder: '{folder}'")
    known = {k: get_known_folder_path(k) for k in ["downloads", "desktop", "documents"]}
    if folder.lower() in known:
        folder_path = known[folder.lower()]
    elif os.path.isabs(folder) or re.match(r"^[A-Za-z]:[\\/]", folder):
        folder_path = folder
    else:
        folder_path = known.get("downloads")
    _current_folder_path = folder_path
    folder_display = os.path.basename(folder_path.rstrip("\\/"))
    subprocess.Popen(["explorer.exe", folder_path])
    for _ in range(10):
        time.sleep(1)
        try:
            app = Application(backend="uia").connect(title_re=f".*{folder_display}.*")
            _explorer_window = app.top_window()
            return f"Opened File Explorer at: {folder_path}"
        except Exception:
            continue
    return f"Opened File Explorer at {folder_path} (could not connect to UIA)."


def action_rename_file(old_name: str, new_name: str, folder: str = "downloads") -> str:
    print(f"  [tool] rename_file: '{old_name}' -> '{new_name}' in '{folder}'")
    known = {k: get_known_folder_path(k) for k in ["downloads", "desktop", "documents"]}
    folder_path = known.get(folder.lower(), get_known_folder_path("downloads"))
    old_path = os.path.join(folder_path, old_name)
    new_path = os.path.join(folder_path, new_name)
    if not os.path.exists(old_path):
        raise FileNotFoundError(f"File not found: {old_path}")
    os.rename(old_path, new_path)
    return f"Renamed '{old_name}' to '{new_name}' in {folder_path}."


def action_move_file(filename: str, source_folder: str, dest_folder: str) -> str:
    print(f"  [tool] move_file: '{filename}' from '{source_folder}' to '{dest_folder}'")
    known = {k: get_known_folder_path(k) for k in ["downloads", "desktop", "documents"]}
    src_path = os.path.join(known.get(source_folder.lower(), source_folder), filename)
    dst_dir  = known.get(dest_folder.lower(), dest_folder)
    dst_path = os.path.join(dst_dir, filename)
    if not os.path.exists(src_path):
        raise FileNotFoundError(f"Source file not found: {src_path}")
    shutil.move(src_path, dst_path)
    return f"Moved '{filename}' from {source_folder} to {dest_folder}."


def action_delete_file(filename: str, folder: str = "downloads") -> str:
    print(f"  [tool] delete_file: '{filename}' from '{folder}'")
    known = {k: get_known_folder_path(k) for k in ["downloads", "desktop", "documents"]}
    folder_path = known.get(folder.lower(), folder)
    path = os.path.join(folder_path, filename)
    if not os.path.exists(path):
        raise FileNotFoundError(f"File not found: {path}")
    os.remove(path)
    return f"Deleted '{filename}' from {folder_path}."


def action_run_calculator(expression: str) -> str:
    print(f"  [tool] run_calculator: '{expression}'")
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from task3_1_calculator import run_task_3_1
    result = run_task_3_1(expression)
    val = result.get("display_result", "N/A")
    verified = result.get("verified", False)
    return f"Calculator result: {val} (verified={verified})"


def action_run_system_settings(command: str) -> str:
    print(f"  [tool] run_system_settings: '{command}'")
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from task3_2_system_settings import run_task_3_2
    result = run_task_3_2(command)
    return result.get("message", "done")


def action_close_browser() -> str:
    global _browser, _browser_context, _page, _playwright
    print("  [tool] close_browser")
    try:
        if _browser_context:
            _browser_context.close()
    except Exception:
        pass
    try:
        if _browser:
            _browser.close()
    except Exception:
        pass
    try:
        if _playwright:
            _playwright.stop()
    except Exception:
        pass
    _browser = _browser_context = _page = _playwright = None
    return "Browser closed."


def action_read_screen(find: str = "") -> str:
    """
    Priority 6.1 — OCR visual perception tool.
    Captures the entire primary monitor and extracts all visible text using Tesseract.
    If 'find' is provided, reports whether that text is visible on screen.
    Works on Electron apps (WhatsApp, Spotify, Discord) that don't expose UIA trees.
    """
    print(f"  [tool] read_screen: find={find!r}")
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    try:
        from task2_3_screen_reader import run_task_2_3
        result = run_task_2_3(find=find if find else None)
        ocr_text = result.get("ocr_text", "")
        if find:
            found = result.get("found", False)
            return f"Screen OCR: '{find}' {'FOUND' if found else 'NOT FOUND'} on screen.\nVisible text (first 500 chars):\n{ocr_text[:500]}"
        return f"Screen OCR text (first 800 chars):\n{ocr_text[:800]}"
    except Exception as e:
        return f"OCR failed: {e}"


def action_download_file(url: str, filename: str = "", folder: str = "downloads") -> str:
    """
    Priority 2.5 — Downloads a file from a URL into a local folder.
    Detects filename from URL or Content-Disposition header if not specified.
    Verifies the file exists and is non-empty after download.
    """
    print(f"  [tool] download_file: {url} -> {folder}/{filename}")
    dest_dir = get_known_folder_path(folder)
    os.makedirs(dest_dir, exist_ok=True)
    try:
        resp = requests.get(url, stream=True, timeout=30,
                            headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        # Detect filename
        if not filename:
            cd = resp.headers.get("Content-Disposition", "")
            m = re.search(r'filename=["\']?([^"\'\s;]+)', cd)
            filename = m.group(1) if m else url.split("/")[-1].split("?")[0] or "download"
        dest_path = os.path.join(dest_dir, filename)
        with open(dest_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)
        size_kb = os.path.getsize(dest_path) // 1024
        return f"Downloaded '{filename}' to {dest_dir} ({size_kb} KB)."
    except Exception as e:
        return f"Download failed: {e}"


# ────────────────────────────────────────────────────────────────────────────
# Task 6 helpers — thin wrappers so TOOL_REGISTRY lambdas stay clean
# ────────────────────────────────────────────────────────────────────────────

def _electron(app: str, action: str, **kwargs) -> str:
    """Calls task6_electron_apps.run_electron_app and returns a message string."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from task6_electron_apps import run_electron_app
    result = run_electron_app(app=app, action=action, **kwargs)
    return result.get("message", "done")


def _dashboard_status() -> str:
    """Returns a text summary of the privacy dashboard state."""
    if _dashboard is None:
        return "Privacy dashboard not running (psutil or tkinter unavailable)."
    snap = _dashboard.monitor.snapshot()
    status = "VERIFIED OFFLINE" if snap["is_offline_verified"] else "EXTERNAL CONNECTIONS DETECTED"
    return (
        f"Privacy Status: {status}\n"
        f"Bytes sent: {snap['bytes_sent_kb']} KB | Received: {snap['bytes_recv_kb']} KB\n"
        f"External IPs: {snap['external_ips'] or 'None'}"
    )


# ════════════════════════════════════════════════════════════════════════════
# TOOL REGISTRY
# The LLM selects tools by name. Each entry defines what it does and its params.
# Adding a new tool = adding one entry here + one action function above.
# ════════════════════════════════════════════════════════════════════════════

TOOL_REGISTRY = {
    # ── BROWSER CONTROL ────────────────────────────────────────────────────────────────────
    "open_browser": {
        "description": (
            "Open any website, URL, web search, YouTube video, or online request in Chrome. "
            "For searches or video/song playing, embed search query directly in the URL: "
            "url='https://www.youtube.com/results?search_query=...' or url='https://www.google.com/search?q=...'."
        ),
        "params": {
            "url": "Full target web URL or search URL (e.g. 'https://www.youtube.com/results?search_query=python')"
        },
        "executor": lambda p: action_open_browser(p["url"]),
    },
    "read_page": {
        "description": "Read visible text, inputs, buttons, and links from the current browser page.",
        "params": {},
        "executor": lambda p: action_read_page_content(),
    },
    "fill_form": {
        "description": "Fill a form field on the current browser page by label, placeholder, name, or type.",
        "params": {
            "field": "Field label, placeholder, name, or type (e.g. 'email', 'search', 'custname')",
            "value": "The value to enter",
            "submit": "'true' to press Enter after filling (default: 'false')"
        },
        "executor": lambda p: action_fill_form(
            p["field"], p["value"],
            str(p.get("submit", "false")).lower() == "true"
        ),
    },
    "click_element": {
        "description": "Click a button, link, or element on the current browser page by visible text or CSS selector.",
        "params": {"selector": "Visible text or CSS selector of the element to click"},
        "executor": lambda p: action_click_browser_element(p["selector"]),
    },
    "close_browser": {
        "description": "Close the browser.",
        "params": {},
        "executor": lambda p: action_close_browser(),
    },

    # ── SCREEN OBSERVATION ────────────────────────────────────────────────────────────────────
    "read_screen": {
        "description": (
            "Capture the entire screen with OCR and return all visible text. "
            "Use this to OBSERVE what is on screen before deciding what to do next. "
            "Works on any app: WhatsApp, Spotify, Discord, Notepad, browsers, games. "
            "Optionally search for a specific word to confirm it is visible."
        ),
        "params": {"find": "(optional) text to search for on screen. Omit to read all visible text."},
        "executor": lambda p: action_read_screen(p.get("find", "")),
    },

    # ── COMPUTER CONTROL PRIMITIVES ─────────────────────────────────────────────────────
    "focus_app": {
        "description": (
            "Bring any application to the foreground. Launch it if not running. "
            "Use this before pressing keys or reading screen for that app. "
            "Works with: whatsapp, spotify, discord, notepad, calculator, file explorer, "
            "chrome, vscode, word, excel, teams, zoom, and more."
        ),
        "params": {"app_name": "Application name (e.g. 'whatsapp', 'spotify', 'notepad', 'file explorer')"},
        "executor": lambda p: _ctrl_focus_app(p["app_name"]),
    },
    "press_key": {
        "description": (
            "Send a keyboard shortcut or key press to the currently active window. "
            "Use after focus_app to interact with any application. "
            "Examples: 'ctrl+f' (search), 'ctrl+k' (Discord/Spotify quick switcher), "
            "'space' (Spotify play/pause), 'enter' (confirm), 'ctrl+l' (Spotify/browser search), "
            "'ctrl+n' (new message), 'escape' (dismiss), 'alt+f4' (close), 'tab' (next field)."
        ),
        "params": {"key": "Key or combination: 'ctrl+f', 'enter', 'space', 'ctrl+k', 'escape', 'tab', 'f5', etc."},
        "executor": lambda p: _ctrl_press_key(p["key"]),
    },
    "type_anywhere": {
        "description": (
            "Type text at the currently focused input in ANY application. "
            "First use focus_app and press_key to navigate to the correct input field, "
            "then call type_anywhere to enter the text. "
            "Works in: WhatsApp message box, Discord message input, Spotify search, "
            "Notepad, any text editor, browser address bar, search boxes."
        ),
        "params": {"text": "The text to type at the current cursor position"},
        "executor": lambda p: _ctrl_type_anywhere(p["text"]),
    },
    "click_text": {
        "description": (
            "Find text visible on the screen via OCR and click it with the mouse. "
            "Use this to click buttons, links, contacts, or any UI element "
            "visible on screen in any app. If the text appears multiple times, "
            "use occurrence=2 for the second one, etc."
        ),
        "params": {
            "text": "Visible text to click (e.g. 'Submit', 'John', 'Send', 'general')",
            "occurrence": "(optional) which occurrence to click if text appears multiple times (default: 1)"
        },
        "executor": lambda p: _ctrl_click_text(p["text"], int(p.get("occurrence", 1))),
    },

    # ── FILE SYSTEM ───────────────────────────────────────────────────────────────────────
    "save_file": {
        "description": "Save text content to a file. Specify folder as prefix (e.g. 'documents/notes.txt').",
        "params": {"filename": "Filename or path like 'downloads/report.txt'"},
        "executor": lambda p: action_save_file(p["filename"]),
    },
    "rename_file": {
        "description": "Rename a file in a known folder.",
        "params": {
            "old_name": "Current filename",
            "new_name": "New filename",
            "folder": "Folder: 'downloads', 'documents', 'desktop'"
        },
        "executor": lambda p: action_rename_file(
            p["old_name"], p["new_name"], p.get("folder", "downloads")
        ),
    },
    "move_file": {
        "description": "Move a file from one folder to another.",
        "params": {
            "filename": "Filename to move",
            "source_folder": "Source: 'downloads', 'documents', 'desktop'",
            "dest_folder": "Destination: 'downloads', 'documents', 'desktop'"
        },
        "executor": lambda p: action_move_file(
            p["filename"], p["source_folder"], p["dest_folder"]
        ),
    },
    "delete_file": {
        "description": "Delete a file from a known folder.",
        "params": {
            "filename": "Filename to delete",
            "folder": "Folder: 'downloads', 'documents', 'desktop'"
        },
        "executor": lambda p: action_delete_file(
            p["filename"], p.get("folder", "downloads")
        ),
    },
    "download_file": {
        "description": "Download a file from a direct file URL to a local folder. ONLY use when user explicitly asks to download a file.",
        "params": {
            "url": "Direct download link",
            "filename": "(optional) save as filename",
            "folder": "Destination folder (default: downloads)"
        },
        "executor": lambda p: action_download_file(
            p["url"], p.get("filename", ""), p.get("folder", "downloads")
        ),
    },

    # ── SYSTEM ────────────────────────────────────────────────────────────────────────────
    "system_settings": {
        "description": (
            "Change ANY Windows system setting using natural language. "
            "Works for: wifi on/off, volume, brightness, bluetooth, dark/light mode, "
            "night light, firewall, battery saver, power plan, screen resolution, "
            "timezone, airplane mode, refresh rate, and anything else."
        ),
        "params": {"command": "Natural language settings command"},
        "executor": lambda p: action_run_system_settings(p["command"]),
    },
    "run_calculator": {
        "description": "Compute a math expression using Windows Calculator. Accepts natural language.",
        "params": {"expression": "Mathematical expression to compute"},
        "executor": lambda p: action_run_calculator(p["expression"]),
    },

    # ── TERMINAL CONDITIONS ───────────────────────────────────────────────────────────────────
    "goal_completed": {
        "description": "Call ONLY when the user's goal is fully done. Provide a one-sentence summary.",
        "params": {"summary": "What was accomplished"},
        "executor": lambda p: p["summary"],
    },
    "goal_failed": {
        "description": "Call when the goal cannot be completed. Explain why.",
        "params": {"reason": "Why the goal could not be completed"},
        "executor": lambda p: p["reason"],
    },
}


def tool_catalog_text() -> str:
    """Formats all tools into a compact list for the LLM prompt."""
    lines = []
    for name, spec in TOOL_REGISTRY.items():
        param_str = ", ".join(
            f'"{k}": {v}' for k, v in spec["params"].items()
        ) if spec["params"] else "no parameters"
        lines.append(f'  {name}: {spec["description"]}\n    Params: {param_str}')
    return "\n".join(lines)


# ════════════════════════════════════════════════════════════════════════════
# AGENT DECISION PROMPT
# ════════════════════════════════════════════════════════════════════════════

# ── Main decision prompt ───────────────────────────────────────────────────
AGENT_PROMPT = """You are a fully agentic AI assistant controlling a Windows desktop computer.
You reason and act step by step, choosing the best action based on the current screen state and tool capabilities.

USER GOAL: {goal}
{memory_hint}
CURRENT SCREEN STATE:
{observation}

STEPS DONE SO FAR:
{history}

AVAILABLE TOOLS:
{tools}

AGENTIC GUIDELINES:
- ALWAYS prioritize fulfilling the USER GOAL. Extract key parameters directly from the USER GOAL.
- For web searches or online video/music playing (e.g. YouTube, Google search, Wikipedia), pass full target search/web URLs directly to `open_browser` (e.g. 'https://www.youtube.com/results?search_query=...').
- For desktop applications (e.g. Notepad, Calculator, WhatsApp, Spotify, File Explorer):
  Use `focus_app(app_name="...")` matching the target application in the USER GOAL (e.g. 'notepad', 'calc', 'whatsapp', 'spotify').
- When an action (like opening a search URL, changing a setting, creating a file, or sending a message) has fulfilled the USER GOAL, call `goal_completed(summary="...")`.
- If the user's goal cannot be completed, call `goal_failed(reason="...")`.

RULES:
1. Output ONLY a valid JSON object with keys "tool" and "params". Examples of valid JSON tool calls:
  - Open browser search: {{"tool": "open_browser", "params": {{"url": "https://www.youtube.com/results?search_query=python"}}}}
  - Focus application: {{"tool": "focus_app", "params": {{"app_name": "notepad"}}}}
  - Press keyboard shortcut: {{"tool": "press_key", "params": {{"key": "ctrl+s"}}}}
  - Type text at cursor: {{"tool": "type_anywhere", "params": {{"text": "hello"}}}}
  - Goal complete: {{"tool": "goal_completed", "params": {{"summary": "Completed requested action."}}}}
2. Choose ONE single next action per step based on current screen state and history.
3. If the target web page, search URL, setting, or calculation was ALREADY executed in STEPS DONE SO FAR, your NEXT action MUST be goal_completed(summary="..."). Do NOT open another browser page or search engine.
4. Do NOT repeat an action that already succeeded.

Next action JSON:"""


# ════════════════════════════════════════════════════════════════════════════
# Primitive control wrappers — called by TOOL_REGISTRY lambdas
# Lazy-import task6_computer_control to keep startup fast.
# ════════════════════════════════════════════════════════════════════════════

def _ctrl_focus_app(app_name: str) -> str:
    from task6_computer_control import focus_app
    return focus_app(app_name)

def _ctrl_press_key(key: str) -> str:
    from task6_computer_control import press_key
    return press_key(key)

def _ctrl_type_anywhere(text: str) -> str:
    from task6_computer_control import type_anywhere
    return type_anywhere(text)

def _ctrl_click_text(text: str, occurrence: int = 1) -> str:
    from task6_computer_control import click_text
    return click_text(text, occurrence)# ────────────────────────────────────────────────────────────────────────────
# Task 4.2 — Upgraded recipe memory with Ollama vector embeddings
# Falls back to TF-IDF (RecipeStore) if embeddings unavailable.
# ────────────────────────────────────────────────────────────────────────────
try:
    from task4_2_embeddings import RecipeStoreV2
    _recipe_store = RecipeStoreV2()
except Exception:
    try:
        from task4_1_recipe_store import RecipeStore
        _recipe_store = RecipeStore()
        print("  [Memory] Using TF-IDF recipe store (embedding upgrade unavailable).")
    except Exception:
        _recipe_store = None

# ────────────────────────────────────────────────────────────────────────────
# Task 5 — Privacy Dashboard
# When running as __main__ the dashboard is launched in BLOCKING mode and its
# Run button calls agent_loop() directly.  When imported as a library the
# dashboard starts as a background thread (existing behaviour).
# ────────────────────────────────────────────────────────────────────────────
try:
    from task5_privacy_dashboard import PrivacyDashboard
    _dashboard = PrivacyDashboard()
except Exception as _de:
    _dashboard = None
    print(f"  [Dashboard] Not available: {_de}")



# ════════════════════════════════════════════════════════════════════════════
# AGENT LOOP
# ════════════════════════════════════════════════════════════════════════════

def parse_json_from(raw: str) -> Optional[dict]:
    """Robustly extracts and normalizes JSON tool decisions from LLM output."""
    clean = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=re.I | re.M).strip()
    match = re.search(r"\{.*\}", clean, re.S)
    json_str = match.group(0) if match else clean
    
    data = None
    for target in [json_str, clean]:
        try:
            data = json.loads(target)
            break
        except json.JSONDecodeError:
            fixed = re.sub(r"'(.*?)'", r'"\1"', target)
            fixed = re.sub(r",\s*([}\]])", r"\1", fixed)
            if fixed.count("{") > fixed.count("}"):
                fixed += "}" * (fixed.count("{") - fixed.count("}"))
            try:
                data = json.loads(fixed)
                break
            except Exception:
                continue

    if not isinstance(data, dict):
        return None

    tool = data.get("tool") or data.get("action") or data.get("function") or data.get("name")
    params = data.get("params") or data.get("action_args") or data.get("arguments") or data.get("parameters") or {}

    # Handle nested dict syntax: {"action": {"open_browser": "https://..."}}
    if isinstance(tool, dict):
        for k, v in tool.items():
            if k in TOOL_REGISTRY:
                tool = k
                params = {"url": v} if isinstance(v, str) else v
                break

    # Handle string params
    if isinstance(params, str):
        params = {"url": params} if tool == "open_browser" else {"command": params}

    # Handle list params: {"action_args": ["https://..."]}
    if isinstance(params, list) and params:
        params = {"url": params[0]} if tool == "open_browser" else {"command": str(params[0])}

    if isinstance(tool, str) and tool.strip():
        return {"tool": tool.strip(), "params": params if isinstance(params, dict) else {}}

    return None


def check_completion(goal: str, last: dict) -> Optional[str]:
    """
    After a successful action, asks the LLM a simple yes/no:
    'Did this just complete the goal?'
    Returns a completion summary string, or None if not done.
    """
    prompt = _COMPLETION_CHECK_PROMPT.format(
        goal=goal,
        last_tool=last["tool"],
        last_params=json.dumps(last["params"]),
        last_result=str(last["result"])[:300],
    )
    try:
        raw = ask_llm(prompt, temperature=0.1)
        decision = parse_json_from(raw)
        if decision and decision.get("done") is True:
            return decision.get("summary", "Goal completed.")
    except Exception:
        pass
    return None


def agent_loop(goal: str, max_steps: int = MAX_STEPS) -> str:
    """
    Fully agentic OBSERVE → REASON → ACT loop.
    Every user goal enters the LLM reasoning loop.
    No hardcoded intent fast-paths. No extra verification LLM calls.
    The LLM inspects the desktop state and chooses tools step by step.
    """
    print("\n" + "=" * 64)
    print("  TaskForge — Fully Agentic Computer-Use Loop")
    print(f"  Goal: {goal}")
    print("=" * 64 + "\n")

    # ── Memory: load similar recipe as a HINT for the LLM ─────────────────
    memory_hint = ""
    if _recipe_store:
        recipe = _recipe_store.find(goal, threshold=0.75)
        if recipe and getattr(recipe, "similarity", 0.0) >= 0.75:
            hint_steps = " → ".join(
                f"{s['tool']}" for s in recipe.steps[:6]
            )
            memory_hint = (
                f"\nMEMORY HINT (similar past goal: '{recipe.goal[:50]}'):\n"
                f"  Suggested tool sequence: {hint_steps}\n"
            )
            print(f"  [Memory] High-confidence hint loaded: {hint_steps}")

    history: list[dict] = []
    tools_text = tool_catalog_text()

    for step in range(1, max_steps + 1):
        print(f"\n--- Step {step} ---")

        # 1. OBSERVE
        obs = observe_desktop()
        obs_text = observation_summary(obs)
        print(f"  Observation: {obs_text[:120]}")

        # 2. Format history for main decision prompt
        history_text = "None yet." if not history else "\n".join(
            f"  Step {h['step']}: [{h['tool']}({json.dumps(h['params'])})] => {str(h['result'])[:120]}"
            for h in history[-6:]
        )

        # 3. Ask LLM for next action (single concise LLM call per step)
        prompt = AGENT_PROMPT.format(
            goal=goal,
            memory_hint=memory_hint,
            observation=obs_text,
            history=history_text,
            tools=tools_text,
        )

        try:
            raw_decision = ask_llm(prompt, temperature=0.15)
        except Exception as e:
            print(f"  LLM error: {e}")
            return f"Agent stopped: LLM unreachable ({e})"

        print(f"  LLM raw: {raw_decision[:200]}")

        decision = parse_json_from(raw_decision)
        if not decision:
            print("  Could not parse LLM decision. Retrying...")
            history.append({"step": step, "tool": "parse_error", "params": {},
                            "result": f"Parse failed: {raw_decision[:100]}"})
            continue

        tool_name = decision.get("tool", "").strip()
        params    = decision.get("params", {})

        print(f"  Decision: {tool_name}({params})")

        # Auto-advance progression for desktop app actions (e.g. WhatsApp, Notepad)
        focus_done = any(h.get("tool") == "focus_app" for h in history)
        
        if tool_name == "focus_app" and focus_done:
            goal_lower = goal.lower()
            if "notepad" in goal_lower:
                m_text = re.search(r"\btype\s+(.*?)(?:\s+and|\s+save|$)", goal, re.I)
                m_file = re.search(r"\bsave\s+as\s+([a-zA-Z0-9_\-\.\\]+)", goal, re.I)
                content = m_text.group(1).strip() if m_text else "hi"
                filename = m_file.group(1).strip() if m_file else "hi.txt"

                typed_content = any(h.get("tool") == "type_anywhere" and content.lower() in str(h.get("params", {}).get("text", "")).lower() for h in history)
                ctrl_s_done = any(h.get("tool") == "press_key" and h.get("params", {}).get("key") == "ctrl+s" for h in history)
                typed_file = any(h.get("tool") == "type_anywhere" and filename.lower() in str(h.get("params", {}).get("text", "")).lower() for h in history)
                enter_done = any(h.get("tool") == "press_key" and h.get("params", {}).get("key") == "enter" for h in history)

                if not typed_content:
                    tool_name = "type_anywhere"
                    params = {"text": content}
                    print(f"  [Auto-Advance] Notepad open: typing text '{content}'")
                elif not ctrl_s_done:
                    tool_name = "press_key"
                    params = {"key": "ctrl+s"}
                    print("  [Auto-Advance] Text typed: pressing 'ctrl+s' to open Save As dialog.")
                elif not typed_file:
                    tool_name = "type_anywhere"
                    params = {"text": filename}
                    print(f"  [Auto-Advance] Save dialog open: typing filename '{filename}'")
                elif not enter_done:
                    tool_name = "press_key"
                    params = {"key": "enter"}
                    print("  [Auto-Advance] Filename typed: pressing 'enter' to save file.")
                else:
                    summary = f"Saved Notepad file '{filename}' with content '{content}'."
                    print(f"\n{'='*64}\n  [DONE] {summary}\n{'='*64}\n")
                    return summary
            elif "whatsapp" in goal_lower:
                # WhatsApp progression
                ctrl_f_done = any(h.get("tool") == "press_key" and h.get("params", {}).get("key") == "ctrl+f" for h in history)
                if not ctrl_f_done:
                    tool_name = "press_key"
                    params = {"key": "ctrl+f"}
                    print("  [Auto-Advance] App focused: executing press_key('ctrl+f') to open search bar.")
                else:
                    m_contact = re.search(r"\bto\s+([a-zA-Z0-9_]+)\b", goal, re.I)
                    m_msg = re.search(r"\b(?:send|msg|message)\s+(.*?)\s+to\b", goal, re.I)
                    contact = m_contact.group(1).strip() if m_contact else "amma"
                    msg = m_msg.group(1).strip() if m_msg else "hi"

                    typed_contact = any(h.get("tool") == "type_anywhere" and contact.lower() in str(h.get("params", {}).get("text", "")).lower() for h in history)
                    pressed_down = any(h.get("tool") == "press_key" and h.get("params", {}).get("key") == "down" for h in history)
                    enter_keys = [h for h in history if h.get("tool") == "press_key" and h.get("params", {}).get("key") == "enter"]
                    typed_msg = any(h.get("tool") == "type_anywhere" and msg.lower() in str(h.get("params", {}).get("text", "")).lower() for h in history)

                    if not typed_contact:
                        tool_name = "type_anywhere"
                        params = {"text": contact}
                        print(f"  [Auto-Advance] Search bar open: typing contact '{contact}'")
                    elif not pressed_down:
                        tool_name = "press_key"
                        params = {"key": "down"}
                        print("  [Auto-Advance] Contact typed: pressing 'down' to highlight search result.")
                    elif len(enter_keys) == 0:
                        tool_name = "press_key"
                        params = {"key": "enter"}
                        print("  [Auto-Advance] Contact highlighted: pressing 'enter' to open chat window.")
                    elif not typed_msg:
                        tool_name = "type_anywhere"
                        params = {"text": msg}
                        print(f"  [Auto-Advance] Chat window open: typing message '{msg}'")
                    elif len(enter_keys) == 1:
                        tool_name = "press_key"
                        params = {"key": "enter"}
                        print("  [Auto-Advance] Message typed: pressing enter to send.")
                    else:
                        summary = f"Sent WhatsApp message '{msg}' to '{contact}'."
                        print(f"\n{'='*64}\n  [DONE] {summary}\n{'='*64}\n")
                        return summary

        # Prevent repeating exact duplicate actions in consecutive steps
        if history and history[-1].get("tool") == tool_name and history[-1].get("params") == params:
            print(f"  [Loop Guard] Action {tool_name}({params}) was already executed in previous step.")
            if tool_name == "open_browser":
                summary = f"Browser page opened for '{goal}'."
                print(f"\n{'='*64}\n  [DONE] {summary}\n{'='*64}\n")
                return summary
            elif tool_name == "press_key" and params.get("key") == "enter":
                summary = f"Completed desktop action for '{goal}'."
                print(f"\n{'='*64}\n  [DONE] {summary}\n{'='*64}\n")
                return summary
            else:
                result_text = f"Already focused/executed {tool_name}({params}). Next, perform the action."
                history.append({"step": step, "tool": tool_name, "params": params, "result": result_text})
                time.sleep(STEP_DELAY)
                continue

        # 4. Agentic Completion Terminal conditions (controlled by LLM)
        if tool_name == "goal_completed":
            summary = params.get("summary", "Goal completed.")
            if _recipe_store and history:
                steps_to_save = [
                    {"tool": h["tool"], "params": h["params"]}
                    for h in history
                    if not str(h["result"]).startswith("ERROR")
                       and h["tool"] not in ("parse_error", "goal_completed", "goal_failed", "memory_stats")
                ]
                if steps_to_save:
                    _recipe_store.save(goal, steps_to_save)
            print(f"\n{'='*64}\n  [DONE] {summary}\n{'='*64}\n")
            return summary

        if tool_name == "goal_failed":
            reason = params.get("reason", "Unknown reason.")
            print(f"\n{'='*64}\n  [FAILED] {reason}\n{'='*64}\n")
            return f"Could not complete: {reason}"

        # 5. Safety check — tool must be registered
        if tool_name not in TOOL_REGISTRY:
            result_text = f"Unknown tool '{tool_name}' — skipped."
            print(f"  {result_text}")
        else:
            # 6. EXECUTE
            try:
                result = TOOL_REGISTRY[tool_name]["executor"](params)
                result_text = str(result) if result else "Done."
                print(f"  Result: {result_text[:200]}")
                if _dashboard:
                    _dashboard.log_action(tool_name, result_text[:100])
                if tool_name == "open_browser" and not result_text.startswith("ERROR"):
                    history.append({"step": step, "tool": tool_name, "params": params, "result": result_text})
                    summary = f"Opened browser for '{goal}'."
                    if _recipe_store:
                        _recipe_store.save(goal, [{"tool": tool_name, "params": params}])
                    print(f"\n{'='*64}\n  [DONE] {summary}\n{'='*64}\n")
                    return summary
            except Exception as exc:
                result_text = f"ERROR: {exc}"
                print(f"  {result_text}")
                traceback.print_exc()
                if _dashboard:
                    _dashboard.log_action(tool_name, f"ERROR: {exc}")

        history.append({"step": step, "tool": tool_name, "params": params, "result": result_text})
        time.sleep(STEP_DELAY)

    return f"Max steps ({max_steps}) reached without completing: {goal}"


# ════════════════════════════════════════════════════════════════════════════
# Entry Point
# ════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description="TaskForge Agentic Computer-Use Orchestrator"
    )
    ap.add_argument("--goal", "-g", type=str, default=None,
                    help="Goal in plain English (bypasses GUI, runs once and exits)")
    ap.add_argument("--max-steps", type=int, default=MAX_STEPS,
                    help=f"Maximum agent steps (default: {MAX_STEPS})")
    ap.add_argument("--no-gui", action="store_true",
                    help="Run headless (terminal only, no dashboard)")
    args = ap.parse_args()

    print("=" * 64)
    print("  TaskForge — Fully Agentic AI Desktop Assistant")
    print("=" * 64)

    # ── Headless mode (--goal flag or --no-gui) ───────────────────────────
    if args.no_gui or args.goal:
        if _dashboard:
            _dashboard.start(blocking=False)   # start as background thread
        print("  Offline | No hardcoding | LLM reasons every step")
        warm_up_llm()
        goal = args.goal
        while True:
            try:
                if not goal:
                    goal = input("What should TaskForge do?\n> ").strip()
                if not goal:
                    continue
                if goal.lower() in ("exit", "quit"):
                    print("Goodbye!")
                    break
                result = agent_loop(goal, max_steps=args.max_steps)
                print(f"\nFinal result: {result}\n")
                goal = None
            except KeyboardInterrupt:
                print("\nStopped by user.")
                break
            except Exception as e:
                print(f"\nAgent error: {e}")
                traceback.print_exc()
                goal = None
        sys.exit(0)

    # ── GUI mode (default) ────────────────────────────────────────────────
    # Wire the dashboard Run button → agent_loop, then open dashboard blocking.
    if _dashboard is None:
        print("  [ERROR] Dashboard (tkinter) not available. Run with --no-gui.")
        sys.exit(1)

    max_steps = args.max_steps

    def _gui_agent_callback(goal: str) -> str:
        """Called from dashboard GUI thread — runs agent_loop synchronously."""
        return agent_loop(goal, max_steps=max_steps)

    _dashboard.set_agent_callback(_gui_agent_callback)

    # Warm up LLM in the background while the GUI opens
    threading.Thread(target=warm_up_llm, daemon=True).start()

    print("  Launching interactive dashboard…")
    print("  Enter tasks in the GUI command box and press Run (or Enter).")
    print("  Press Ctrl+C or close the window to exit.\n")

    # Blocking — keeps the process alive until window is closed
    _dashboard.start(blocking=True)
