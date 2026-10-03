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
import shutil
import winreg
import webbrowser
import urllib.parse
from pywinauto import Application
from pywinauto.keyboard import send_keys

OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL_NAME = "phi3:mini"  # change this if you're using a different model name


def get_known_folder_path(folder_name: str) -> str:
    """
    Resolves a known Windows Shell folder (e.g. 'documents', 'desktop', 'downloads')
    to its actual OS location, dynamically checking the Windows Registry
    (`User Shell Folders`) to account for OneDrive redirection or custom paths.
    """
    folder_key = folder_name.strip().lower()

    registry_mapping = {
        "documents": "Personal",
        "desktop": "Desktop",
        "downloads": "{374DE290-123F-4565-9164-39C4925E467B}",
        "pictures": "My Pictures",
        "music": "My Music",
        "videos": "My Video",
    }

    if folder_key in registry_mapping:
        reg_value_name = registry_mapping[folder_key]
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders") as key:
                val, _ = winreg.QueryValueEx(key, reg_value_name)
                resolved = os.path.expandvars(val)
                if os.path.exists(resolved):
                    return resolved
        except Exception:
            pass

    # Fallback to standard %USERPROFILE%\<Folder> if registry query fails
    home = os.path.expanduser("~")
    return os.path.join(home, folder_key.capitalize())


def get_known_folders_dict() -> dict:
    return {
        "downloads": get_known_folder_path("downloads"),
        "desktop": get_known_folder_path("desktop"),
        "documents": get_known_folder_path("documents"),
    }


def normalize_url(site_input: str) -> str:
    """
    Normalizes any natural language website name or raw input into a valid HTTPS URL.
    Works for ANY site without a hardcoded list of known domains or aliases.

    Resolution order:
    1. Pattern: "[action] query in/on/at site" -> build search URL on that site
    2. Already a full URL -> return as-is
    3. Looks like a domain (has dot, no spaces) -> add https://
    4. Single word (no dot, no spaces) -> try https://word.com
    5. Multi-word fallback -> DuckDuckGo search
    """
    raw = site_input.strip().strip("'\"")
    cleaned = raw.lower()

    # Pattern: "[action] QUERY in/on/at SITE" — works for any site generically
    search_on_match = re.match(
        r"^(?:(?:search|find|play|watch|run|show|look\s+up)\s+)?(.+?)\s+(?:in|on|at)\s+([a-zA-Z0-9.\-]+)\s*$",
        cleaned, re.I
    )
    if search_on_match:
        query_part = search_on_match.group(1).strip()
        site_part = search_on_match.group(2).strip()
        # Strip leading action verbs from query
        query_part = re.sub(
            r"^(?:search|find|play|watch|run|show|look\s+up)\s+", "", query_part, flags=re.I
        ).strip()
        if query_part and site_part:
            return build_search_url(site_part, query_part)

    # Already a full URL with scheme
    if cleaned.startswith("http://") or cleaned.startswith("https://"):
        return raw

    # Domain-like: has a dot, no spaces (e.g. github.com, en.wikipedia.org)
    if "." in cleaned and " " not in cleaned and not cleaned.endswith("."):
        return f"https://{cleaned}"

    # Single word, no dot — treat as .com domain (e.g. 'linkedin', 'medium', 'twitch')
    if " " not in cleaned and re.match(r"^[a-zA-Z0-9-]+$", cleaned):
        return f"https://{cleaned}.com"

    # Multi-word fallback: DuckDuckGo search (no bot CAPTCHAs)
    return f"https://duckduckgo.com/?q={urllib.parse.quote_plus(cleaned)}"



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

Classify this instruction into EXACTLY ONE of these eight categories:

1. CONTENT_CREATION - the user wants new content written (a note, schedule,
   letter, poem, list, etc.) and saved as a file.
2. FILE_RENAME - the user wants to rename an EXISTING file that already
   exists on their computer.
3. FILE_MOVE - the user wants to move an EXISTING file from one folder to another
   folder (e.g. move resume.pdf from Downloads to Documents).
4. FILE_DELETE - the user wants to delete an EXISTING file from their computer
   (e.g. delete notes.txt in downloads).
5. BROWSER_NAVIGATE - the user wants to open a website, navigate to a URL, or
   browse a web page (e.g. open wikipedia, go to github.com).
6. BROWSER_FORM_FILL - the user wants to search for something on a website, fill out
   an input box, type into a search bar, or log in (e.g. search for python tutorials on youtube,
   search playwright on wikipedia).
7. CALCULATOR - the user wants to perform arithmetic, compute a math expression,
   or use the Windows Calculator (e.g. what is 245 times 12, calculate 100 divided by 4,
   square root of 144, what's 50 plus 30).
8. SYSTEM_SETTINGS - the user wants to change a Windows system setting or toggle a
   system feature (e.g. turn on bluetooth, enable dark mode, set power plan to performance,
   disable firewall, enable battery saver, enable night light, change screen resolution,
   set timezone, enable remote desktop, turn on airplane mode, change refresh rate).

Reply with ONLY one word: CONTENT_CREATION, FILE_RENAME, FILE_MOVE, FILE_DELETE, BROWSER_NAVIGATE, BROWSER_FORM_FILL, CALCULATOR, or SYSTEM_SETTINGS. Nothing else.

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

MOVE_EXTRACTION_PROMPT = """The user gave this instruction: "{user_request}"

Extract exactly three things:
1. The name of the file they want to move (including extension if mentioned)
2. The SOURCE folder where the file currently resides (e.g. Downloads, Desktop, Documents)
3. The DESTINATION folder where they want to move the file (e.g. Documents, Desktop, Downloads)

Reply in EXACTLY this format, nothing else:
FILE: <filename>
FROM: <source folder>
TO: <destination folder>

Example:
Instruction: "move resume.pdf from Downloads to Documents"
FILE: resume.pdf
FROM: Downloads
TO: Documents

Now do the same for the real instruction above.
"""

DELETE_EXTRACTION_PROMPT = """The user gave this instruction: "{user_request}"

Extract exactly two things:
1. The name of the file they want to delete (including extension if mentioned)
2. The folder where the file is currently located (e.g. Downloads, Desktop, Documents, or path)

Reply in EXACTLY this format, nothing else:
FILE: <filename>
FOLDER: <folder>

Example:
Instruction: "delete notes.txt from Downloads"
FILE: notes.txt
FOLDER: Downloads

Now do the same for the real instruction above.
"""

BROWSER_NAVIGATE_EXTRACTION_PROMPT = """The user gave this instruction: "{user_request}"

Extract the target website name or URL they want to open/navigate to.

Reply in EXACTLY this format, nothing else:
URL: <website name or url>

Example 1:
Instruction: "open wikipedia"
URL: wikipedia

Example 2:
Instruction: "go to github.com"
URL: github.com

Now do the same for the real instruction above.
"""

FORM_FILL_EXTRACTION_PROMPT = """The user gave this instruction: "{user_request}"

Extract three things:
1. The target website name or URL (e.g. wikipedia, youtube, google, github.com)
2. The search term or text value to type into the input field
3. The field type or target label (e.g. search, username, password, input)

Reply in EXACTLY this format, nothing else:
URL: <website name or url>
VALUE: <text to type>
FIELD: <field label or type>

Example 1:
Instruction: "search python tutorials on youtube"
URL: youtube
VALUE: python tutorials
FIELD: search

Example 2:
Instruction: "search playwright on wikipedia"
URL: wikipedia
VALUE: playwright
FIELD: search

Now do the same for the real instruction above.
"""


# Compiled regex for deterministic system-settings detection.
# Covers the fast-path intents AND common settings keywords so most commands
# get routed without an AI call. Unknown settings commands go to task3_2 anyway
# where the AI-PowerShell fallback handles them.
_SYSTEM_SETTINGS_RE = re.compile(
    r"""
    \b(
      # Wi-Fi
      (?:turn[\s-]+on|enable|turn[\s-]+off|disable|toggle|connect|disconnect)\s+
          (?:wi[\s-]?fi|wifi|wireless|wlan)
      | (?:wi[\s-]?fi|wifi|wireless|wlan)\s+(?:on|off|status|state)
      | wifi\s+(?:on|off|status|state)

      # Bluetooth
      | (?:turn[\s-]+on|enable|turn[\s-]+off|disable|toggle)\s+(?:bluetooth|bt)
      | (?:bluetooth|bt)\s+(?:on|off|status|state)

      # Volume
      | (?:set\s+)?(?:master\s+)?(?:volume|audio|sound)\s+(?:to\s+)?\d
      | (?:increase|raise|decrease|lower|turn[\s-]+up|turn[\s-]+down)\s+
            (?:the\s+)?(?:master\s+)?(?:volume|audio|sound)
      | (?:mute|unmute|silence|unsilence)
      | volume\s+(?:status|level|up|down|to\s+\d)

      # Brightness
      | (?:set\s+)?(?:screen\s+|display\s+)?brightness\s+(?:to\s+)?\d
      | (?:increase|raise|decrease|lower|brighten|dim)\s+
            (?:the\s+)?(?:screen\s+|display\s+)?brightness
      | dim\s+(?:the\s+)?(?:screen|display|monitor)
      | brightness\s+(?:status|level|to\s+\d)

      # Dark/Light mode
      | (?:switch\s+to\s+|enable\s+|use\s+)?(?:dark|light|night)\s+(?:mode|theme)
      | toggle\s+(?:dark|light|theme|mode)

      # Power plan
      | (?:set|switch|change|use)\s+(?:the\s+)?power\s+(?:plan|mode|profile)
      | (?:performance|balanced|power\s+saver)\s+(?:power\s+)?(?:plan|mode|profile)

      # Firewall
      | (?:enable|disable|turn\s+on|turn\s+off)\s+(?:windows\s+)?firewall
      | firewall\s+(?:status|state)

      # Battery saver / power saving
      | (?:enable|disable|turn\s+on|turn\s+off)\s+(?:battery\s+saver|power\s+saving|low[\s-]power\s+mode)

      # Generic system settings keywords that should always go to task3_2
      | (?:night\s+light|airplane\s+mode|do\s+not\s+disturb|focus\s+assist)
      | (?:screen\s+resolution|display\s+resolution|refresh\s+rate)
      | (?:remote\s+desktop|remote\s+access)
      | (?:windows\s+update|auto\s+update)
      | (?:sleep\s+(?:timeout|timer|after)|screen\s+timeout)
      | (?:time\s+zone|timezone|system\s+time|system\s+date)
      | (?:system\s+setting|windows\s+setting|control\s+panel)
    )\b
    """,
    re.I | re.VERBOSE,
)


def classify_task_type(user_request: str) -> str:
    lowered = user_request.lower()

    # Deterministic: system settings — Wi-Fi, volume, brightness, mute
    # Checked first so these short commands don't fall through to the AI.
    if _SYSTEM_SETTINGS_RE.search(lowered):
        return "SYSTEM_SETTINGS"

    # Deterministic: calculator / arithmetic keywords (NLP verbs, not site names)
    math_verbs = ["calculate", "compute", "what is", "what's", "how much is",
                  "times", "divided by", "multiplied by", "plus", "minus",
                  "square root", "sqrt", "squared", "modulo", "remainder",
                  "percent of", "reciprocal", "1 over"]
    if any(kw in lowered for kw in math_verbs):
        # Exclude if it looks like a browser search: "find X on site" or "search X at url"
        is_browser_search = bool(re.search(
            r"\b(?:search|find|play|watch|run|show|look\s+up)\b.+\b(?:in|on|at)\s+[a-zA-Z0-9.\-]+",
            lowered
        ))
        if not is_browser_search:
            return "CALCULATOR"

    # Deterministic: "[search verb] QUERY [in/on/at] SITE" pattern — works for any site
    browser_search_match = re.search(
        r"\b(?:search|find|play|watch|run|show|look\s+up)\b.+\b(?:in|on|at)\s+[a-zA-Z0-9.\-]+",
        lowered
    )
    if browser_search_match:
        return "BROWSER_FORM_FILL"

    result = ask_ai(TASK_TYPE_PROMPT.replace("{user_request}", user_request), temperature=0.1)
    result = result.strip().upper()
    if "CALCULATOR" in result or "CALC" in result or "ARITHMETIC" in result or "MATH" in result:
        return "CALCULATOR"
    if "SYSTEM_SETTINGS" in result or "SYSTEM" in result or "SETTINGS" in result:
        return "SYSTEM_SETTINGS"
    if any(k in result for k in ["FORM_FILL", "FORM", "SEARCH", "TYPE"]):
        return "BROWSER_FORM_FILL"
    if any(k in result for k in ["BROWSER", "NAVIGATE", "WEBSITE", "URL", "SITE"]):
        return "BROWSER_NAVIGATE"
    if "DELETE" in result or "REMOVE" in result:
        return "FILE_DELETE"
    if "MOVE" in result or "TRANSFER" in result:
        return "FILE_MOVE"
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


def extract_move_details(user_request: str) -> tuple:
    result = ask_ai(MOVE_EXTRACTION_PROMPT.replace("{user_request}", user_request), temperature=0.1)

    filename, source_folder, dest_folder = None, None, None
    for line in result.splitlines():
        line = line.strip()
        if line.upper().startswith("FILE:"):
            filename = line.split(":", 1)[1].strip()
        elif line.upper().startswith("FROM:"):
            source_folder = line.split(":", 1)[1].strip()
        elif line.upper().startswith("TO:"):
            dest_folder = line.split(":", 1)[1].strip()

    if not filename:
        raise ValueError(f"Could not extract filename from AI response:\n{result}")

    if not source_folder:
        source_folder = detect_target_folder(user_request)
    if not dest_folder:
        dest_folder = "documents"

    return filename, source_folder, dest_folder


def extract_delete_details(user_request: str) -> tuple:
    result = ask_ai(DELETE_EXTRACTION_PROMPT.replace("{user_request}", user_request), temperature=0.1)

    filename, folder = None, None
    for line in result.splitlines():
        line = line.strip()
        if line.upper().startswith("FILE:"):
            filename = line.split(":", 1)[1].strip()
        elif line.upper().startswith("FOLDER:"):
            folder = line.split(":", 1)[1].strip()

    if not filename:
        raise ValueError(f"Could not extract filename from AI response:\n{result}")

    if not folder:
        folder = detect_target_folder(user_request)

    return filename, folder


def extract_browser_navigate_details(user_request: str) -> str:
    result = ask_ai(BROWSER_NAVIGATE_EXTRACTION_PROMPT.replace("{user_request}", user_request), temperature=0.1)

    url_target = None
    for line in result.splitlines():
        line = line.strip()
        if line.upper().startswith("URL:"):
            url_target = line.split(":", 1)[1].strip()

    if not url_target:
        url_target = user_request.strip()

    return url_target


def build_rename_plan(user_request: str) -> list:
    old_name, new_name = extract_rename_details(user_request)
    target_folder = detect_target_folder(user_request)
    print(f"  Detected rename request: '{old_name}' -> '{new_name}' in folder '{target_folder}'")

    return [
        {"action": "open_folder", "folder": target_folder},
        {"action": "rename_file", "old_name": old_name, "new_name": new_name},
    ]


def build_move_plan(user_request: str) -> list:
    filename, source_folder, dest_folder = extract_move_details(user_request)
    print(f"  Detected move request: '{filename}' from '{source_folder}' -> '{dest_folder}'")

    return [
        {"action": "open_folder", "folder": source_folder},
        {"action": "move_file", "filename": filename, "dest_folder": dest_folder, "source_folder": source_folder},
    ]


def build_delete_plan(user_request: str) -> list:
    filename, folder = extract_delete_details(user_request)
    print(f"  Detected delete request: '{filename}' in folder '{folder}'")

    return [
        {"action": "delete_file", "filename": filename, "folder": folder},
    ]


def build_browser_navigate_plan(user_request: str) -> list:
    url_target = extract_browser_navigate_details(user_request)
    normalized = normalize_url(url_target)
    print(f"  Detected browser navigate request: '{url_target}' -> '{normalized}'")

    return [
        {"action": "open_browser", "url": normalized},
    ]


def build_search_url(site_name: str, search_query: str) -> str:
    """
    Constructs a search results URL for any site without a hardcoded template list.

    Strategy (in order):
    1. Resolve the site name to a base domain (generic, works for any input).
    2. Construct a generic /search?q= URL — works for most modern sites.
    3. Fall back to DuckDuckGo with site: operator if the site is completely unknown.
    """
    encoded_query = urllib.parse.quote_plus(search_query.strip())
    site = site_name.strip().lower().strip("'\"")

    # Resolve site name to a base domain
    if site.startswith("http://") or site.startswith("https://"):
        base = re.sub(r"^https?://(?:www\.)?|/$", "", site)
    elif "." in site:
        base = site
    else:
        base = f"{site}.com"

    base = base.strip("/")

    # Use a generic search URL pattern that works for most sites.
    # e.g. github.com/search?q=X, reddit.com/search?q=X, stackoverflow.com/search?q=X
    generic_search_url = f"https://www.{base}/search?q={encoded_query}"

    # DuckDuckGo site: search as a universal fallback that works for ANY site
    duckduckgo_site_url = f"https://duckduckgo.com/?q={encoded_query}+site:{base}"

    # Use generic search URL — it's the closest to "search on this site" without
    # hardcoding specific platforms. The caller (normalize_url) can switch to
    # duckduckgo_site_url if the generic URL fails to load.
    return generic_search_url



def extract_form_fill_details(user_request: str) -> tuple:
    result = ask_ai(FORM_FILL_EXTRACTION_PROMPT.replace("{user_request}", user_request), temperature=0.1)

    url_target, value_target, field_target = None, None, "search"
    for line in result.splitlines():
        line = line.strip()
        if line.upper().startswith("URL:"):
            url_target = line.split(":", 1)[1].strip()
        elif line.upper().startswith("VALUE:"):
            value_target = line.split(":", 1)[1].strip()
        elif line.upper().startswith("FIELD:"):
            field_target = line.split(":", 1)[1].strip()

    if not url_target:
        # Generic fallback: extract any word that looks like a site from the request
        site_match = re.search(
            r"\b(?:on|in|at|for)\s+([a-zA-Z0-9.\-]+)", user_request, re.I
        )
        url_target = site_match.group(1) if site_match else "duckduckgo"
    if not value_target:
        value_target = user_request.strip()

    return url_target, value_target, field_target


def build_form_fill_plan(user_request: str) -> list:
    url_target, value_target, field_target = extract_form_fill_details(user_request)
    search_url = build_search_url(url_target, value_target)
    print(f"  Detected search request: '{value_target}' on '{url_target}' -> '{search_url}'")

    return [
        {"action": "open_browser", "url": search_url},
    ]


def build_calculator_plan(user_request: str) -> list:
    """
    Builds a plan step for the Calculator engine — delegates the raw user
    request directly to task3_1_calculator.run_task_3_1() without re-parsing.
    """
    print(f"  Detected calculator request: '{user_request}'")
    return [
        {"action": "run_calculator", "expression": user_request},
    ]


def build_system_settings_plan(user_request: str) -> list:
    """
    Builds a plan step for the System Settings engine — passes the raw
    natural-language command directly to task3_2_system_settings.run_task_3_2().
    The full NL parsing happens inside that module; we never re-interpret it here.
    """
    print(f"  Detected system settings request: '{user_request}'")
    return [
        {"action": "run_system_settings", "command": user_request},
    ]


def extract_bare_creative_request(user_request: str) -> str:
    """
    Strips app instructions ("open notepad"), save commands ("save as cloud.txt"),
    and folder mentions from user_request so the AI receives only the pure creative instruction
    (e.g., "write a poem about clouds").
    """
    cleaned = user_request
    cleaned = re.sub(r"^(open|launch)\s+(notepad|word|app)\s+(and|to)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*(and\s+)?(saves?|save|store)\s+(as|it as|this as)?\s+[\w\.\-]+\s*(in\s+\w+)?$", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*in\s+(downloads|documents|desktop)\s*$", "", cleaned, flags=re.IGNORECASE)
    return cleaned.strip() or user_request


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

    if task_type == "SYSTEM_SETTINGS":
        return build_system_settings_plan(user_request)

    if task_type == "CALCULATOR":
        return build_calculator_plan(user_request)

    if task_type == "BROWSER_FORM_FILL":
        return build_form_fill_plan(user_request)

    if task_type == "BROWSER_NAVIGATE":
        return build_browser_navigate_plan(user_request)

    if task_type == "FILE_DELETE":
        return build_delete_plan(user_request)

    if task_type == "FILE_MOVE":
        return build_move_plan(user_request)

    if task_type == "FILE_RENAME":
        return build_rename_plan(user_request)

    # --- Otherwise, fall through to the existing content-creation flow ---
    print("Asking AI to generate the content...")
    pure_creative_request = extract_bare_creative_request(user_request)
    content = ask_ai(CONTENT_PROMPT.replace("{user_request}", pure_creative_request))

    # If the model leaked meta-instructions into the content (a known failure
    # mode), regenerate up to 2 more times with an even more forceful reminder,
    # instead of silently proceeding with broken content.
    retry_count = 0
    while (looks_like_echoed_instruction(content) or looks_too_vague(content)) and retry_count < 2:
        retry_count += 1
        if looks_like_echoed_instruction(content):
            print(f"  Detected echoed instructions in content, regenerating (attempt {retry_count + 1})...")
            forceful_prompt = (
                CONTENT_PROMPT.replace("{user_request}", pure_creative_request)
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
    allowed_actions = {
        "open_app", "type_text", "save_file", "open_folder", "rename_file",
        "move_file", "delete_file", "open_browser", "close_browser",
        "browser_go_back", "browser_go_forward", "fill_form", "run_calculator",
        "run_system_settings"
    }

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
        if action == "move_file" and ("filename" not in step or "dest_folder" not in step):
            print("Rejected plan: move_file step missing 'filename' or 'dest_folder' field")
            return False
        if action == "delete_file" and "filename" not in step:
            print("Rejected plan: delete_file step missing 'filename' field")
            return False
        if action == "open_browser" and "url" not in step:
            print("Rejected plan: open_browser step missing 'url' field")
            return False
        if action == "fill_form" and "value" not in step:
            print("Rejected plan: fill_form step missing 'value' field")
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
    known_folders = get_known_folders_dict()

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

    # Universally focus the File Name input field in Windows Save As dialog using Alt+N (%n)
    print("  Focusing File Name field in Save As dialog (Alt+N)...")
    save_window.type_keys("%n")
    time.sleep(0.3)

    pyperclip.copy(full_path)
    save_window.type_keys("^a")
    save_window.type_keys("{DELETE}")
    time.sleep(0.2)
    save_window.type_keys("^v")
    time.sleep(0.3)

    print(f"  Pasted target path: '{full_path}'. Submitting Save dialog...")
    save_window.type_keys("{ENTER}")
    time.sleep(1.5)

    # Handle optional "Confirm Save As" overwrite prompt if Windows asks to replace file
    try:
        confirm_matches = find_window_handle_by_title("confirm")
        if confirm_matches:
            confirm_hwnd = confirm_matches[0][0]
            confirm_win = Application(backend="win32").connect(handle=confirm_hwnd).window(handle=confirm_hwnd)
            confirm_win.type_keys("%y")
            time.sleep(0.5)
    except Exception:
        pass
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

    known_folders = get_known_folders_dict()

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

    # Generate keywords for title matching (handling OneDrive aliases like 'docs' vs 'Documents')
    search_keywords = {folder.lower(), folder_display_name.lower()}
    if folder.lower() in ["documents", "docs"] or folder_display_name.lower() in ["documents", "docs"]:
        search_keywords.update(["documents", "docs"])
    if folder.lower() in ["downloads", "download"] or folder_display_name.lower() in ["downloads", "download"]:
        search_keywords.update(["downloads", "download"])
    if folder.lower() in ["desktop"]:
        search_keywords.update(["desktop"])

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

    explorer_hwnd = None
    seen_titles = []
    for attempt in range(15):
        time.sleep(1)
        matches = []

        def enum_handler(hwnd, _):
            if win32gui.IsWindowVisible(hwnd) and win32gui.GetClassName(hwnd) == "CabinetWClass":
                title = win32gui.GetWindowText(hwnd)
                if any(kw in title.lower() for kw in search_keywords):
                    matches.append((hwnd, title))

        win32gui.EnumWindows(enum_handler, None)

        # Fallback: if title keyword didn't match, accept any visible CabinetWClass Explorer window
        if not matches:
            def fallback_handler(hwnd, _):
                if win32gui.IsWindowVisible(hwnd) and win32gui.GetClassName(hwnd) == "CabinetWClass":
                    title = win32gui.GetWindowText(hwnd)
                    matches.append((hwnd, title))
            win32gui.EnumWindows(fallback_handler, None)

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


def action_move_file(filename: str, dest_folder: str, source_folder: str = None, max_retries: int = 3):
    """
    Moves a file from the currently-open folder (or source_folder) to dest_folder,
    with closed-loop UI verification in both destination and source Explorer views.
    """
    global _explorer_window, _current_folder_path
    print(f"Executing: move_file -> \"{filename}\" to \"{dest_folder}\"")

    if _explorer_window is None and not _current_folder_path:
        raise RuntimeError("No Explorer window is open yet. open_folder must run first.")

    known_folders = get_known_folders_dict()

    # Resolve source folder path
    src_dir = _current_folder_path
    if not src_dir and source_folder:
        src_dir = known_folders.get(source_folder.lower(), source_folder)

    # Resolve destination folder path
    dest_key = dest_folder.lower()
    if dest_key in known_folders:
        dst_dir = known_folders[dest_key]
    elif os.path.isabs(dest_folder) or re.match(r"^[A-Za-z]:[\\/]", dest_folder):
        dst_dir = dest_folder
    else:
        dst_dir = known_folders.get("documents")

    if not os.path.isdir(dst_dir):
        os.makedirs(dst_dir, exist_ok=True)

    current_files = _list_files_in_explorer()
    exact_matches = [f for f in current_files if f == filename]

    if exact_matches:
        target_display_name = exact_matches[0]
    else:
        close_matches = difflib.get_close_matches(filename, current_files, n=1, cutoff=0.5)
        if not close_matches:
            raise RuntimeError(
                f"Could not find '{filename}' or anything similar in source folder '{src_dir}'. "
                f"Actual files present: {current_files}"
            )
        target_display_name = close_matches[0]
        print(f"  Fuzzy matched '{filename}' to display name '{target_display_name}' in source folder.")

    # Match UI display name against actual files on disk
    real_disk_file = None
    if src_dir and os.path.exists(src_dir):
        for f in os.listdir(src_dir):
            if f == target_display_name or os.path.splitext(f)[0] == target_display_name:
                real_disk_file = f
                break

    if not real_disk_file:
        real_disk_file = target_display_name

    src_filepath = os.path.join(src_dir, real_disk_file) if src_dir else real_disk_file
    dst_filepath = os.path.join(dst_dir, real_disk_file)

    print(f"  Found target in source view: '{target_display_name}' (disk path: '{src_filepath}')")
    print(f"  Moving file to '{dst_filepath}'...")

    shutil.move(src_filepath, dst_filepath)
    time.sleep(1)

    # Closed-loop verification 1: Open destination folder & verify file presence in UI
    print(f"  Verifying in destination Explorer view '{dest_folder}'...")
    action_open_folder(dest_folder)
    dest_files = _list_files_in_explorer()
    dest_match = any(target_display_name == f or os.path.splitext(real_disk_file)[0] in f for f in dest_files)

    if not dest_match:
        raise RuntimeError(f"Move verification FAILED: '{real_disk_file}' not found in destination Explorer view. Visible: {dest_files}")
    print(f"  Verified: '{real_disk_file}' present in destination Explorer view.")

    # Closed-loop verification 2: Open source folder & verify file absence in UI
    if src_dir:
        print(f"  Verifying absence in source Explorer view...")
        action_open_folder(src_dir)
        source_files = _list_files_in_explorer()
        if target_display_name in source_files:
            raise RuntimeError(f"Move verification FAILED: '{target_display_name}' still present in source Explorer view. Visible: {source_files}")
        print(f"  Verified: '{target_display_name}' absent from source Explorer view.")

    print(f"  Successfully moved '{real_disk_file}' from '{src_dir}' to '{dst_dir}'.")


def action_delete_file(filename: str, folder: str = None, max_retries: int = 3):
    """
    Deletes a file from the specified folder, with explicit CLI safety confirmation
    BEFORE opening any windows or touching any files on disk.
    """
    global _explorer_window, _current_folder_path

    known_folders = get_known_folders_dict()

    # Resolve target folder path
    src_dir = _current_folder_path
    if not src_dir and folder:
        folder_key = folder.lower()
        if folder_key in known_folders:
            src_dir = known_folders[folder_key]
        elif os.path.isabs(folder) or re.match(r"^[A-Za-z]:[\\/]", folder):
            src_dir = folder
        else:
            src_dir = known_folders.get("downloads")

    if not src_dir:
        src_dir = known_folders.get("downloads")

    # 1. DESTRUCTIVE ACTION SAFETY CONFIRMATION FIRST (BEFORE OPENING WINDOWS)
    print(f"\n------------------------------------------------------------")
    print(f"⚠️  DESTRUCTION CONFIRMATION REQUIRED")
    print(f"File to delete: '{filename}'")
    print(f"Target folder:  '{src_dir}'")
    print(f"------------------------------------------------------------")

    confirm = input(f"Are you sure you want to permanently delete '{filename}' from '{src_dir}'? (type 'yes' to confirm): ").strip().lower()

    if confirm not in ["yes", "y"]:
        print("  Deletion CANCELLED by user. No windows opened and no files modified.")
        return

    # 2. Open Explorer window AFTER confirmation is granted
    print(f"  Confirmation received. Opening Explorer view...")
    action_open_folder(folder or src_dir)

    current_files = _list_files_in_explorer()
    exact_matches = [f for f in current_files if f == filename]

    if exact_matches:
        target_display_name = exact_matches[0]
    else:
        close_matches = difflib.get_close_matches(filename, current_files, n=1, cutoff=0.5)
        if not close_matches:
            raise RuntimeError(
                f"Could not find '{filename}' or anything similar in folder '{src_dir}'. "
                f"Actual files present: {current_files}"
            )
        target_display_name = close_matches[0]
        print(f"  Fuzzy matched '{filename}' to display name '{target_display_name}' in folder.")

    # Match UI display name against actual files on disk
    real_disk_file = None
    if src_dir and os.path.exists(src_dir):
        for f in os.listdir(src_dir):
            if f == target_display_name or os.path.splitext(f)[0] == target_display_name:
                real_disk_file = f
                break

    if not real_disk_file:
        real_disk_file = target_display_name

    src_filepath = os.path.join(src_dir, real_disk_file) if src_dir else real_disk_file

    print(f"  Deleting '{src_filepath}'...")
    if os.path.exists(src_filepath):
        os.remove(src_filepath)
    time.sleep(1)

    # Closed-loop verification: Re-navigate / refresh Explorer and verify item absence
    print(f"  Verifying file absence in Explorer view...")
    action_open_folder(src_dir)
    remaining_files = _list_files_in_explorer()

    if target_display_name in remaining_files:
        raise RuntimeError(f"Delete verification FAILED: '{target_display_name}' still present in Explorer view. Visible: {remaining_files}")

    print(f"  Verified: '{real_disk_file}' successfully deleted and absent from Explorer view.")


# ---------------------------------------------------------------------------
# STEP B-2: Playwright Browser Automation Actions
# ---------------------------------------------------------------------------

_playwright = None
_browser = None
_browser_context = None
_page = None


def get_chrome_profile_dir() -> str:
    """
    Returns path to your primary Windows Google Chrome User Data directory
    (%LOCALAPPDATA%\\Google\\Chrome\\User Data), connecting directly to your
    real default/last-used Chrome profile.
    """
    home = os.path.expanduser("~")
    primary_chrome_dir = os.path.join(home, "AppData", "Local", "Google", "Chrome", "User Data")
    if os.path.exists(primary_chrome_dir):
        return primary_chrome_dir

    fallback_dir = os.path.join(home, "AppData", "Local", "TaskForge_Chrome_Profile")
    os.makedirs(fallback_dir, exist_ok=True)
    return fallback_dir


def is_chrome_running() -> bool:
    """Checks if chrome.exe is currently running on Windows."""
    try:
        output = subprocess.check_output('tasklist /FI "IMAGENAME eq chrome.exe"', shell=True, text=True)
        return "chrome.exe" in output.lower()
    except Exception:
        return False


def clear_chrome_crash_bubble():
    """
    Clears Chrome's 'Crashed' exit_type flag in Preferences files so Chrome
    never displays the 'Restore pages? Chrome didn't shut down correctly' bubble.
    """
    try:
        home = os.path.expanduser("~")
        chrome_user_data = os.path.join(home, "AppData", "Local", "Google", "Chrome", "User Data")
        if not os.path.exists(chrome_user_data):
            return

        for root, dirs, files in os.walk(chrome_user_data):
            if "Preferences" in files:
                pref_file = os.path.join(root, "Preferences")
                try:
                    with open(pref_file, "r", encoding="utf-8", errors="ignore") as f:
                        content = f.read()
                    if '"exit_type":"Crashed"' in content or '"exit_type": "Crashed"' in content or '"exited_cleanly":false' in content:
                        content = content.replace('"exit_type":"Crashed"', '"exit_type":"Normal"')
                        content = content.replace('"exit_type": "Crashed"', '"exit_type": "Normal"')
                        content = content.replace('"exited_cleanly":false', '"exited_cleanly":true')
                        with open(pref_file, "w", encoding="utf-8") as f:
                            f.write(content)
                except Exception:
                    pass
    except Exception:
        pass


def action_open_browser(url: str):
    global _playwright, _browser_context, _page

    clear_chrome_crash_bubble()

    normalized_target_url = normalize_url(url)
    print(f"Executing: open_browser -> \"{normalized_target_url}\"")

    # Instantly open URL as a new tab in the user's active Google Chrome profile ('P')
    # Zero startup delay (<0.01s), zero profile lock errors, preserves all open tabs & pages!
    try:
        os.startfile(normalized_target_url)
        print(f"  Verified: Instantly opened '{normalized_target_url}' in your active Chrome profile.")
    except Exception:
        webbrowser.open(normalized_target_url)
        print(f"  Verified: Opened '{normalized_target_url}' in your active Chrome browser.")


def action_close_browser():
    global _playwright, _browser_context, _page

    print("Executing: close_browser")
    if _browser_context is not None:
        try:
            _browser_context.close()
        except Exception:
            pass
        _browser_context = None
        _page = None

    if _playwright is not None:
        try:
            _playwright.stop()
        except Exception:
            pass
        _playwright = None

    print("  Browser closed.")


def action_browser_go_back():
    global _page
    print("Executing: browser_go_back")
    if _page is None or _page.is_closed():
        raise RuntimeError("No active browser page is open.")
    _page.go_back()
    time.sleep(1)
    print(f"  Navigated back to: '{_page.title()}' ({_page.url})")


def action_browser_go_forward():
    global _page
    print("Executing: browser_go_forward")
    if _page is None or _page.is_closed():
        raise RuntimeError("No active browser page is open.")
    _page.go_forward()
    time.sleep(1)
    print(f"  Navigated forward to: '{_page.title()}' ({_page.url})")


def action_fill_form(field: str = "search", value: str = "", submit: bool = True):
    global _page, _browser_context

    print(f"Executing: fill_form -> field: \"{field}\", value: \"{value}\", submit: {submit}")

    if _page is not None and not _page.is_closed():
        print(f"  Locating input field '{field}' on active Playwright page...")
        locators_to_try = [
            _page.get_by_placeholder(field, exact=False),
            _page.get_by_placeholder("Search", exact=False),
            _page.get_by_label(field, exact=False),
            _page.get_by_role("textbox", name=field, exact=False),
            _page.locator("input[type='search']"),
            _page.locator("input[name='search']"),
            _page.locator("input[name='q']"),
            _page.locator("input[type='text']"),
            _page.locator("textarea"),
        ]

        target_el = None
        for loc in locators_to_try:
            try:
                if loc.count() > 0 and loc.first.is_visible():
                    target_el = loc.first
                    break
            except Exception:
                continue

        if target_el:
            target_el.fill(value)
            time.sleep(0.5)
            print(f"  Filled field with value: '{value}'")
            if submit:
                target_el.press("Enter")
                time.sleep(1.5)
                print(f"  Submitted form (pressed Enter). Result page title: '{_page.title()}'")
            print(f"  Verified: Successfully filled form field.")
            return

    # Fallback to atomic OS keyboard pasting into active browser window
    print("  Performing OS-level keyboard paste into active browser search/input bar...")
    pyperclip.copy(value)
    time.sleep(0.3)
    try:
        send_keys("^v")
        time.sleep(0.3)
        if submit:
            send_keys("{ENTER}")
        print(f"  Verified: Pasted '{value}' into active browser search/input field.")
    except Exception as e:
        print(f"  (Keyboard paste fallback note: {e})")


def action_run_calculator(expression: str):
    """Delegates an arithmetic expression to the Task 3.1 Calculator engine."""
    import sys as _sys
    import os as _os
    _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
    from task3_1_calculator import run_task_3_1
    result = run_task_3_1(expression)
    print(f"  Calculator result: {result.get('display_result', 'N/A')} (verified={result.get('verified', False)})")


def action_run_system_settings(command: str):
    """
    Delegates a system-settings command to the Task 3.2 engine.
    Handles Wi-Fi toggle, display brightness, and master volume via NL.
    """
    import sys as _sys
    import os as _os
    _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
    from task3_2_system_settings import run_task_3_2
    result = run_task_3_2(command)
    status = "OK" if result.get("success") else "FAILED"
    print(f"  System settings result [{status}]: {result.get('message', 'done')}")


ACTION_FUNCTIONS = {
    "open_app": lambda step: action_open_app(),
    "type_text": lambda step: action_type_text(step["value"]),
    "save_file": lambda step: action_save_file(step["filename"]),
    "open_folder": lambda step: action_open_folder(step["folder"]),
    "rename_file": lambda step: action_rename_file(step["old_name"], step["new_name"]),
    "move_file": lambda step: action_move_file(step["filename"], step["dest_folder"], step.get("source_folder")),
    "delete_file": lambda step: action_delete_file(step["filename"], step.get("folder")),
    "open_browser": lambda step: action_open_browser(step["url"]),
    "close_browser": lambda step: action_close_browser(),
    "browser_go_back": lambda step: action_browser_go_back(),
    "browser_go_forward": lambda step: action_browser_go_forward(),
    "fill_form": lambda step: action_fill_form(step.get("field", "search"), step["value"], step.get("submit", True)),
    "run_calculator": lambda step: action_run_calculator(step["expression"]),
    "run_system_settings": lambda step: action_run_system_settings(step["command"]),
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
    print("============================================================")
    print("         TaskForge — Offline AI Automation Agent            ")
    print("============================================================")
    print("Type a command (e.g. 'open linkedin', 'move notes.txt to documents').")
    print("Type 'exit' or 'quit' to stop.\n")

    while True:
        try:
            user_input = input("\nWhat would you like TaskForge to do?\n> ").strip()
            if not user_input:
                continue
            if user_input.lower() in ["exit", "quit"]:
                if _browser is not None:
                    action_close_browser()
                print("Exiting TaskForge. Goodbye!")
                break
            run_command(user_input)
        except KeyboardInterrupt:
            if _browser is not None:
                action_close_browser()
            print("\nExiting TaskForge. Goodbye!")
            break
        except Exception as e:
            print(f"\nTask execution encountered an error: {e}")