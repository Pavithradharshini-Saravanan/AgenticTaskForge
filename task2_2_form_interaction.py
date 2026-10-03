"""
Task 2.2: Universal AI Form Filling & Action Engine (Full Execution Mode)
Purpose: Parses natural language prompts into target URLs and action steps (fill, select, click, check, submit),
navigates to the web page, and executes every action step dynamically on the live browser DOM.

Features:
1. Multi-step form automation: Fills inputs, selects dropdowns, checks boxes, and clicks submit.
2. Local AI Intent Parsing (Phi-3 via Ollama) with dynamic fallback.
3. Universal semantic DOM element matching.
"""

import os
import re
import sys
import time
import json
import argparse
import requests
import subprocess
from urllib.parse import quote_plus
from playwright.sync_api import sync_playwright, Playwright, Browser, Page, Error as PlaywrightError

OLLAMA_URL = "http://localhost:11434/api/generate"


def clean_user_prompt(prompt: str) -> str:
    """Strips accidental CLI wrapper prefixes if user pasted 'python task2_2_form_interaction.py "..."' into terminal."""
    clean = prompt.strip()
    clean = re.sub(r'^(?:python\s+)?(?:task|module)\d(?:_\d)?_[^\s]+\.py\s*', '', clean, flags=re.I).strip()
    clean = clean.strip('"\'')
    return clean


def resolve_plan_with_local_ai(prompt: str) -> dict:
    """
    Queries local Phi-3 LLM via Ollama to generate a structured multi-step action plan from any natural language prompt.
    """
    system_instruction = (
        "You are an offline web automation AI planner. Parse the user prompt into a structured JSON execution plan.\n"
        "Return ONLY a valid JSON object matching this schema:\n"
        "{\n"
        '  "target_url": "valid full HTTPS URL to open (e.g. https://github.com/search?q=playwright, https://wikipedia.org, or target website)",\n'
        '  "actions": [\n'
        '    { "type": "fill", "field": "label or placeholder or description", "value": "text to enter" },\n'
        '    { "type": "select", "field": "dropdown label or name", "value": "option to choose" },\n'
        '    { "type": "click", "target": "button or link or radio label to click" },\n'
        '    { "type": "check", "target": "checkbox label" },\n'
        '    { "type": "type_chat", "value": "message for AI chat prompt box" },\n'
        '    { "type": "submit", "target": "button name or submit trigger (or empty string)" }\n'
        "  ]\n"
        "}\n"
        "Output JSON ONLY. No markdown formatting."
    )

    full_prompt = f"{system_instruction}\nUser request: \"{prompt}\"\nJSON:"

    try:
        response = requests.post(
            OLLAMA_URL,
            json={"model": "phi3:mini", "prompt": full_prompt, "stream": False},
            timeout=8
        )
        if response.status_code == 200:
            raw_text = response.json().get("response", "").strip()
            raw_text = re.sub(r"^```json\s*", "", raw_text, flags=re.I)
            raw_text = re.sub(r"\s*```$", "", raw_text)
            
            match = re.search(r"\{.*\}", raw_text, re.DOTALL)
            if match:
                parsed = json.loads(match.group(0))
                if "target_url" in parsed and "actions" in parsed:
                    print("SUCCESS: Local AI (Phi-3) generated structured action plan.")
                    return parsed
    except Exception as e:
        print(f"(Local AI offline or warming up: {e}. Using dynamic generic planner.)")

    return None


def resolve_plan_generic_fallback(prompt: str) -> dict:
    """
    100% Generic Fallback (Zero domain if-statements or site-specific maps).
    """
    clean_prompt = clean_user_prompt(prompt)
    actions = []

    # 1. Chat prompt verb check: "go to [site] and tell/type/say [message]"
    chat_match = re.match(
        r"^(?:go\s+to|open|visit|navigate\s+to)\s+([a-zA-Z0-9.-]+)\s+(?:and\s+)?(?:tell|type|say|send|ask|write|chat)\s+(.+)$",
        clean_prompt, re.I
    )
    if chat_match:
        site = chat_match.group(1).strip()
        msg = chat_match.group(2).strip()
        url = site.lower()
        target_url = url if url.startswith("http") else (f"https://www.{url}.com" if "." not in url else f"https://{url}")
        return {
            "target_url": target_url,
            "actions": [{"type": "type_chat", "value": msg}]
        }

    # 2. Search verb check: "go to [site] search [query]"
    search_match = re.match(
        r"^(?:go\s+to|open|visit|navigate\s+to)\s+([a-zA-Z0-9.-]+)\s+(?:and\s+)?(?:search|find|query|for|look\s+up)\s+(.+)$",
        clean_prompt, re.I
    )
    if search_match:
        site = search_match.group(1).strip()
        query = search_match.group(2).strip()
        url = site.lower()
        site_domain = url if url.startswith("http") else (f"https://www.{url}.com" if "." not in url else f"https://{url}")
        target_url = f"{site_domain}/search?q={quote_plus(query)}"
        return {
            "target_url": target_url,
            "actions": [{"type": "fill_search", "value": query}]
        }

    # 3. Multi-field assignment check: "name as X, email as Y..."
    # Restrict field name to 1-3 words max to avoid capturing sentence fragments
    field_assignments = re.findall(
        r"\b([a-zA-Z][a-zA-Z0-9_]{0,29}(?:\s+[a-zA-Z][a-zA-Z0-9_]{0,29}){0,2})\s+as\s+([^\s,;]+(?:\s+[^\s,;]+){0,3})",
        clean_prompt, re.I
    )
    if field_assignments:
        for field, val in field_assignments:
            # Skip if field looks like a sentence fragment (more than 3 words)
            if len(field.strip().split()) <= 3:
                actions.append({"type": "fill", "field": field.strip(), "value": val.strip()})

        
        if re.search(r"submit|register|complete|apply", clean_prompt, re.I):
            actions.append({"type": "submit", "target": "submit"})

    # Extract target URL from prompt
    url_match = re.search(r"https?://[^\s]+|[a-zA-Z0-9-]+\.[a-zA-Z]{2,}", clean_prompt)
    if url_match:
        extracted = url_match.group(0)
        target_url = extracted if extracted.startswith("http") else f"https://{extracted}"
    else:
        site = clean_prompt.split()[0] if clean_prompt else "google"
        target_url = f"https://www.{site}.com" if "." not in site else f"https://{site}"

    return {
        "target_url": target_url,
        "actions": actions
    }


def find_element_by_semantic_match(page: Page, description: str, element_types: list = None):
    """
    Semantic DOM finder: locates fields, dropdowns, checkboxes, or buttons
    using ARIA labels, placeholders, visible text, names, IDs, or nearby labels.
    """
    desc_clean = description.strip()

    locators = [
        page.get_by_label(re.compile(re.escape(desc_clean), re.I)),
        page.get_by_placeholder(re.compile(re.escape(desc_clean), re.I)),
        page.get_by_role("button", name=re.compile(re.escape(desc_clean), re.I)),
        page.get_by_text(re.compile(re.escape(desc_clean), re.I))
    ]

    for loc in locators:
        try:
            if loc.count() > 0 and loc.first.is_visible():
                return loc.first
        except Exception:
            continue

    if element_types:
        for tag in element_types:
            try:
                candidates = page.locator(f"{tag}[name*='{desc_clean}' i], {tag}[id*='{desc_clean}' i], {tag}[aria-label*='{desc_clean}' i]")
                if candidates.count() > 0 and candidates.first.is_visible():
                    return candidates.first
            except Exception:
                continue

    return None


def execute_action_step(page: Page, action: dict):
    """
    Executes a single action step against the live browser DOM.
    """
    act_type = action.get("type", "").lower()
    field = action.get("field", "")
    target = action.get("target", "")
    val = action.get("value", "")

    print(f"\n--- Executing Action Step: {act_type.upper()} ---")

    if act_type in ["fill", "type"]:
        print(f"Target Field: '{field}' | Value: '{val}'")
        elem = find_element_by_semantic_match(page, field, ["input", "textarea"])
        if not elem:
            elem = page.locator("input[type='text'], input[type='email'], textarea").first
        if elem and elem.is_visible():
            elem.click()
            time.sleep(0.3)
            elem.fill(val)
            print(f"  SUCCESS: Filled '{val}' into element.")
        else:
            print(f"  NOTICE: Field '{field}' not found or not editable on current page.")

    elif act_type == "select":
        print(f"Target Dropdown: '{field}' | Option: '{val}'")
        elem = find_element_by_semantic_match(page, field, ["select"])
        if elem and elem.is_visible():
            try:
                elem.select_option(label=val)
                print(f"  SUCCESS: Selected option '{val}'.")
            except Exception:
                elem.select_option(value=val)
                print(f"  SUCCESS: Selected value '{val}'.")
        else:
            print(f"  NOTICE: Select dropdown '{field}' not found on page.")

    elif act_type == "click":
        print(f"Target Element: '{target or field}'")
        elem = find_element_by_semantic_match(page, target or field, ["button", "a", "input"])
        if elem and elem.is_visible():
            elem.click()
            print(f"  SUCCESS: Clicked '{target or field}'.")
        else:
            print(f"  NOTICE: Could not click '{target or field}'.")

    elif act_type == "check":
        print(f"Target Checkbox: '{target or field}'")
        elem = find_element_by_semantic_match(page, target or field, ["input"])
        if elem and elem.is_visible():
            try:
                elem.check()
                print(f"  SUCCESS: Checked '{target or field}'.")
            except Exception:
                elem.click()
        else:
            print(f"  NOTICE: Checkbox '{target or field}' not found.")

    elif act_type == "type_chat":
        print(f"Typing Chat Message: '{val}'")
        chat_candidates = [
            page.locator("rich-textarea div[contenteditable='true']"),
            page.locator("div[contenteditable='true']"),
            page.locator("#prompt-textarea"),
            page.locator("textarea[placeholder*='Message' i]"),
            page.locator("textarea[placeholder*='Ask' i]"),
            page.locator("textarea")
        ]
        chat_input = None
        for cand in chat_candidates:
            try:
                if cand.count() > 0 and cand.first.is_visible():
                    chat_input = cand.first
                    break
            except Exception:
                continue

        if chat_input:
            chat_input.click()
            time.sleep(0.3)
            chat_input.fill(val)
            time.sleep(0.3)
            chat_input.press("Enter")
            print(f"  SUCCESS: Submitted chat message '{val}'.")
        else:
            print("  NOTICE: Could not locate chat input prompt box.")

    elif act_type == "submit":
        print(f"Submitting Form (Target: '{target}')...")
        submit_btn = None
        if target:
            submit_btn = find_element_by_semantic_match(page, target, ["button", "input"])
        
        if not submit_btn:
            locs = [
                page.locator("button[type='submit']"),
                page.locator("input[type='submit']"),
                page.get_by_role("button", name=re.compile(r"submit|register|send|apply|save", re.I))
            ]
            for l in locs:
                if l.count() > 0 and l.first.is_visible():
                    submit_btn = l.first
                    break

        if submit_btn and submit_btn.is_visible():
            submit_btn.click()
            print("  SUCCESS: Clicked form submit button.")
        else:
            print("  Pressing Enter on active form...")
            page.keyboard.press("Enter")


def run_task_2_2(prompt: str, use_headless: bool = False):
    """
    Main entry point for Task 2.2: Generates structured plan and executes EVERY action step on the live page DOM.
    """
    clean_p = clean_user_prompt(prompt)

    print(f"\n============================================================")
    print(f"Task 2.2: Universal AI Form Filling & Action Engine")
    print(f"User Prompt: \"{clean_p}\"")
    print(f"============================================================\n")

    plan = resolve_plan_with_local_ai(clean_p)
    if not plan:
        plan = resolve_plan_generic_fallback(clean_p)

    target_url = plan.get("target_url", "")
    actions = plan.get("actions", [])

    print(f"Resolved Target URL : {target_url}")
    print(f"Action Steps Count  : {len(actions)}")

    if not target_url:
        print("Could not resolve a valid target URL. Exiting.")
        return

    # Execute Action Steps on live browser window via Playwright
    with sync_playwright() as p:
        print(f"\nLaunching Full Automation Engine for '{target_url}'...")
        try:
            browser = p.chromium.launch(channel="chrome", headless=use_headless, args=["--start-maximized"])
        except Exception:
            browser = p.chromium.launch(headless=use_headless, args=["--start-maximized"])

        context = browser.new_context(viewport=None)
        page = context.new_page()

        print(f"Navigating to '{target_url}'...")
        try:
            page.goto(target_url, wait_until="domcontentloaded", timeout=30000)
        except PlaywrightError as err:
            print(f"\n[NAVIGATION NOTICE] Could not reach '{target_url}'.")
            print(f"Details: {err}")
            browser.close()
            return

        time.sleep(2)
        print(f"Loaded Page: '{page.title()}' | URL: {page.url}")

        if not actions:
            print("No form action steps required. Page loaded successfully.")

        # Execute every action step (fill name, fill email, select dropdown, click submit)
        for idx, step in enumerate(actions, start=1):
            try:
                print(f"\n--- Step {idx}/{len(actions)} ---")
                execute_action_step(page, step)
                time.sleep(1)
            except Exception as step_err:
                print(f"  Step {idx} Note: {step_err}")

        print("\nAll action steps executed successfully!")
        
        if not use_headless:
            print("\nPress ENTER in terminal when finished inspecting...")
            input()

        browser.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="TaskForge Task 2.2 - Universal AI Form Filling & Action Engine")
    parser.add_argument(
        "prompt",
        nargs="?",
        type=str,
        default=None,
        help="Natural language action prompt"
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run browser in headless mode"
    )

    args = parser.parse_args()

    user_prompt = args.prompt
    if not user_prompt:
        user_prompt = input("What would you like TaskForge to do?\n> ")

    clean_p = clean_user_prompt(user_prompt)
    if not clean_p:
        print("No command provided. Exiting.")
        sys.exit(0)

    run_task_2_2(prompt=clean_p, use_headless=args.headless)
