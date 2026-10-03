"""
Task 6.2 + 6.3 + 6.4 — Electron App Controllers
=================================================

Controls WhatsApp Desktop, Spotify, and Discord using a layered approach:

Layer 1 (preferred): Windows UIA accessibility tree via pywinauto
Layer 2 (fallback):  OCR screen reading via task2_3_screen_reader
Layer 3 (universal): Keyboard shortcuts + pyautogui for mouse actions

Each app controller exposes a clean action dict returned to the agent:
  {
    "success": bool,
    "message": str,
    "app": "whatsapp" | "spotify" | "discord"
  }

How to use standalone:
  python task6_electron_apps.py --app whatsapp --action send --contact "Mum" --message "Hi!"
  python task6_electron_apps.py --app spotify  --action play --query "Blinding Lights"
  python task6_electron_apps.py --app discord  --action send --channel "general" --message "Hello"
"""

import os
import re
import sys
import time
import subprocess
import argparse
from typing import Optional

import pyperclip
from pywinauto import Application
from pywinauto.keyboard import send_keys

# ────────────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────────────

def _ok(msg: str, app: str) -> dict:
    print(f"  [OK] {app}: {msg}")
    return {"success": True, "message": msg, "app": app}

def _err(msg: str, app: str) -> dict:
    print(f"  [ERR] {app}: {msg}")
    return {"success": False, "message": msg, "app": app}


def _focus_window(title_re: str, timeout: int = 8) -> Optional[object]:
    """Tries to connect to a window matching title_re via pywinauto."""
    end = time.time() + timeout
    while time.time() < end:
        try:
            app = Application(backend="uia").connect(title_re=title_re)
            win = app.top_window()
            win.set_focus()
            time.sleep(0.5)
            return win
        except Exception:
            time.sleep(1)
    return None


def _type_and_send(text: str, send: bool = True):
    """Pastes text via clipboard (avoids unicode issues with send_keys)."""
    pyperclip.copy(text)
    send_keys("^v")
    time.sleep(0.3)
    if send:
        send_keys("{ENTER}")
        time.sleep(0.5)


def _ocr_read() -> str:
    """Reads visible screen text via OCR (task2_3 fallback)."""
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from task2_3_screen_reader import run_task_2_3
        result = run_task_2_3()
        return result.get("ocr_text", "")
    except Exception:
        return ""


# ════════════════════════════════════════════════════════════════════════════
# TASK 6.2 — WhatsApp Desktop Controller
# ════════════════════════════════════════════════════════════════════════════

WHATSAPP_EXE = os.path.join(
    os.getenv("LOCALAPPDATA", ""),
    "WhatsApp", "WhatsApp.exe"
)

def _launch_whatsapp() -> Optional[object]:
    """Launches WhatsApp Desktop if not running, returns focused window."""
    # Try to connect to already-running instance
    win = _focus_window(r"(?i)WhatsApp", timeout=3)
    if win:
        return win
    # Launch it
    if os.path.exists(WHATSAPP_EXE):
        subprocess.Popen([WHATSAPP_EXE])
    else:
        subprocess.Popen(["start", "whatsapp:"], shell=True)
    time.sleep(4)
    return _focus_window(r"(?i)WhatsApp", timeout=10)


def whatsapp_send_message(contact: str, message: str) -> dict:
    """
    Searches for a contact and sends a message.
    Uses UIA search box → contact click → message box → send.
    """
    print(f"  [WhatsApp] Sending to '{contact}': {message[:40]}")
    win = _launch_whatsapp()
    if win is None:
        return _err("Could not open WhatsApp Desktop. Is it installed?", "whatsapp")

    try:
        # Open new chat / search (Ctrl+F or Ctrl+N)
        win.set_focus()
        time.sleep(0.5)
        send_keys("^f")   # search / find contact
        time.sleep(0.8)

        # Type contact name
        pyperclip.copy(contact)
        send_keys("^v")
        time.sleep(1.5)

        # Press Enter to open the chat (or click first result)
        send_keys("{ENTER}")
        time.sleep(1)

        # Find the message input box — try UIA first
        try:
            msg_box = win.child_window(
                control_type="Edit", found_index=0
            )
            msg_box.set_focus()
            msg_box.type_keys(message, with_spaces=True)
            time.sleep(0.3)
            send_keys("{ENTER}")
        except Exception:
            # Fallback: click bottom area and paste
            send_keys("{TAB}")
            time.sleep(0.3)
            _type_and_send(message, send=True)

        time.sleep(0.5)
        return _ok(f"Sent '{message[:40]}' to {contact}", "whatsapp")

    except Exception as e:
        return _err(f"Send failed: {e}", "whatsapp")


def whatsapp_read_last(contact: str, n: int = 5) -> dict:
    """
    Opens a contact chat and reads the last N messages via OCR.
    Returns the OCR text of the chat area.
    """
    print(f"  [WhatsApp] Reading last {n} messages from '{contact}'")
    win = _launch_whatsapp()
    if win is None:
        return _err("Could not open WhatsApp Desktop.", "whatsapp")

    try:
        win.set_focus()
        send_keys("^f")
        time.sleep(0.8)
        pyperclip.copy(contact)
        send_keys("^v")
        time.sleep(1.5)
        send_keys("{ENTER}")
        time.sleep(1)

        # Read screen OCR to get message text
        ocr = _ocr_read()
        return _ok(f"Chat with {contact} opened. OCR text: {ocr[:400]}", "whatsapp")

    except Exception as e:
        return _err(f"Read failed: {e}", "whatsapp")


def whatsapp_action(action: str, contact: str = "", message: str = "", **kwargs) -> dict:
    """Dispatcher for WhatsApp actions."""
    a = action.lower().strip()
    if a in ("send", "message", "send_message"):
        if not contact or not message:
            return _err("Need both 'contact' and 'message' parameters.", "whatsapp")
        return whatsapp_send_message(contact, message)
    elif a in ("read", "read_messages", "get_messages"):
        return whatsapp_read_last(contact, n=int(kwargs.get("n", 5)))
    else:
        return _err(f"Unknown WhatsApp action: '{action}'. Use: send, read", "whatsapp")


# ════════════════════════════════════════════════════════════════════════════
# TASK 6.3 — Spotify Controller
# ════════════════════════════════════════════════════════════════════════════

SPOTIFY_EXE = os.path.join(
    os.getenv("APPDATA", ""),
    "Spotify", "Spotify.exe"
)

# Spotify keyboard shortcuts (work globally when Spotify has focus)
_SPOTIFY_KEYS = {
    "play_pause" : " ",           # Space
    "next"       : "^{RIGHT}",    # Ctrl+Right
    "prev"       : "^{LEFT}",     # Ctrl+Left
    "volume_up"  : "^{UP}",       # Ctrl+Up
    "volume_down": "^{DOWN}",     # Ctrl+Down
    "search"     : "^l",          # Ctrl+L — focus search bar
    "mute"       : "^+{DOWN}",    # Ctrl+Shift+Down
    "shuffle"    : "^+s",         # Ctrl+Shift+S (unofficial)
    "repeat"     : "^+r",         # Ctrl+Shift+R (unofficial)
}


def _launch_spotify() -> Optional[object]:
    """Launches Spotify if not running, returns focused window."""
    win = _focus_window(r"(?i)Spotify", timeout=3)
    if win:
        return win
    exe = SPOTIFY_EXE if os.path.exists(SPOTIFY_EXE) else "spotify"
    try:
        subprocess.Popen([exe])
    except Exception:
        subprocess.Popen(["start", "spotify:"], shell=True)
    time.sleep(5)
    return _focus_window(r"(?i)Spotify", timeout=12)


def spotify_play_pause() -> dict:
    """Toggle play/pause."""
    win = _launch_spotify()
    if win is None:
        return _err("Could not open Spotify.", "spotify")
    win.set_focus()
    time.sleep(0.3)
    send_keys(_SPOTIFY_KEYS["play_pause"])
    return _ok("Toggled play/pause.", "spotify")


def spotify_next() -> dict:
    win = _launch_spotify()
    if win is None:
        return _err("Could not open Spotify.", "spotify")
    win.set_focus()
    send_keys(_SPOTIFY_KEYS["next"])
    time.sleep(0.5)
    return _ok("Skipped to next track.", "spotify")


def spotify_prev() -> dict:
    win = _launch_spotify()
    if win is None:
        return _err("Could not open Spotify.", "spotify")
    win.set_focus()
    send_keys(_SPOTIFY_KEYS["prev"])
    time.sleep(0.5)
    return _ok("Went to previous track.", "spotify")


def spotify_search(query: str) -> dict:
    """Searches for a song/artist and plays the first result."""
    print(f"  [Spotify] Searching: {query}")
    win = _launch_spotify()
    if win is None:
        return _err("Could not open Spotify.", "spotify")

    win.set_focus()
    time.sleep(0.5)

    # Open search
    send_keys(_SPOTIFY_KEYS["search"])
    time.sleep(0.8)

    # Clear existing search and type query
    send_keys("^a")
    time.sleep(0.2)
    pyperclip.copy(query)
    send_keys("^v")
    time.sleep(1.5)
    send_keys("{ENTER}")
    time.sleep(2)

    # Try to play first result: Tab to first item, Enter to play
    send_keys("{TAB}{TAB}")
    time.sleep(0.3)
    send_keys("{ENTER}")
    time.sleep(0.5)

    # Read OCR to confirm what's playing
    ocr = _ocr_read()
    return _ok(f"Searched '{query}' and played first result. Screen: {ocr[:200]}", "spotify")


def spotify_get_current() -> dict:
    """Reads the currently playing track via OCR."""
    win = _launch_spotify()
    if win is None:
        return _err("Spotify not open.", "spotify")
    win.set_focus()
    time.sleep(0.5)
    ocr = _ocr_read()
    return _ok(f"Current Spotify screen text: {ocr[:400]}", "spotify")


def spotify_volume(direction: str) -> dict:
    """Adjusts Spotify volume: 'up' or 'down'."""
    win = _launch_spotify()
    if win is None:
        return _err("Spotify not open.", "spotify")
    win.set_focus()
    key = _SPOTIFY_KEYS["volume_up"] if direction == "up" else _SPOTIFY_KEYS["volume_down"]
    for _ in range(3):
        send_keys(key)
        time.sleep(0.1)
    return _ok(f"Volume {direction}.", "spotify")


def spotify_action(action: str, query: str = "", **kwargs) -> dict:
    """Dispatcher for Spotify actions."""
    a = action.lower().strip()
    if a in ("play_pause", "pause", "resume", "toggle"):
        return spotify_play_pause()
    elif a in ("next", "skip", "next_track"):
        return spotify_next()
    elif a in ("prev", "previous", "back", "prev_track"):
        return spotify_prev()
    elif a in ("search", "play", "find"):
        if not query:
            return _err("Need 'query' for search action.", "spotify")
        return spotify_search(query)
    elif a in ("current", "now_playing", "what_playing"):
        return spotify_get_current()
    elif a in ("volume_up",):
        return spotify_volume("up")
    elif a in ("volume_down",):
        return spotify_volume("down")
    else:
        return _err(f"Unknown Spotify action: '{action}'. Use: play, pause, next, prev, search, current", "spotify")


# ════════════════════════════════════════════════════════════════════════════
# TASK 6.4 — Discord Controller
# ════════════════════════════════════════════════════════════════════════════

DISCORD_EXE = os.path.join(
    os.getenv("LOCALAPPDATA", ""),
    "Discord", "Update.exe"
)

# Discord keyboard shortcuts
_DISCORD_KEYS = {
    "quick_switcher": "^k",       # Ctrl+K — open channel/server quick switcher
    "next_unread":    "^+{DOWN}", # Ctrl+Shift+Down
    "mention_recent": "^+m",      # Ctrl+Shift+M
    "mark_read":      "{ESC}",
    "upload":         "^+u",
    "emoji":          "^e",
}


def _launch_discord() -> Optional[object]:
    """Launches Discord if not running, returns focused window."""
    win = _focus_window(r"(?i)Discord", timeout=3)
    if win:
        return win
    if os.path.exists(DISCORD_EXE):
        subprocess.Popen([DISCORD_EXE, "--processStart", "Discord.exe"])
    else:
        subprocess.Popen(["start", "discord:"], shell=True)
    time.sleep(6)
    return _focus_window(r"(?i)Discord", timeout=15)


def discord_navigate_channel(channel: str) -> dict:
    """
    Uses Discord's Quick Switcher (Ctrl+K) to jump to a channel or server.
    Works like Spotlight: just type the channel name.
    """
    print(f"  [Discord] Navigating to channel: {channel}")
    win = _launch_discord()
    if win is None:
        return _err("Could not open Discord. Is it installed?", "discord")

    win.set_focus()
    time.sleep(0.5)

    # Open quick switcher
    send_keys(_DISCORD_KEYS["quick_switcher"])
    time.sleep(0.8)

    # Clear and type channel name
    send_keys("^a")
    time.sleep(0.2)
    pyperclip.copy(channel)
    send_keys("^v")
    time.sleep(1)

    # Select first result
    send_keys("{DOWN}{ENTER}")
    time.sleep(1)

    return _ok(f"Navigated to channel '{channel}'.", "discord")


def discord_send_message(channel: str, message: str) -> dict:
    """Navigates to a channel and sends a message."""
    print(f"  [Discord] Sending to #{channel}: {message[:40]}")

    # Navigate to the channel
    nav = discord_navigate_channel(channel)
    if not nav["success"]:
        return nav

    # Now find the message input box and type
    win = _focus_window(r"(?i)Discord", timeout=5)
    if win is None:
        return _err("Lost Discord window after navigation.", "discord")

    win.set_focus()
    time.sleep(0.5)

    # Click in the message box area (Tab to input area)
    try:
        # Try UIA child window for message input
        msg_box = win.child_window(
            control_type="Edit",
            title_re=r"(?i)message.*|type.*message"
        )
        msg_box.set_focus()
        msg_box.type_keys(message, with_spaces=True)
        time.sleep(0.3)
        send_keys("{ENTER}")
    except Exception:
        # Fallback: just paste (Discord message box is usually focused after nav)
        pyperclip.copy(message)
        send_keys("^v")
        time.sleep(0.3)
        send_keys("{ENTER}")

    time.sleep(0.5)
    return _ok(f"Sent message to #{channel}: '{message[:60]}'", "discord")


def discord_read_channel(channel: str) -> dict:
    """Navigates to a channel and reads visible messages via OCR."""
    nav = discord_navigate_channel(channel)
    if not nav["success"]:
        return nav
    time.sleep(1)
    ocr = _ocr_read()
    return _ok(f"Channel #{channel} content (OCR): {ocr[:500]}", "discord")


def discord_action(action: str, channel: str = "", message: str = "", **kwargs) -> dict:
    """Dispatcher for Discord actions."""
    a = action.lower().strip()
    if a in ("send", "message", "send_message"):
        if not channel or not message:
            return _err("Need both 'channel' and 'message' parameters.", "discord")
        return discord_send_message(channel, message)
    elif a in ("navigate", "go", "open_channel"):
        if not channel:
            return _err("Need 'channel' parameter.", "discord")
        return discord_navigate_channel(channel)
    elif a in ("read", "read_messages"):
        if not channel:
            return _err("Need 'channel' parameter.", "discord")
        return discord_read_channel(channel)
    else:
        return _err(f"Unknown Discord action: '{action}'. Use: send, navigate, read", "discord")


# ════════════════════════════════════════════════════════════════════════════
# Unified dispatcher (used by agent_orchestrator tool)
# ════════════════════════════════════════════════════════════════════════════

def run_electron_app(app: str, action: str, **kwargs) -> dict:
    """
    Master dispatcher for all Electron app actions.
    Called by the agent as a single tool: electron_app(app, action, **kwargs)
    """
    a = app.lower().strip()
    if a in ("whatsapp", "whatsapp_desktop", "wa"):
        return whatsapp_action(action, **kwargs)
    elif a in ("spotify",):
        return spotify_action(action, **kwargs)
    elif a in ("discord",):
        return discord_action(action, **kwargs)
    else:
        return _err(f"Unknown app: '{app}'. Use: whatsapp, spotify, discord", app)


# ════════════════════════════════════════════════════════════════════════════
# CLI entry point
# ════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="TaskForge Task 6 — Electron App Controller"
    )
    parser.add_argument("--app",     required=True,
                        choices=["whatsapp", "spotify", "discord"],
                        help="Target application")
    parser.add_argument("--action",  required=True,
                        help="Action to perform (e.g. send, play, navigate)")
    parser.add_argument("--contact", default="", help="[WhatsApp] Contact name")
    parser.add_argument("--message", default="", help="Message text to send")
    parser.add_argument("--query",   default="", help="[Spotify] Search query")
    parser.add_argument("--channel", default="", help="[Discord] Channel name")
    parser.add_argument("--n",       type=int, default=5,
                        help="[WhatsApp read] Number of messages")

    args = parser.parse_args()
    result = run_electron_app(
        app=args.app,
        action=args.action,
        contact=args.contact,
        message=args.message,
        query=args.query,
        channel=args.channel,
        n=args.n,
    )
    print("\nResult:", result)
