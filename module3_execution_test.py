"""
Module 3 - Execution Test
Purpose: Open Notepad completely from scratch (not connecting to an already-open one),
type text into it automatically, and confirm the action worked - no manual clicking.

Prerequisite: Close any Notepad windows you already have open before running this,
so we start from a clean state.

How to run:
1. Activate your venv: venv\\Scripts\\activate
2. Run: python module3_execution_test.py
3. Watch your screen - Notepad should open by itself and text should appear,
   typed automatically.
"""

import time
import subprocess
import pyperclip
from pywinauto import Application


def open_notepad_and_type(message: str):
    """
    Opens a brand new Notepad window and types the given message into it.
    """
    print("Opening Notepad...")

    # NOTE: Windows 11's built-in Notepad is a "packaged app" (delivered through the
    # Microsoft Store style system), not a classic Win32 program. pywinauto's
    # Application().start() waits for a signal called WaitForInputIdle that packaged
    # apps don't send, which causes it to hang or fail. So instead, we launch Notepad
    # the same way Windows itself would (via subprocess), then separately CONNECT to
    # it - exactly like we did successfully in Module 2 - retrying a few times since
    # the window may take a moment to become available after launch.
    subprocess.Popen(["notepad.exe"])

    app = None
    max_attempts = 10
    for attempt in range(max_attempts):
        time.sleep(1)
        try:
            app = Application(backend="uia").connect(path="notepad.exe")
            break
        except Exception:
            print(f"Waiting for Notepad to be ready... (attempt {attempt + 1}/{max_attempts})")

    if app is None:
        raise RuntimeError("Could not connect to Notepad after several attempts.")

    main_window = app.top_window()
    main_window.wait("visible", timeout=15)

    print(f"Notepad window found: '{main_window.window_text()}'")

    # From what we saw in Module 2, the typing area is a 'Document' control,
    # not the older 'Edit' control. We find it by its control type.
    document = main_window.child_window(control_type="Document")

    print(f"Typing message: \"{message}\"")

    # click_input() actually moves the mouse and clicks, just like a real user,
    # to make sure the text area has focus before we type.
    document.click_input()

    # IMPORTANT: instead of simulating individual keystrokes (which can drop or
    # scramble characters on modern packaged apps, as we just saw), we put the
    # text on the clipboard and paste it in one action. This is far more reliable
    # because the whole string arrives as a single paste event, not dozens of
    # separate simulated key presses that can be missed or reordered.
    pyperclip.copy(message)
    main_window.type_keys("^v")  # Ctrl+V

    print("Done. Check your screen - Notepad should now show the typed message.")


if __name__ == "__main__":
    open_notepad_and_type("Hello from TaskForge - this text was typed automatically.")