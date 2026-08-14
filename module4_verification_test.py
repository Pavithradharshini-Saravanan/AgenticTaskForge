"""
Module 4 - Verification Test (Closed-Loop Self-Correction)
Purpose: After typing text into Notepad, READ BACK what actually appears in the
document and compare it to what we meant to type. If it doesn't match, retry.
This is the core "closed-loop" idea behind the whole project: never assume an
action worked - always check, and recover if it didn't.

How to run:
1. Close all Notepad windows/tabs first, for a clean start.
2. Activate your venv: venv\\Scripts\\activate
3. Run: python module4_verification_test.py
"""

import time
import subprocess
import pyperclip
from pywinauto import Application


def open_fresh_notepad():
    """
    Opens a brand new Notepad window and returns (app, main_window, document).
    This is the same reliable open+connect pattern from Module 3.
    """
    print("Opening Notepad...")
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

    document = main_window.child_window(control_type="Document")
    return app, main_window, document


def type_message(main_window, document, message: str):
    """
    Pastes the given message into the document control.
    (Same paste-based approach from Module 3, since it's more reliable than
    simulated keystrokes.)
    """
    document.click_input()
    pyperclip.copy(message)
    main_window.type_keys("^v")


def read_back_text(document) -> str:
    """
    Reads back whatever text currently exists in the document control.
    This is the NEW piece for Module 4 - we're not just writing anymore,
    we're checking what actually got written.
    """
    # get_value() reads the current text content that Windows itself reports
    # for this control - this is the same kind of structural reading pywinauto
    # used in Module 2, just applied to checking instead of discovering.
    try:
        return document.get_value()
    except Exception:
        # Some control types expose text slightly differently - window_text()
        # is a reasonable fallback if get_value() isn't supported.
        return document.window_text()


def type_with_verification(message: str, max_retries: int = 3):
    """
    The full closed-loop sequence:
    1. Open Notepad
    2. Type the message
    3. Read back what's actually there
    4. If it matches -> success
    5. If it doesn't match -> clear and retry, up to max_retries times
    """
    app, main_window, document = open_fresh_notepad()

    for attempt in range(1, max_retries + 1):
        print(f"\nAttempt {attempt}: typing message...")
        type_message(main_window, document, message)

        # Give the UI a brief moment to actually register the paste before we
        # check it - checking instantly can sometimes catch a half-updated state.
        time.sleep(0.5)

        actual_text = read_back_text(document)
        print(f"Verifying... Notepad currently contains: \"{actual_text.strip()}\"")

        if actual_text.strip() == message.strip():
            print(f"\nSUCCESS on attempt {attempt}: text matches exactly.")
            return True
        else:
            print(f"Mismatch detected on attempt {attempt}. Clearing and retrying...")
            # Select all existing text and delete it before retrying.
            main_window.type_keys("^a{DELETE}")
            time.sleep(0.3)

    print(f"\nFAILED after {max_retries} attempts: text never matched correctly.")
    return False


if __name__ == "__main__":
    target_message = "Hello from TaskForge - verified automatically."
    type_with_verification(target_message)
