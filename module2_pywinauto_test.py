"""
Module 2 - Part 2: pywinauto Structure Test
Purpose: Instead of just taking a picture of the screen, this connects directly to
a running app (Notepad) and reads its actual internal structure - what controls exist,
what they're named, and where they are. This is far more reliable than reading pixels.

Prerequisite: Notepad must already be open before running this script.

How to run:
1. Make sure Notepad is open somewhere on your screen
2. Activate your venv: venv\\Scripts\\activate
3. Run: python module2_pywinauto_test.py
"""

from pywinauto import Application


def inspect_notepad():
    """
    Connects to an already-open Notepad window and lists out its controls
    (the text area, menu bar, etc.) along with their names and types.
    """
    try:
        # This connects to an EXISTING Notepad window, rather than opening a new one.
        # "notepad.exe" is the actual process name Windows uses for Notepad.
        app = Application(backend="uia").connect(path="notepad.exe")
    except Exception as e:
        print("Could not find an open Notepad window.")
        print("Make sure Notepad is open, then run this script again.")
        print(f"(Technical detail: {e})")
        return

    # Get the main window of the app
    main_window = app.top_window()

    print(f"Connected to window titled: '{main_window.window_text()}'\n")
    print("Listing all controls pywinauto can see inside this window:\n")

    # This prints a tree of every control (buttons, text areas, menus) pywinauto
    # can detect inside the window, along with their type and name.
    main_window.print_control_identifiers()


if __name__ == "__main__":
    inspect_notepad()
