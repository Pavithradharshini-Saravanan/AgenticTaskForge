"""
Module 6 - Part 1: File Explorer Perception Test
Purpose: Open File Explorer at a specific folder and read back the actual list
of files/folders present, using pywinauto's structural reading (the ListView
control), rather than screenshots. This is the new "perception" skill needed
before we can do anything useful with File Explorer (renaming, moving, etc.).

How to run:
1. Close any open File Explorer windows first, for a clean test.
2. Activate your venv: venv\\Scripts\\activate
3. Run: python module6_explorer_perception_test.py
"""

import os
import time
import subprocess
from pywinauto import Application


def open_explorer_at(folder_path: str):
    """
    Opens File Explorer directly at the given folder path.
    Windows lets you do this by launching explorer.exe with the path as an
    argument - much simpler and more reliable than opening Explorer generically
    and then navigating through folders by clicking.
    """
    print(f"Opening File Explorer at: {folder_path}")
    subprocess.Popen(["explorer.exe", folder_path])
    time.sleep(2)  # Explorer windows take a moment to fully appear


def find_explorer_window(folder_name: str):
    """
    Connects to the File Explorer window. Explorer windows are typically
    titled after the folder they're showing (e.g. "Downloads"), so we search
    for that specific title rather than matching by process name - multiple
    Explorer windows can be open at once under the same process.
    """
    app = None
    for attempt in range(10):
        time.sleep(1)
        try:
            app = Application(backend="uia").connect(title_re=f".*{folder_name}.*")
            break
        except Exception:
            print(f"  Waiting for Explorer window... (attempt {attempt + 1}/10)")

    if app is None:
        raise RuntimeError(f"Could not find an Explorer window titled like '{folder_name}'.")

    return app.top_window()


def list_files_in_explorer(explorer_window) -> list:
    """
    Reads the actual list of file/folder names shown in the Explorer window.

    CONFIRMED from debug inventory: the file view has NO auto_id, but its
    title/name is literally "Items View". We target it by title instead.
    Also note: files may be grouped under date-based headers ("Earlier this
    week", "Last week", etc., shown as Group controls) - we only want the
    actual ListItem children (the files themselves), not the group headers.
    """
    file_list_control = explorer_window.child_window(title="Items View", control_type="List")
    items = file_list_control.descendants(control_type="ListItem")

    names = [item.window_text() for item in items]
    return names


if __name__ == "__main__":
    target_folder = os.path.join(os.path.expanduser("~"), "Downloads")
    folder_display_name = "Downloads"

    open_explorer_at(target_folder)
    explorer_window = find_explorer_window(folder_display_name)

    print(f"Connected to Explorer window: '{explorer_window.window_text()}'\n")
    print("Reading file/folder list...\n")

    files = list_files_in_explorer(explorer_window)

    if files:
        print(f"Found {len(files)} items in {folder_display_name}:")
        for name in files:
            print(f"  - {name}")
    else:
        print("No items found, or the list could not be read correctly.")
        print("Printing a DEDUPLICATED inventory of every control type present:\n")

        # Our narrower guess (List/ListItem/DataGrid/etc.) found nothing useful,
        # meaning the file view is exposed under a control_type we didn't
        # anticipate. Instead of guessing again, we print every unique
        # (control_type, auto_id) combination seen anywhere in the tree - this
        # is deduplicated so it stays short, but complete enough to reveal
        # exactly what's actually available to target.
        all_descendants = explorer_window.descendants()
        print(f"(Total descendant controls found: {len(all_descendants)})\n")

        seen_combinations = set()
        for ctrl in all_descendants:
            try:
                ctrl_type = ctrl.element_info.control_type
                auto_id = ctrl.element_info.automation_id
                key = (ctrl_type, auto_id)
                if key not in seen_combinations:
                    seen_combinations.add(key)
                    name = ctrl.window_text()
                    print(f"  control_type='{ctrl_type}'  auto_id='{auto_id}'  example_name='{name}'")
            except Exception:
                continue