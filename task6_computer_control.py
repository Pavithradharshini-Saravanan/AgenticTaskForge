"""
Task 6 — Primitive Computer Control Tools
==========================================

These are the ONLY low-level tools the agent needs to control ANY application
on a Windows desktop — including Electron apps that don't expose UIA trees.

Primitives:
  focus_app(name)       — Bring any app to front, or launch it
  press_key(key)        — Send any keyboard shortcut to the active window
  type_anywhere(text)   — Type text at the currently focused input position
  click_text(text)      — Find text on screen via OCR and click it (pixel-level)
  get_mouse_pos()       — Returns current mouse position (for debug)

With these 4 primitives + read_screen (OCR), the agent can control:
  - WhatsApp Desktop: focus_app → read_screen → press_key(ctrl+f) → type_anywhere → press_key(enter)
  - Spotify: focus_app → press_key(ctrl+l) → type_anywhere → press_key(enter)
  - Discord: focus_app → press_key(ctrl+k) → type_anywhere → press_key(enter)
  - Notepad: focus_app → type_anywhere → press_key(ctrl+s)
  - File Explorer: focus_app → press_key(alt+d) → type_anywhere(path)
  - Any app ever built — the developer never needs to write a new sequence

Design principle:
  The LLM observes the screen, decides what key to press or what text to click.
  Python executes it reliably.
  No hardcoded per-app workflows exist here.
"""

import os
import re
import sys
import time
import subprocess
import shutil
from typing import Optional

import pyperclip
try:
    from pywinauto.keyboard import send_keys
except Exception:
    send_keys = None

try:
    import pyautogui
    pyautogui.FAILSAFE = False
    pyautogui.PAUSE = 0.1
    _PYAUTOGUI = True
except ImportError:
    _PYAUTOGUI = False

try:
    import pytesseract
    from PIL import Image
    tess_paths = [
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        os.path.expanduser(r"~\AppData\Local\Programs\Tesseract-OCR\tesseract.exe"),
        shutil.which("tesseract") or "",
    ]
    for p in tess_paths:
        if p and os.path.exists(p):
            pytesseract.pytesseract.tesseract_cmd = p
            break
    _PYTESSERACT = True
except ImportError:
    _PYTESSERACT = False

try:
    import mss
    _MSS = True
except ImportError:
    _MSS = False


# ────────────────────────────────────────────────────────────────────────────
# Key name normalizer
# Maps human-readable key names → pywinauto send_keys format
# ────────────────────────────────────────────────────────────────────────────

_KEY_MAP = {
    # Special keys
    "enter": "{ENTER}", "return": "{ENTER}",
    "esc": "{ESCAPE}", "escape": "{ESCAPE}",
    "tab": "{TAB}", "backspace": "{BACKSPACE}",
    "delete": "{DELETE}", "del": "{DELETE}",
    "space": " ", "spacebar": " ",
    "up": "{UP}", "down": "{DOWN}", "left": "{LEFT}", "right": "{RIGHT}",
    "home": "{HOME}", "end": "{END}",
    "pageup": "{PGUP}", "pagedown": "{PGDN}",
    "f1": "{F1}", "f2": "{F2}", "f3": "{F3}", "f4": "{F4}",
    "f5": "{F5}", "f6": "{F6}", "f7": "{F7}", "f8": "{F8}",
    "f9": "{F9}", "f10": "{F10}", "f11": "{F11}", "f12": "{F12}",

    # Common combos
    "ctrl+c": "^c", "ctrl+v": "^v", "ctrl+x": "^x", "ctrl+z": "^z",
    "ctrl+y": "^y", "ctrl+a": "^a", "ctrl+s": "^s", "ctrl+w": "^w",
    "ctrl+n": "^n", "ctrl+t": "^t", "ctrl+r": "^r", "ctrl+f": "^f",
    "ctrl+k": "^k", "ctrl+l": "^l", "ctrl+p": "^p", "ctrl+d": "^d",
    "ctrl+e": "^e", "ctrl+q": "^q", "ctrl+h": "^h",
    "ctrl+enter": "^{ENTER}",
    "ctrl+right": "^{RIGHT}", "ctrl+left": "^{LEFT}",
    "ctrl+up": "^{UP}", "ctrl+down": "^{DOWN}",
    "ctrl+shift+esc": "^+{ESCAPE}",
    "ctrl+shift+down": "^+{DOWN}", "ctrl+shift+up": "^+{UP}",
    "ctrl+shift+s": "^+s", "ctrl+shift+n": "^+n",
    "alt+f4": "%{F4}", "alt+tab": "%{TAB}", "alt+enter": "%{ENTER}",
    "alt+d": "%d", "alt+f": "%f",
    "win+d": "{WIN}d", "win+e": "{WIN}e",
    "win": "{WIN}",
    "ctrl+alt+del": "^%{DEL}",
}


def _normalize_key(key: str) -> str:
    """
    Converts a human-friendly key name to pywinauto send_keys format.
    Falls back to passing the key string directly if not in the map.
    """
    normalized = key.lower().strip()
    if normalized in _KEY_MAP:
        return _KEY_MAP[normalized]
    # Already in send_keys format (e.g. "^c", "{ENTER}")
    if re.match(r"^[\^%+!{]", key):
        return key
    # Single character — pass as-is
    if len(key) == 1:
        return key
    # Wrap unknown multi-char in braces (e.g. "ENTER" → "{ENTER}")
    return "{" + key.upper() + "}"


# ────────────────────────────────────────────────────────────────────────────
# App launch / focus database
# Maps common app names → executable path or launch command
# This is the ONLY place app names appear — used just for launching.
# All interaction after launch is done via primitives (press_key, click_text).
# ────────────────────────────────────────────────────────────────────────────

_APP_LAUNCH = {
    "notepad":      "notepad.exe",
    "calc":         "calc.exe",
    "calculator":   "calc.exe",
    "explorer":     "explorer.exe",
    "paint":        "mspaint.exe",
    "wordpad":      "wordpad.exe",
    "cmd":          "cmd.exe",
    "powershell":   "powershell.exe",
    "settings":     "ms-settings:",
    "spotify":      os.path.join(os.getenv("APPDATA", ""), "Spotify", "Spotify.exe"),
    "whatsapp":     "whatsapp:",
    "discord":      os.path.join(os.getenv("LOCALAPPDATA", ""), "Discord", "Update.exe"),
    "chrome":       shutil.which("chrome") or r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    "firefox":      shutil.which("firefox") or r"C:\Program Files\Mozilla Firefox\firefox.exe",
    "vlc":          r"C:\Program Files\VideoLAN\VLC\vlc.exe",
    "vscode":       shutil.which("code") or r"C:\Program Files\Microsoft VS Code\Code.exe",
    "excel":        r"C:\Program Files\Microsoft Office\root\Office16\EXCEL.EXE",
    "word":         r"C:\Program Files\Microsoft Office\root\Office16\WINWORD.EXE",
    "teams":        os.path.join(os.getenv("LOCALAPPDATA", ""), "Microsoft", "Teams", "current", "Teams.exe"),
    "zoom":         os.path.join(os.getenv("APPDATA", ""), "Zoom", "bin", "Zoom.exe"),
    "task manager": "taskmgr.exe",
}


def focus_app(app_name: str) -> str:
    """
    Brings an application to the foreground.
    If not running, launches it.
    The agent uses this to switch context before pressing keys or reading screen.
    
    Examples:
      focus_app("whatsapp")
      focus_app("spotify")
      focus_app("notepad")
      focus_app("discord")
      focus_app("file explorer")
    """
    import win32gui

    name_lower = app_name.lower().strip()

    # 1. Try to find a running window matching the name
    found_hwnd = None
    def _enum(hwnd, _):
        nonlocal found_hwnd
        if win32gui.IsWindowVisible(hwnd):
            title = win32gui.GetWindowText(hwnd).lower()
            if name_lower in title or any(
                word in title for word in name_lower.split()
                if len(word) > 2
            ):
                found_hwnd = hwnd
    try:
        win32gui.EnumWindows(_enum, None)
    except Exception:
        pass

    if found_hwnd:
        try:
            import win32con
            win32gui.ShowWindow(found_hwnd, win32con.SW_RESTORE)
            win32gui.SetForegroundWindow(found_hwnd)
            time.sleep(0.6)
            return f"Brought '{app_name}' to foreground."
        except Exception:
            pass

    # 2. App not running — launch it
    exe = None
    for key, path in _APP_LAUNCH.items():
        if key in name_lower or name_lower in key:
            exe = path
            break

    if exe:
        if exe.startswith("ms-") or ":" in exe and not exe[1] == ":":
            subprocess.Popen(["start", exe], shell=True)
        elif os.path.exists(exe):
            args = ["--processStart", "Discord.exe"] if "Discord" in exe and "Update" in exe else []
            subprocess.Popen([exe] + args)
        else:
            subprocess.Popen(exe, shell=True)
        time.sleep(3.5)
        return f"Launched '{app_name}' — waiting for it to open."

    # 3. Try via 'start' command with the name directly
    try:
        subprocess.Popen(["start", app_name], shell=True)
        time.sleep(3)
        return f"Attempted to launch '{app_name}' via system."
    except Exception as e:
        return f"Could not find or launch '{app_name}': {e}"


def press_key(key: str) -> str:
    """
    Sends a keyboard shortcut or key press to the active window using PyAutoGUI (pure Windows API, no win32ui DLL required).
    """
    normalized = key.lower().strip()
    print(f"  [press_key] {key!r}")
    try:
        if "+" in normalized:
            parts = [p.strip() for p in normalized.split("+")]
            key_map = {"ctrl": "ctrl", "alt": "alt", "shift": "shift", "win": "win", "esc": "escape", "return": "enter"}
            clean_parts = [key_map.get(p, p) for p in parts]
            pyautogui.hotkey(*clean_parts)
        else:
            key_map = {"enter": "enter", "return": "enter", "esc": "escape", "space": "space", "tab": "tab", "down": "down", "up": "up", "left": "left", "right": "right"}
            clean_key = key_map.get(normalized, normalized)
            pyautogui.press(clean_key)
        time.sleep(0.3)
        return f"Pressed key: {key}"
    except Exception as e:
        return f"Key press failed: {e}"


def type_anywhere(text: str) -> str:
    """
    Types text at the currently focused input field in ANY application using clipboard paste via PyAutoGUI.
    Works reliably in WhatsApp, Discord, Spotify search, Notepad, browser address bar, etc.
    """
    print(f"  [type_anywhere] typing {len(text)} chars: {text[:60]!r}")
    try:
        pyperclip.copy(text)
        time.sleep(0.2)
        pyautogui.hotkey("ctrl", "v")
        time.sleep(0.4)
        return f"Typed: {text[:80]}"
    except Exception as e:
        return f"Typing failed: {e}"

        return f"Typed: {text[:80]}"
    except Exception as e:
        return f"Typing failed: {e}"


def click_text(text: str, occurrence: int = 1) -> str:
    """
    Finds the specified text on the current screen using OCR and clicks its center.
    This allows the agent to click ANY visible UI element in ANY app —
    even Electron apps and games that don't expose accessibility trees.

    Uses pytesseract word-level bounding boxes + pyautogui for the actual click.

    Args:
      text:       The visible text to find and click (case-insensitive partial match)
      occurrence: Which occurrence to click if the text appears multiple times (default: 1st)

    Examples:
      click_text("Submit")        — click a submit button
      click_text("John")          — click a contact named John in WhatsApp
      click_text("Send")          — click a Send button
      click_text("general")       — click a Discord channel named "general"
      click_text("Blinding")      — click a Spotify search result
    """
    if not _PYTESSERACT or not _MSS or not _PYAUTOGUI:
        return (f"click_text requires: mss, pytesseract, pyautogui. "
                f"Install: pip install mss pytesseract pyautogui pillow")

    print(f"  [click_text] looking for {text!r} on screen...")

    try:
        # Capture screen
        with mss.MSS() as sct:
            monitor = sct.monitors[1]  # primary monitor
            shot = sct.grab(monitor)
            img = Image.frombytes("RGB", shot.size, shot.rgb)

        # Get word-level bounding boxes from Tesseract
        data = pytesseract.image_to_data(
            img, output_type=pytesseract.Output.DICT, lang="eng"
        )

        # Find matching words/phrases
        matches = []
        n = len(data["text"])
        for i in range(n):
            word = data["text"][i].strip()
            if not word:
                continue
            if text.lower() in word.lower() or word.lower() in text.lower():
                conf = int(data["conf"][i])
                if conf > 30:  # ignore low-confidence detections
                    x = data["left"][i] + data["width"][i] // 2 + monitor["left"]
                    y = data["top"][i] + data["height"][i] // 2 + monitor["top"]
                    matches.append((conf, x, y, word))

        # Also try multi-word phrase matching
        if not matches and " " in text:
            words = text.lower().split()
            for i in range(n - len(words) + 1):
                chunk = [data["text"][j].strip().lower() for j in range(i, i + len(words))]
                if chunk == words:
                    # Use bounding box of the first word
                    x = data["left"][i] + data["width"][i] // 2 + monitor["left"]
                    y = data["top"][i] + data["height"][i] // 2 + monitor["top"]
                    matches.append((50, x, y, text))

        if not matches:
            return (f"Text '{text}' not found on screen. "
                    f"Use read_screen() to see what's currently visible.")

        # Sort by confidence descending, pick the nth occurrence
        matches.sort(key=lambda m: -m[0])
        idx = min(occurrence - 1, len(matches) - 1)
        conf, x, y, found_word = matches[idx]

        print(f"  [click_text] found '{found_word}' at ({x}, {y}), conf={conf}. Clicking.")
        pyautogui.click(x, y)
        time.sleep(0.5)
        return f"Clicked '{found_word}' at ({x}, {y})."

    except Exception as e:
        if "tesseract" in str(e).lower():
            return "click_text unavailable (Tesseract OCR engine not installed in PATH). Use press_key (e.g. 'ctrl+f' to search, 'enter' to confirm) or type_anywhere instead."
        return f"click_text failed: {e}"


def clear_input() -> str:
    """Selects all text in the focused input and deletes it (Ctrl+A → Delete)."""
    send_keys("^a")
    time.sleep(0.1)
    send_keys("{DELETE}")
    time.sleep(0.1)
    return "Cleared input field."


# ────────────────────────────────────────────────────────────────────────────
# Standalone test
# ────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--tool", choices=["focus_app", "press_key", "type_anywhere", "click_text"])
    ap.add_argument("--arg1", default="")
    ap.add_argument("--arg2", default="")
    args = ap.parse_args()

    if args.tool == "focus_app":
        print(focus_app(args.arg1))
    elif args.tool == "press_key":
        print(press_key(args.arg1))
    elif args.tool == "type_anywhere":
        print(type_anywhere(args.arg1))
    elif args.tool == "click_text":
        print(click_text(args.arg1))
