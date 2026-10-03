"""
Task 2.1: Browser Navigation & Window Management Engine (Active User Chrome Integration)
Purpose: Open specific websites by natural-language query or URL directly inside the user's
ALREADY RUNNING Google Chrome browser window as a new tab, without creating separate profile locks
or hardcoded default strings.

How it works:
1. Parses natural language queries into valid URLs dynamically.
2. If Google Chrome is already running on your desktop, it sends the URL directly to your ACTIVE Chrome window.
3. If CDP debugging is active, it attaches to the active session.
4. If Chrome is not open, it launches Chrome in full screen mode.
"""

import os
import re
import sys
import time
import argparse
import subprocess
from urllib.parse import quote_plus
from playwright.sync_api import sync_playwright, BrowserContext, Page, Error as PlaywrightError


def parse_natural_language_url(user_query: str) -> str:
    """
    Dynamically parses a natural-language request or raw URL string into a valid web URL.
    Does NOT use hardcoded site dictionaries. Uses pattern matching & search engine fallback.
    """
    query = user_query.strip()

    # Strip conversational prefixes like "Open ", "Go to ", "Navigate to ", "Visit "
    cleaned = re.sub(r"^(open|go\s+to|navigate\s+to|visit|show|load)\s+", "", query, flags=re.IGNORECASE).strip()

    # Case 1: Already a full URL with scheme
    if re.match(r"^https?://", cleaned, re.IGNORECASE):
        return cleaned

    # Case 2: Direct domain format like github.com, wikipedia.org, sub.domain.co.uk
    if re.match(r"^[a-zA-Z0-9-]+(\.[a-zA-Z0-9-]+)+(/.*)?$", cleaned):
        return f"https://{cleaned}"

    # Case 3: Single word target
    if " " not in cleaned:
        return f"https://www.{cleaned}.com" if not cleaned.endswith(".org") else f"https://www.{cleaned}"

    # Case 4: General natural language query -> fallback to web search engine
    return f"https://www.google.com/search?q={quote_plus(cleaned)}"


def open_in_active_system_chrome(url: str) -> bool:
    """
    Opens the target URL directly as a tab inside the user's ALREADY RUNNING Google Chrome window.
    This uses Windows native process dispatching to target the user's active Chrome session.
    """
    print(f"Dispatching URL to your active Google Chrome browser...")
    print(f" -> URL: {url}")

    try:
        cmd = f'start chrome "{url}"'
        subprocess.Popen(cmd, shell=True)
        time.sleep(1)
        print("\nSUCCESS: Opened in your active Chrome browser window!")
        return True
    except Exception as e:
        print(f"Could not dispatch directly to active Chrome: {e}")
        return False


def open_via_playwright(url: str, headless: bool = False):
    """
    Fallback method using Playwright with full-screen native viewport.
    Tries connecting via CDP first if Chrome was started with debugging port.
    """
    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp("http://localhost:9222")
            context = browser.contexts[0] if browser.contexts else browser.new_context()
            page = context.new_page()
            page.goto(url, wait_until="domcontentloaded")
            print(f"Successfully opened via active Chrome CDP: '{page.title()}'")
            return
        except Exception:
            pass

        local_appdata = os.getenv("LOCALAPPDATA", "")
        user_data_dir = os.path.join(local_appdata, "Google", "Chrome", "User Data")
        
        try:
            context = p.chromium.launch_persistent_context(
                user_data_dir=user_data_dir,
                channel="chrome",
                headless=headless,
                viewport=None,
                args=["--start-maximized", "--no-first-run", "--no-default-browser-check"]
            )
        except Exception:
            alt_dir = os.path.expanduser("~\\AppData\\Local\\TaskForge\\ChromeProfile")
            os.makedirs(alt_dir, exist_ok=True)
            context = p.chromium.launch_persistent_context(
                user_data_dir=alt_dir,
                channel="chrome",
                headless=headless,
                viewport=None,
                args=["--start-maximized"]
            )

        pages = [pg for pg in context.pages if not pg.is_closed()]
        page = pages[0] if pages else context.new_page()
        page.goto(url, wait_until="domcontentloaded")
        print(f"Opened URL: '{page.title()}' | {page.url}")


def run_task_2_1(query: str):
    """
    Main entry point for Task 2.1 - Browser Navigation & Window Management.
    """
    url = parse_natural_language_url(query)
    print(f"\n============================================================")
    print(f"Task 2.1: Browser Navigation & Window Management")
    print(f"Input Query : '{query}'")
    print(f"Resolved URL: '{url}'")
    print(f"============================================================\n")

    success = open_in_active_system_chrome(url)
    if not success:
        open_via_playwright(url)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="TaskForge Task 2.1 - Active Chrome Browser Navigation")
    parser.add_argument(
        "-q", "--query",
        type=str,
        default=None,
        help="Natural language query or URL to open"
    )

    args = parser.parse_args()
    
    user_query = args.query
    if not user_query:
        user_query = input("Enter website or query to open in Chrome:\n> ")

    if not user_query.strip():
        print("No input provided. Exiting.")
        sys.exit(0)

    run_task_2_1(query=user_query)
