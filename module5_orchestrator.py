"""
Module 5 - Orchestrator
Purpose: This is where it all comes together. You type a plain-English command,
the local AI (Module 1) turns it into a step-by-step plan using ONLY a small set
of safe, pre-approved actions, and the orchestrator executes each step using the
functions we already built and tested in Modules 2-4 - verifying as it goes.

Allowed actions (deliberately kept small and reliable for now):
  - open_app     : opens Notepad
  - type_text    : types/pastes text into the open Notepad window (verified)
  - save_file    : saves the current Notepad content with a given filename

How to run:
1. Close all Notepad windows first.
2. Make sure Ollama is running and phi3:mini is available.
3. Activate your venv: venv\\Scripts\\activate
4. Run: python module5_orchestrator.py
5. Type a command when prompted, e.g.:
     "Open notepad, write 'Meeting notes for today', and save it as notes.txt"
"""

import os
import time
import json
import subprocess
import requests
import re
import pyperclip
import win32gui
import difflib
from pywinauto import Application, Desktop

OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL_NAME = "phi3:mini"  # change this if you're using a different model name


# ---------------------------------------------------------------------------
# STEP A: Ask the local AI to turn the user's command into a structured plan
# ---------------------------------------------------------------------------

CONTENT_PROMPT = """Write the actual content requested below. Output ONLY the
finished content itself - never repeat, summarize, or restate the instructions.
Do not mention "the user", "the request", or any meta-commentary. Do not include
words like "Save as" or filenames in your output - just the content itself.
Keep your answer reasonably concise - a few sentences per point is enough, no
need to over-explain. Do not repeat any line or sentence you've already written.

WARNING: Do NOT simply repeat the instruction back as your answer. The instruction
describes WHAT to write, not what your output should literally say.

Example 1:
Instruction: "write a short note reminding me to buy milk"
Correct output: Remember to buy milk on the way home today.
WRONG output (do not do this): "Write a short note reminding me to buy milk"

Example 2:
Instruction: "plan a study schedule for learning Python over one week"
Correct output:
Monday: Learn Python basics - variables, data types, and print statements.
Tuesday: Practice loops and conditional statements.
Wednesday: Learn functions and how to organize code.
Thursday: Practice working with lists and dictionaries.
Friday: Build a small project combining everything learned.
Saturday: Review mistakes and revise weak topics.
Sunday: Take a short quiz or solve practice problems.

Now do the same for this instruction:
"{user_request}"

Correct output:"""

FILENAME_PROMPT = """The user asked for the following: "{user_request}"

What filename did they want to save this as? Reply with ONLY the bare
filename and extension - do NOT include any folder name or path, just the
file name itself. If no filename was mentioned, reply with exactly: notes.txt

Examples of valid replies:
studyplan.txt
notes.txt
todo.txt

Filename:"""


EXPLICIT_PATH_PATTERN = re.compile(r"[A-Za-z]:[\\/].+$")


def extract_explicit_path(user_request: str):
    """
    Detects a literal Windows absolute path (e.g. "D:\\MSME\\ECODRONENET")
    mentioned in the request. IMPORTANT: this must be checked BEFORE falling
    back to the known-folder keyword matching below - a request naming a
    specific custom folder should never silently default to "documents" just
    because it didn't contain the word "document".
    """
    match = EXPLICIT_PATH_PATTERN.search(user_request)
    if match:
        return match.group(0).rstrip(".,;!?\"'` ")
    return None


def detect_target_folder(user_request: str) -> str:
    """
    IMPORTANT: relying on the AI to correctly combine "folder/filename" into
    one string proved unreliable - it sometimes dropped the folder mention
    entirely even when the user clearly asked for it. Instead, we detect the
    target folder ourselves in plain Python by keyword search, which is
    completely deterministic and doesn't depend on the small model getting
    the combination right. The AI is now only responsible for the much
    simpler, more reliable task of extracting the bare filename.

    Returns either a known-folder keyword ("downloads"/"desktop"/"documents")
    OR a literal absolute path string if one was explicitly mentioned.
    """
    explicit_path = extract_explicit_path(user_request)
    if explicit_path:
        return explicit_path

    lowered = user_request.lower()
    if "download" in lowered:
        return "downloads"
    if "desktop" in lowered:
        return "desktop"
    if "document" in lowered:
        return "documents"
    return "documents"  # sensible default if no folder is mentioned at all


def ask_ai(prompt: str, temperature: float = 0.4) -> str:
    # IMPORTANT: lowering temperature (default Ollama temperature is often ~0.8)
    # makes the model stick more closely to what's actually asked, reducing
    # vague, generic, or randomly-drifting output. We don't set it to 0 entirely,
    # since a small amount of variation still helps avoid overly robotic text.
    payload = {
        "model": MODEL_NAME,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": temperature,
            "top_p": 0.9,
        }
    }
    response = requests.post(OLLAMA_URL, json=payload)
    if response.status_code != 200:
        raise RuntimeError(f"Ollama error: {response.status_code} - {response.text}")
    return response.json()["response"].strip()


ECHO_TRIGGER_PHRASES = [
    "open notepad", "open note pad", "save this as", "save it as",
    "save as", "in the downloads folder", "in downloads folder",
    "within downloads folder", "create a document named",
    "please note", "as per your request", "as requested",
    "i've followed", "i have followed", "your instructions",
    "your request", "as per the instructions", "note that while",
]


def clean_meta_commentary(content: str) -> str:
    """
    Defense-in-depth cleanup applied regardless of how well the prompt worked:
    strips bracketed meta-notes like "[Further detail continues...]" and any
    trailing sentence that talks ABOUT the instructions rather than being the
    actual content itself. This runs even after regeneration, since small
    models can slip meta-commentary in unpredictably.
    """
    # Remove anything in square brackets - real content rarely needs these,
    # and this is exactly where the model tends to insert self-referential notes.
    content = re.sub(r"\[.*?\]", "", content)

    # Split into sentences and drop any sentence that talks about "your
    # instructions/request" rather than being real content.
    meta_indicators = ["please note", "as per your", "as requested", "i've followed",
                        "i have followed", "your instructions", "your request"]
    sentences = re.split(r"(?<=[.!?])\s+", content)
    cleaned_sentences = [
        s for s in sentences
        if not any(indicator in s.lower() for indicator in meta_indicators)
    ]
    return " ".join(cleaned_sentences).strip()


def looks_like_echoed_instruction(content: str) -> bool:
    """
    Detects whether the generated 'content' actually leaked meta-instructions
    from the original request (e.g. "Open Notepad, create a document named...")
    instead of containing only the real content. This is a known failure mode
    of small local models - this check catches it automatically so we can
    regenerate instead of silently pasting junk into the document.
    """
    lowered = content.lower()
    return any(phrase in lowered for phrase in ECHO_TRIGGER_PHRASES)


def looks_too_vague(content: str, min_words: int = 40) -> bool:
    """
    A simple, generalizable proxy for 'this content is too short/generic to be
    useful' - small models sometimes produce a one or two sentence non-answer
    instead of properly elaborating. Word count isn't a perfect quality signal,
    but it's a cheap, reliable enough check to catch the worst cases and
    trigger a regeneration with a more explicit "elaborate further" instruction.
    """
    word_count = len(content.split())
    return word_count < min_words


TASK_TYPE_PROMPT = """The user gave this instruction: "{user_request}"

Classify this instruction into EXACTLY ONE of these two categories:

1. CONTENT_CREATION - the user wants new content written (a note, schedule,
   letter, poem, list, etc.) and saved as a file.
2. FILE_RENAME - the user wants to rename an EXISTING file that already
   exists on their computer.

Reply with ONLY one word: either CONTENT_CREATION or FILE_RENAME. Nothing else.

Category:"""

RENAME_EXTRACTION_PROMPT = """The user gave this instruction: "{user_request}"

Extract exactly two things:
1. The CURRENT name of the file they want to rename (including its extension if mentioned)
2. The NEW name they want to give it (including its extension if mentioned)

Reply in EXACTLY this format, nothing else:
OLD: <current filename>
NEW: <new filename>

Example:
Instruction: "rename studyplan.txt to notes.txt in downloads"
OLD: studyplan.txt
NEW: notes.txt

Now do the same for the real instruction above.
"""


def classify_task_type(user_request: str) -> str:
    result = ask_ai(TASK_TYPE_PROMPT.replace("{user_request}", user_request), temperature=0.1)
    result = result.strip().upper()
    if "RENAME" in result:
        return "FILE_RENAME"
    return "CONTENT_CREATION"  # default/fallback - safer than guessing something more destructive


def extract_rename_details(user_request: str) -> tuple:
    result = ask_ai(RENAME_EXTRACTION_PROMPT.replace("{user_request}", user_request), temperature=0.1)

    old_name, new_name = None, None
    for line in result.splitlines():
        line = line.strip()
        if line.upper().startswith("OLD:"):
            old_name = line.split(":", 1)[1].strip()
        elif line.upper().startswith("NEW:"):
            new_name = line.split(":", 1)[1].strip()

    if not old_name or not new_name:
        raise ValueError(f"Could not extract old/new filenames from AI response:\n{result}")

    return old_name, new_name


def build_rename_plan(user_request: str) -> list:
    old_name, new_name = extract_rename_details(user_request)
    target_folder = detect_target_folder(user_request)
    print(f"  Detected rename request: '{old_name}' -> '{new_name}' in folder '{target_folder}'")

    return [
        {"action": "open_folder", "folder": target_folder},
        {"action": "rename_file", "old_name": old_name, "new_name": new_name},
    ]


def get_plan_from_ai(user_request: str) -> list:
    """
    Instead of asking the AI to produce a full JSON plan in one shot (which small
    models like phi3:mini are unreliable at, especially when ALSO asked to write
    creative content), we split this into two small, focused AI calls, then build
    the JSON plan ourselves in Python. This removes JSON-formatting failures
    almost entirely, since the AI never has to produce JSON at all.
    """
    print("Classifying task type...")
    task_type = classify_task_type(user_request)
    print(f"  Task type: {task_type}")

    if task_type == "FILE_RENAME":
        return build_rename_plan(user_request)

    # --- Otherwise, fall through to the existing content-creation flow ---
    print("Asking AI to generate the content...")
    content = ask_ai(CONTENT_PROMPT.replace("{user_request}", user_request))

    # If the model leaked meta-instructions into the content (a known failure
    # mode), regenerate up to 2 more times with an even more forceful reminder,
    # instead of silently proceeding with broken content.
    retry_count = 0
    while (looks_like_echoed_instruction(content) or looks_too_vague(content)) and retry_count < 2:
        retry_count += 1
        if looks_like_echoed_instruction(content):
            print(f"  Detected echoed instructions in content, regenerating (attempt {retry_count + 1})...")
            forceful_prompt = (
                CONTENT_PROMPT.replace("{user_request}", user_request)
                + "\n\nIMPORTANT REMINDER: Your previous attempt incorrectly included words like "
                  "'open notepad', 'save as', or 'downloads folder'. Do NOT mention notepad, "
                  "saving, filenames, or folders anywhere in your answer. Write ONLY the actual "
                  "content itself, starting immediately with the first real sentence."
            )
        else:
            print(f"  Content was too short/vague, regenerating with more detail (attempt {retry_count + 1})...")
            forceful_prompt = (
                CONTENT_PROMPT.replace("{user_request}", user_request)
                + "\n\nIMPORTANT REMINDER: Your previous attempt was too short and generic. "
                  "Write a properly detailed, specific answer - include concrete points, "
                  "examples, or day-by-day breakdown where relevant. Do not give a vague "
                  "one or two sentence summary."
            )
        content = ask_ai(forceful_prompt)

    # Final safety net regardless of how the above went - strip any remaining
    # bracketed notes or meta-commentary sentences that slipped through.
    content = clean_meta_commentary(content)

    print("Asking AI to determine the filename...")
    filename_raw = ask_ai(FILENAME_PROMPT.replace("{user_request}", user_request))

    # Defensive cleanup - small models sometimes add quotes or extra words.
    bare_filename = filename_raw.strip().strip('"').strip("'").split("\n")[0].strip()
    if not bare_filename:
        bare_filename = "notes.txt"
    # Defensive: if the AI still included a folder-like prefix despite being
    # told not to, strip it - we're handling the folder ourselves below.
    if "/" in bare_filename:
        bare_filename = bare_filename.rsplit("/", 1)[-1]

    # Folder is detected deterministically in Python, not left to the AI.
    target_folder = detect_target_folder(user_request)
    filename = f"{target_folder}/{bare_filename}"
    print(f"  Detected target folder: '{target_folder}', filename: '{bare_filename}'")

    # We build the plan in plain Python - no AI-generated JSON involved at all.
    plan = [
        {"action": "open_app"},
        {"action": "type_text", "value": content},
        {"action": "save_file", "filename": filename},
    ]
    return plan


def validate_plan(plan: list) -> bool:
    """
    Safety check: make sure every step in the plan uses ONLY the allowed actions
    and has the required fields. This is what prevents the AI from doing anything
    unexpected - we execute nothing it hasn't correctly specified.
    """
    allowed_actions = {"open_app", "type_text", "save_file", "open_folder", "rename_file"}

    for step in plan:
        action = step.get("action")
        if action not in allowed_actions:
            print(f"Rejected plan: unknown action '{action}'")
            return False
        if action == "type_text" and "value" not in step:
            print("Rejected plan: type_text step missing 'value' field")
            return False
        if action == "save_file" and "filename" not in step:
            print("Rejected plan: save_file step missing 'filename' field")
            return False
        if action == "open_folder" and "folder" not in step:
            print("Rejected plan: open_folder step missing 'folder' field")
            return False
        if action == "rename_file" and ("old_name" not in step or "new_name" not in step):
            print("Rejected plan: rename_file step missing 'old_name' or 'new_name' field")
            return False

    return True


# ---------------------------------------------------------------------------
# STEP B: The actual execution functions (from Modules 2-4, reused here)
# ---------------------------------------------------------------------------

_app = None
_main_window = None
_document = None
_explorer_window = None  # tracks the currently-open File Explorer window, for file-management actions
_current_folder_path = None  # tracks the folder path we intend to be viewing, for defensive re-navigation


def action_open_app():
    global _app, _main_window, _document

    print("Executing: open_app")

    # IMPORTANT: if a previous run crashed or left a Notepad window open in the
    # background, connect(path="notepad.exe") can accidentally attach to that
    # STALE process instead of the one we're about to launch - which is exactly
    # what caused the last failure ("No windows for that process could be
    # found"). To guarantee a clean slate, we force-close any existing Notepad
    # processes first, before launching a fresh one.
    subprocess.run(["taskkill", "/F", "/IM", "notepad.exe", "/T"],
                    capture_output=True, text=True)
    time.sleep(1)  # give Windows a moment to fully release the process

    subprocess.Popen(["notepad.exe"])

    app = None
    for attempt in range(10):
        time.sleep(1)
        try:
            app = Application(backend="uia").connect(path="notepad.exe")
            break
        except Exception:
            print(f"  Waiting for Notepad... (attempt {attempt + 1}/10)")

    if app is None:
        raise RuntimeError("Could not connect to Notepad.")

    main_window = app.top_window()
    main_window.wait("visible", timeout=15)
    time.sleep(1)  # extra settle time - "visible" can fire slightly before the window is fully interactive

    _app = app
    _main_window = main_window
    _document = main_window.child_window(control_type="Document")

    print(f"  Notepad opened: '{main_window.window_text()}'")


def action_type_text(value: str, max_retries: int = 3):
    print(f"Executing: type_text -> \"{value[:60]}{'...' if len(value) > 60 else ''}\" ({len(value)} characters)")

    if _document is None:
        raise RuntimeError("No Notepad window is open yet. open_app must run first.")

    # Longer content needs more time to actually render/register after a paste,
    # and more time for a select-all+copy to capture everything. We scale the
    # wait times based on content length instead of using one fixed short delay
    # that only worked for shorter test messages.
    paste_wait = max(0.7, len(value) / 800)
    copy_wait = max(0.3, len(value) / 1500)

    for attempt in range(1, max_retries + 1):
        _main_window.set_focus()
        time.sleep(0.3)
        _document.click_input()
        time.sleep(0.2)

        pyperclip.copy(value)
        time.sleep(0.2)
        _document.type_keys("^v")
        time.sleep(paste_wait)

        # IMPORTANT: get_value()/window_text() proved unreliable for multi-line
        # content in this control - it was silently truncating to a single line,
        # causing false "mismatch" reports even when the paste actually worked
        # correctly. Instead, we verify by selecting all the text and copying it
        # back out to the clipboard, then reading the clipboard directly - this
        # gives us the true, complete content, not a possibly-truncated UI report.
        _document.type_keys("^a^c")
        time.sleep(copy_wait)
        actual_text = pyperclip.paste()

        # IMPORTANT: Windows converts each \n line break into \r\n when text is
        # pasted into Notepad. When we copy it back out, those \r\n sequences
        # come back too - adding exactly one extra character per line break,
        # even though the actual content is correct. We normalize both sides to
        # plain \n before comparing, so this Windows line-ending convention
        # doesn't cause a false mismatch.
        normalized_actual = actual_text.replace("\r\n", "\n").replace("\r", "\n").strip()
        normalized_expected = value.replace("\r\n", "\n").replace("\r", "\n").strip()

        if normalized_actual == normalized_expected:
            print(f"  Verified correct on attempt {attempt}.")
            return
        else:
            preview = actual_text.strip()[:60] if actual_text.strip() else "(completely empty)"
            print(f"  Mismatch on attempt {attempt}. Expected {len(normalized_expected)} chars, "
                  f"got {len(normalized_actual)} chars. Notepad currently shows: \"{preview}\". Retrying...")
            _main_window.set_focus()
            _document.type_keys("^a{DELETE}")
            time.sleep(0.3)

    raise RuntimeError("type_text failed verification after all retries.")


def action_save_file(filename: str):
    print(f"Executing: save_file -> \"{filename}\"")

    if _main_window is None:
        raise RuntimeError("No Notepad window is open yet. open_app must run first.")

    # If the AI mentioned a known folder (Downloads, Desktop, Documents), resolve
    # it to a real absolute path. Windows' Save dialog accepts a full path typed
    # directly into the filename field and will save there directly - no need to
    # click through folders manually.
    home = os.path.expanduser("~")
    known_folders = {
        "downloads": os.path.join(home, "Downloads"),
        "desktop": os.path.join(home, "Desktop"),
        "documents": os.path.join(home, "Documents"),
    }

    normalized = filename.replace("\\", "/")
    if "/" in normalized:
        folder_part, name_part = normalized.rsplit("/", 1)
        folder_key = folder_part.strip().lower()
        if folder_key in known_folders:
            full_path = os.path.join(known_folders[folder_key], name_part)
        else:
            # Unknown folder mentioned - fall back to Documents to avoid guessing wrong.
            full_path = os.path.join(known_folders["documents"], name_part)
    else:
        # No folder mentioned at all - default to Documents.
        full_path = os.path.join(known_folders["documents"], filename)

    print(f"  Resolved save path: {full_path}")

    # Explicitly refocus the Notepad window before sending Ctrl+S. Without this,
    # focus can end up elsewhere after the previous step's clipboard operations,
    # causing Ctrl+S to go nowhere and the Save dialog to never open at all -
    # exactly what happened in the last run.
    _main_window.set_focus()
    time.sleep(0.3)
    if _document is not None:
        _document.click_input()
    time.sleep(0.3)

    # Ctrl+S opens the Save dialog
    _main_window.type_keys("^s")
    time.sleep(0.5)

    # IMPORTANT: pywinauto's UIA-based Desktop().windows() proved to silently
    # MISS this Save dialog entirely, even when it was clearly open on screen -
    # not a timing issue, a detection issue. Instead, we use win32gui.EnumWindows,
    # the actual low-level Windows OS API for listing every top-level window,
    # bypassing whatever UIA was failing to report.
    def find_window_handle_by_title(keyword: str):
        matches = []

        def enum_handler(hwnd, _):
            if win32gui.IsWindowVisible(hwnd):
                title = win32gui.GetWindowText(hwnd)
                if keyword.lower() in title.lower():
                    matches.append((hwnd, title))

        win32gui.EnumWindows(enum_handler, None)
        return matches

    save_hwnd = None
    seen_titles = []
    max_wait_attempts = 20
    for attempt in range(max_wait_attempts):
        time.sleep(1)
        matches = find_window_handle_by_title("save")
        if matches:
            save_hwnd = matches[0][0]
            break
        # collect all visible titles for debugging in case we still fail
        seen_titles = []
        win32gui.EnumWindows(
            lambda h, _: seen_titles.append(win32gui.GetWindowText(h))
            if win32gui.IsWindowVisible(h) and win32gui.GetWindowText(h) else None,
            None
        )
        print(f"  Waiting for Save dialog... (attempt {attempt + 1}/{max_wait_attempts})")

    if save_hwnd is None:
        raise RuntimeError(
            f"Could not find the Save dialog after several attempts. "
            f"Currently visible window titles were: {seen_titles}"
        )

    # Wrap the raw window handle we found using win32gui into a pywinauto
    # control object, so we can keep using pywinauto's click/type_keys methods
    # on it exactly as before.
    save_window = Application(backend="uia").connect(handle=save_hwnd).window(handle=save_hwnd)
    save_window.wait("visible", timeout=10)

    # The filename field in a Windows Save dialog is typically an editable
    # ComboBox. We find it, then VERIFY what actually ends up in it before
    # pressing Enter - the same closed-loop principle from Module 4, applied
    # here because Windows sometimes auto-suggests a filename from the
    # document's content, which can silently survive a naive overwrite attempt.
    filename_field = save_window.child_window(control_type="Edit", found_index=0)

    saved_correctly = False
    for attempt in range(1, 4):
        filename_field.click_input()
        filename_field.type_keys("^a")       # select all existing text
        filename_field.type_keys("{DELETE}")  # explicitly delete it (safer than relying on paste to overwrite)
        time.sleep(0.2)

        pyperclip.copy(full_path)
        filename_field.type_keys("^v")
        time.sleep(0.3)

        try:
            current_value = filename_field.get_value()
        except Exception:
            current_value = filename_field.window_text()

        if current_value.strip() == full_path.strip():
            print(f"  Filename field verified correct on attempt {attempt}.")
            saved_correctly = True
            break
        else:
            print(f"  Filename field shows unexpected value on attempt {attempt}: \"{current_value}\". Retrying...")

    if not saved_correctly:
        raise RuntimeError(
            f"Could not get the correct path into the Save dialog's filename field "
            f"after several attempts. Last seen value did not match \"{full_path}\"."
        )

    save_window.type_keys("{ENTER}")
    time.sleep(1.5)

    # IMPORTANT: don't just assume the save worked because we pressed Enter -
    # actually check the file exists at the path we intended, the same
    # closed-loop principle used everywhere else in this system. If a
    # "Confirm Save As" overwrite dialog or some other unexpected prompt
    # appeared instead, the file won't exist yet, and we want to know that
    # clearly rather than silently reporting success.
    for check_attempt in range(5):
        if os.path.exists(full_path):
            print(f"  Confirmed: file actually exists at {full_path}")
            return
        time.sleep(1)

    raise RuntimeError(
        f"Save appears to have completed, but no file was found at {full_path}. "
        f"An unexpected dialog (e.g. overwrite confirmation) may have appeared."
    )


def action_open_folder(folder: str):
    """
    Opens File Explorer at either a known folder keyword (downloads/desktop/
    documents) OR a literal absolute path (e.g. "D:\\MSME\\ECODRONENET") if
    one was explicitly given. Previously this silently defaulted to Downloads
    for anything it didn't recognize, which caused a real bug: a request
    naming a specific custom folder got redirected to the wrong location
    entirely, with no warning that the requested folder was never opened.
    """
    global _explorer_window, _current_folder_path

    print(f"Executing: open_folder -> \"{folder}\"")

    home = os.path.expanduser("~")
    known_folders = {
        "downloads": os.path.join(home, "Downloads"),
        "desktop": os.path.join(home, "Desktop"),
        "documents": os.path.join(home, "Documents"),
    }

    if folder.lower() in known_folders:
        folder_path = known_folders[folder.lower()]
    elif os.path.isabs(folder) or re.match(r"^[A-Za-z]:[\\/]", folder):
        # An explicit absolute path was given - use it directly rather than
        # silently substituting a default folder.
        folder_path = folder
        if not os.path.isdir(folder_path):
            raise RuntimeError(
                f"The specified folder does not exist or is not accessible: '{folder_path}'"
            )
    else:
        print(f"  Warning: '{folder}' was not recognized as a known folder or valid path - "
              f"defaulting to Downloads.")
        folder_path = known_folders["downloads"]

    _current_folder_path = folder_path
    folder_display_name = os.path.basename(folder_path.rstrip("\\/"))

    # IMPORTANT: Windows File Explorer often reuses an EXISTING window and adds
    # a new tab instead of opening a genuinely fresh window - this caused the
    # window's title to correctly say "Downloads" while the actually-visible
    # tab still showed stale content from a previous session. We fix this by
    # closing existing File Explorer windows first. "CabinetWClass" is the
    # specific window class for individual Explorer windows - this is NOT the
    # desktop shell (Progman) or taskbar, so closing these is safe and won't
    # disrupt the desktop the way killing explorer.exe entirely would.
    def close_existing_explorer_windows(hwnd, _):
        class_name = win32gui.GetClassName(hwnd)
        if class_name == "CabinetWClass":
            win32gui.PostMessage(hwnd, 0x0010, 0, 0)  # WM_CLOSE

    win32gui.EnumWindows(close_existing_explorer_windows, None)
    time.sleep(1)

    subprocess.Popen(["explorer.exe", folder_path])
    time.sleep(2)

    # IMPORTANT: Application().connect(title_re=...) proved unreliable for
    # finding windows earlier (same issue we hit with the Save dialog) - it can
    # silently fail to find a window even when it's genuinely open. We use the
    # same fix here: directly enumerate all OS-level windows via win32gui and
    # match by title, which is far more reliable.
    explorer_hwnd = None
    seen_titles = []
    for attempt in range(15):
        time.sleep(1)
        matches = []

        def enum_handler(hwnd, _):
            if win32gui.IsWindowVisible(hwnd):
                title = win32gui.GetWindowText(hwnd)
                if folder_display_name.lower() in title.lower():
                    matches.append((hwnd, title))

        win32gui.EnumWindows(enum_handler, None)
        if matches:
            explorer_hwnd = matches[0][0]
            break

        seen_titles = []
        win32gui.EnumWindows(
            lambda h, _: seen_titles.append(win32gui.GetWindowText(h))
            if win32gui.IsWindowVisible(h) and win32gui.GetWindowText(h) else None,
            None
        )
        print(f"  Waiting for Explorer window... (attempt {attempt + 1}/15)")

    if explorer_hwnd is None:
        raise RuntimeError(
            f"Could not find an Explorer window for '{folder_display_name}'. "
            f"Currently visible window titles were: {seen_titles}"
        )

    _explorer_window = Application(backend="uia").connect(handle=explorer_hwnd).window(handle=explorer_hwnd)
    _explorer_window.wait("visible", timeout=10)
    print(f"  Connected to Explorer window: '{_explorer_window.window_text()}'")


def _get_file_list_control():
    return _explorer_window.child_window(title="Items View", control_type="List")


def _list_files_in_explorer() -> list:
    file_list_control = _get_file_list_control()
    items = file_list_control.descendants(control_type="ListItem")
    return [item.window_text() for item in items]


def action_rename_file(old_name: str, new_name: str, max_retries: int = 3):
    """
    Renames a file via the F2 shortcut and verifies the rename actually took
    effect by re-reading the folder's file list - adapted directly from
    Module 6's tested rename script.
    """
    print(f"Executing: rename_file -> \"{old_name}\" to \"{new_name}\"")

    if _explorer_window is None:
        raise RuntimeError("No Explorer window is open yet. open_folder must run first.")

    for attempt in range(1, max_retries + 1):
        print(f"  Attempt {attempt}...")

        # IMPORTANT: don't trust that the Explorer window is still showing the
        # folder we expect - a misfired click can navigate INTO a subfolder
        # (e.g. a double-click instead of a single select), silently changing
        # what's visible. We defensively re-navigate to the known target
        # folder via the address bar before every attempt, using Ctrl+L (the
        # standard Explorer shortcut to focus the address bar) - this makes
        # each attempt start from a guaranteed-correct location.
        if _current_folder_path:
            _explorer_window.set_focus()
            time.sleep(0.3)
            _explorer_window.type_keys("^l")
            time.sleep(0.3)
            pyperclip.copy(_current_folder_path)
            _explorer_window.type_keys("^v")
            time.sleep(0.2)
            _explorer_window.type_keys("{ENTER}")
            time.sleep(1.5)

        current_files = _list_files_in_explorer()

        # First try an exact match. If that fails, fall back to fuzzy matching -
        # the AI's extraction step can occasionally mis-extract a slightly wrong
        # name (missing extension, minor wording difference), and requiring an
        # exact match would fail unnecessarily in those cases.
        exact_matches = [
            item for item in _get_file_list_control().descendants(control_type="ListItem")
            if item.window_text() == old_name
        ]

        if exact_matches:
            target_item = exact_matches[0]
        else:
            close_matches = difflib.get_close_matches(old_name, current_files, n=1, cutoff=0.5)
            if not close_matches:
                raise RuntimeError(
                    f"Could not find '{old_name}' or anything similar in the folder. "
                    f"Actual files present: {current_files}"
                )
            best_match_name = close_matches[0]
            print(f"  No exact match for '{old_name}', using closest match instead: '{best_match_name}'")
            matching_items = [
                item for item in _get_file_list_control().descendants(control_type="ListItem")
                if item.window_text() == best_match_name
            ]
            target_item = matching_items[0]
            old_name = best_match_name  # use the real name for the rest of this attempt

        # IMPORTANT: only the very first click uses target_item directly (to
        # select and focus it). All keystrokes AFTER that go through the
        # window itself, not the specific item reference - because Explorer
        # can re-sort the list live as you type a new name, which recreates
        # the underlying UI element and makes the original target_item
        # reference stale/invalid. Keyboard input goes to whatever currently
        # has focus regardless, so the window-level reference (which stays
        # valid) is the safer target for every step after the initial click.
        target_item.click_input()
        time.sleep(0.3)
        _explorer_window.type_keys("{F2}")
        time.sleep(0.5)
        _explorer_window.type_keys("^a")
        time.sleep(0.2)
        _explorer_window.type_keys(new_name, with_spaces=True)
        time.sleep(0.2)
        _explorer_window.type_keys("{ENTER}")
        time.sleep(1)

        current_files = _list_files_in_explorer()
        new_name_without_ext = os.path.splitext(new_name)[0]
        rename_succeeded = any(new_name_without_ext in f for f in current_files)
        old_name_still_present = old_name in current_files

        if rename_succeeded and not old_name_still_present:
            print(f"  Verified: '{old_name}' successfully renamed to '{new_name}'.")
            return
        else:
            print(f"  Rename could not be verified. Retrying...")

    raise RuntimeError(f"Failed to rename '{old_name}' after {max_retries} attempts.")


ACTION_FUNCTIONS = {
    "open_app": lambda step: action_open_app(),
    "type_text": lambda step: action_type_text(step["value"]),
    "save_file": lambda step: action_save_file(step["filename"]),
    "open_folder": lambda step: action_open_folder(step["folder"]),
    "rename_file": lambda step: action_rename_file(step["old_name"], step["new_name"]),
}


# ---------------------------------------------------------------------------
# STEP C: Tie it all together
# ---------------------------------------------------------------------------

def run_command(user_request: str):
    print(f"\nUser command: \"{user_request}\"")
    print("Asking local AI to generate a plan...\n")

    plan = get_plan_from_ai(user_request)
    print("AI generated plan:")
    print(json.dumps(plan, indent=2))

    if not validate_plan(plan):
        print("\nPlan was rejected for safety reasons. Aborting.")
        return

    print("\nExecuting plan step by step...\n")
    for i, step in enumerate(plan, start=1):
        print(f"--- Step {i}/{len(plan)} ---")
        ACTION_FUNCTIONS[step["action"]](step)

    print("\nAll steps completed successfully.")


if __name__ == "__main__":
    user_input = input("What would you like TaskForge to do?\n> ")
    run_command(user_input)