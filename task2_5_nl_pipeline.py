"""
Task 2.5: End-to-End Natural Language Orchestration Pipeline
Purpose: Accept a single natural-language command and execute a complete multi-step
automated workflow combining browser navigation, form filling, screen reading (OCR),
desktop app interaction, and file operations — all without hardcoding any targets,
URLs, selectors, or file paths.

This is the capstone task that ties together:
  - Task 2.1: Browser Navigation (task2_1_browser_navigation.py)
  - Task 2.2: Web Form Filling (task2_2_form_interaction.py)
  - Task 2.3: Screen Content OCR (task2_3_screen_reader.py)
  - Task 2.4: Desktop App Interaction (task2_4_desktop_interaction.py)
  - Module 5: Orchestrator (module5_orchestrator.py)

The local Phi-3 AI (via Ollama) classifies the command intent and generates a
multi-step pipeline plan. Each step is dispatched to the appropriate sub-engine.

How to run:
1. Activate venv: .\\venv\\Scripts\\activate
2. Run: python task2_5_nl_pipeline.py
   Or with a command directly:
     python task2_5_nl_pipeline.py --command "Go to youtube and search python tutorials"
     python task2_5_nl_pipeline.py --command "Open notepad, write a poem about the sky, save it"
     python task2_5_nl_pipeline.py --command "Take a screenshot and read the text on screen"
     python task2_5_nl_pipeline.py --command "Go to github, search playwright, open notepad, write the results"
"""

import os
import sys
import time
import json
import re
import argparse
import requests
from typing import Optional, List

# Import sub-engines
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from task2_1_browser_navigation import parse_natural_language_url, open_in_active_system_chrome
except ImportError:
    parse_natural_language_url = None
    open_in_active_system_chrome = None

try:
    from task2_2_form_interaction import run_task_2_2
except ImportError:
    run_task_2_2 = None

try:
    from task2_3_screen_reader import run_task_2_3
except ImportError:
    run_task_2_3 = None

try:
    from task2_4_desktop_interaction import run_task_2_4
except ImportError:
    run_task_2_4 = None

OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL_NAME = "phi3:mini"


# ---------------------------------------------------------------------------
# AI Pipeline Planner
# ---------------------------------------------------------------------------

PIPELINE_SYSTEM_PROMPT = """\
You are an offline AI automation pipeline planner. Given a natural language task,
generate a JSON array of steps to execute. Each step must use one of these types:

- {"type": "browser_navigate", "query": "<site or URL to open>"}
- {"type": "web_form_fill", "prompt": "<full natural language prompt for form filling engine>"}
- {"type": "screen_read", "find": "<text to search for on screen, or empty string to read all>"}
- {"type": "desktop_interact", "app": "<app window keyword>", "action": "<click|type|read|list_controls>", "target": "<control label>", "value": "<text if typing>"}
- {"type": "file_operation", "operation": "<open_app|type_text|save_file|move_file|rename_file|delete_file|open_folder>", "params": {<operation-specific params>}}
- {"type": "notify", "message": "<short status message to print>"}

Output ONLY a valid JSON array. No markdown. No explanation.

Examples:
User: "Go to youtube and search for python tutorials"
Output: [
  {"type": "browser_navigate", "query": "youtube.com"},
  {"type": "web_form_fill", "prompt": "go to youtube search python tutorials"}
]

User: "Open notepad, write a haiku about autumn, save as haiku.txt in documents"
Output: [
  {"type": "file_operation", "operation": "open_app", "params": {}},
  {"type": "file_operation", "operation": "type_text", "params": {"value": "Autumn leaves falling\\nSilent winds carry them far\\nWinter waits ahead"}},
  {"type": "file_operation", "operation": "save_file", "params": {"filename": "documents/haiku.txt"}}
]

User: "Take a screenshot and check if Chrome is visible on screen"
Output: [
  {"type": "screen_read", "find": "Chrome"}
]

Now generate the plan for this task:
"""


def plan_with_ai(command: str) -> Optional[List[dict]]:
    """Calls local Phi-3 LLM to generate a multi-step pipeline plan."""
    full_prompt = f"{PIPELINE_SYSTEM_PROMPT}\nUser: \"{command}\"\nOutput:"
    try:
        response = requests.post(
            OLLAMA_URL,
            json={"model": MODEL_NAME, "prompt": full_prompt, "stream": False},
            timeout=12,
        )
        if response.status_code == 200:
            raw = response.json().get("response", "").strip()
            raw = re.sub(r"^```json\s*", "", raw, flags=re.I)
            raw = re.sub(r"\s*```$", "", raw)
            match = re.search(r"\[.*\]", raw, re.DOTALL)
            if match:
                parsed = json.loads(match.group(0))
                if isinstance(parsed, list):
                    print(f"  [AI] Generated {len(parsed)}-step pipeline.")
                    return parsed
    except Exception as e:
        print(f"  [AI] Offline or error: {e}. Falling back to rule-based planner.")
    return None


def plan_with_rules(command: str) -> List[dict]:
    """
    Rule-based fallback planner — no hardcoded site names.
    Parses the command structure to detect intent patterns.
    """
    cmd = command.strip().lower()
    steps = []

    # Pattern: "search/find/watch/play X on/in Y" or "go to Y and search X"
    search_on_match = re.match(
        r"(?:go\s+to\s+([a-z0-9.\-]+)\s+(?:and\s+)?(?:search|find|look\s+up|query|type)\s+(.+))|"
        r"(?:(?:search|find|look\s+up|play|watch|run)\s+(.+?)\s+(?:on|in|at)\s+([a-z0-9.\-]+))",
        cmd,
    )
    if search_on_match:
        g = search_on_match.groups()
        site = (g[0] or g[3] or "google").strip()
        query = (g[1] or g[2] or command).strip()
        steps.append({"type": "web_form_fill", "prompt": f"go to {site} and search {query}"})
        return steps

    # Pattern: "open X" or "go to X" or "navigate to X" — pure navigation
    nav_match = re.match(
        r"^(?:open|go\s+to|navigate\s+to|visit|show|load)\s+(.+)$", cmd
    )
    if nav_match:
        target = nav_match.group(1).strip()
        # Check if it also has action verbs — use form fill
        if any(v in target for v in ["search", "find", "type", "fill", "write", "enter"]):
            steps.append({"type": "web_form_fill", "prompt": command})
        else:
            steps.append({"type": "browser_navigate", "query": target})
        return steps

    # Pattern: OCR / screen reading
    if any(kw in cmd for kw in ["screenshot", "screen read", "read screen", "ocr", "what is on screen", "what's on screen"]):
        find_match = re.search(r"(?:find|check|look for|search for)\s+['\"]?([^'\"]+)['\"]?", cmd)
        find_term = find_match.group(1) if find_match else ""
        steps.append({"type": "screen_read", "find": find_term})
        return steps

    # Pattern: file/notepad operations
    if any(kw in cmd for kw in ["notepad", "write", "create", "note", "save", "document"]):
        steps.append({"type": "file_operation", "operation": "open_app", "params": {}})
        steps.append({"type": "notify", "message": f"Delegating to orchestrator for: '{command}'"})
        return steps

    # Default: treat the whole thing as a web form fill command
    steps.append({"type": "web_form_fill", "prompt": command})
    return steps


# ---------------------------------------------------------------------------
# Step Executors
# ---------------------------------------------------------------------------

def execute_browser_navigate(step: dict) -> dict:
    """Executes a browser_navigate step using Task 2.1 engine."""
    query = step.get("query", "")
    print(f"\n[Step: BROWSER_NAVIGATE] Query: '{query}'")
    if parse_natural_language_url and open_in_active_system_chrome:
        url = parse_natural_language_url(query)
        print(f"  Resolved URL: {url}")
        success = open_in_active_system_chrome(url)
        time.sleep(1.5)
        return {"success": success, "url": url}
    else:
        # Fallback via webbrowser
        import webbrowser
        url = query if query.startswith("http") else f"https://www.{query}.com"
        webbrowser.open(url)
        time.sleep(1.5)
        return {"success": True, "url": url, "note": "via webbrowser fallback"}


def execute_web_form_fill(step: dict) -> dict:
    """Executes a web_form_fill step using Task 2.2 engine."""
    prompt = step.get("prompt", "")
    print(f"\n[Step: WEB_FORM_FILL] Prompt: '{prompt}'")
    if run_task_2_2:
        try:
            run_task_2_2(prompt=prompt, use_headless=False)
            return {"success": True, "prompt": prompt}
        except Exception as e:
            return {"success": False, "error": str(e)}
    else:
        print("  [Warning] task2_2_form_interaction module not available.")
        return {"success": False, "error": "Module not loaded"}


def execute_screen_read(step: dict) -> dict:
    """Executes a screen_read step using Task 2.3 engine."""
    find_term = step.get("find", "")
    print(f"\n[Step: SCREEN_READ] Find: '{find_term}'")
    if run_task_2_3:
        try:
            result = run_task_2_3(find=find_term if find_term else None)
            return {"success": True, "result": result}
        except Exception as e:
            return {"success": False, "error": str(e)}
    else:
        print("  [Warning] task2_3_screen_reader module not available.")
        return {"success": False, "error": "Module not loaded"}


def execute_desktop_interact(step: dict) -> dict:
    """Executes a desktop_interact step using Task 2.4 engine."""
    app = step.get("app", "")
    action = step.get("action", "focus")
    target = step.get("target", "")
    value = step.get("value", "")
    print(f"\n[Step: DESKTOP_INTERACT] App: '{app}' | Action: '{action}' | Target: '{target}'")
    if run_task_2_4:
        try:
            result = run_task_2_4(
                app_hint=app,
                action=action,
                target=target or None,
                value=value or None,
            )
            return {"success": result.get("success", False), "result": result}
        except Exception as e:
            return {"success": False, "error": str(e)}
    else:
        print("  [Warning] task2_4_desktop_interaction module not available.")
        return {"success": False, "error": "Module not loaded"}


def execute_file_operation(step: dict) -> dict:
    """Executes a file_operation step by delegating to module5_orchestrator actions."""
    operation = step.get("operation", "")
    params = step.get("params", {})
    print(f"\n[Step: FILE_OPERATION] Operation: '{operation}' | Params: {params}")
    try:
        from module5_orchestrator import (
            action_open_app,
            action_type_text,
            action_save_file,
            action_open_folder,
            action_move_file,
            action_rename_file,
            action_delete_file,
        )
        op_map = {
            "open_app": lambda: action_open_app(),
            "type_text": lambda: action_type_text(params.get("value", "")),
            "save_file": lambda: action_save_file(params.get("filename", "notes.txt")),
            "open_folder": lambda: action_open_folder(params.get("folder", "documents")),
            "move_file": lambda: action_move_file(
                params.get("filename", ""),
                params.get("dest_folder", "documents"),
                params.get("source_folder", None),
            ),
            "rename_file": lambda: action_rename_file(
                params.get("old_name", ""),
                params.get("new_name", ""),
            ),
            "delete_file": lambda: action_delete_file(
                params.get("filename", ""),
                params.get("folder", None),
            ),
        }
        if operation in op_map:
            op_map[operation]()
            return {"success": True, "operation": operation}
        else:
            return {"success": False, "error": f"Unknown file operation: '{operation}'"}
    except Exception as e:
        return {"success": False, "error": str(e)}


def execute_notify(step: dict) -> dict:
    """Prints a status notification."""
    msg = step.get("message", "")
    print(f"\n[Step: NOTIFY] {msg}")
    return {"success": True}


STEP_EXECUTORS = {
    "browser_navigate": execute_browser_navigate,
    "web_form_fill": execute_web_form_fill,
    "screen_read": execute_screen_read,
    "desktop_interact": execute_desktop_interact,
    "file_operation": execute_file_operation,
    "notify": execute_notify,
}


# ---------------------------------------------------------------------------
# Main Pipeline Runner
# ---------------------------------------------------------------------------

def run_pipeline(command: str, use_ai_planner: bool = True) -> List[dict]:
    """
    Runs the end-to-end NL pipeline for a given command.
    Returns a list of step results.
    """
    print("\n============================================================")
    print("Task 2.5: End-to-End Natural Language Orchestration Pipeline")
    print(f"Command: \"{command}\"")
    print("============================================================\n")

    # Generate plan
    plan = None
    if use_ai_planner:
        print("Generating pipeline plan with local AI (Phi-3)...")
        plan = plan_with_ai(command)

    if not plan:
        print("Using rule-based planner (AI offline or unavailable)...")
        plan = plan_with_rules(command)

    print(f"\nGenerated {len(plan)}-step pipeline:")
    for i, step in enumerate(plan, 1):
        print(f"  Step {i}: {json.dumps(step)}")

    # Execute plan
    print("\nExecuting pipeline steps...\n")
    results = []
    for i, step in enumerate(plan, 1):
        step_type = step.get("type", "unknown")
        print(f"--- Pipeline Step {i}/{len(plan)}: {step_type.upper()} ---")

        executor = STEP_EXECUTORS.get(step_type)
        if executor is None:
            print(f"  [Warning] No executor for step type: '{step_type}'. Skipping.")
            results.append({"step": i, "type": step_type, "success": False, "error": "No executor"})
            continue

        try:
            result = executor(step)
            results.append({"step": i, "type": step_type, **result})
            if not result.get("success", True):
                print(f"  [Notice] Step {i} did not complete fully: {result.get('error', '')}")
        except Exception as e:
            print(f"  [Error] Step {i} failed: {e}")
            results.append({"step": i, "type": step_type, "success": False, "error": str(e)})

        time.sleep(0.5)

    # Summary
    print("\n============================================================")
    print("Pipeline Execution Summary:")
    for r in results:
        status = "✓" if r.get("success", False) else "✗"
        print(f"  {status} Step {r['step']} [{r['type']}]", end="")
        if not r.get("success", True):
            print(f" — {r.get('error', 'failed')}", end="")
        print()
    print("============================================================\n")

    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="TaskForge Task 2.5 - End-to-End Natural Language Orchestration Pipeline"
    )
    parser.add_argument(
        "--command",
        "-c",
        type=str,
        default=None,
        help="Natural language command to execute.",
    )
    parser.add_argument(
        "--no-ai",
        action="store_true",
        help="Skip AI planner and use rule-based planning only.",
    )

    args = parser.parse_args()
    command = args.command

    if not command:
        print("============================================================")
        print("         TaskForge — NL Orchestration Pipeline (Task 2.5)   ")
        print("============================================================")
        print("Examples:")
        print("  Go to youtube and search python tutorials")
        print("  Take a screenshot and check if Chrome is on screen")
        print("  Open notepad, write a poem about winter, save it")
        print("  Go to github, search playwright")
        print()
        command = input("Enter your command:\n> ").strip()

    if not command:
        print("No command provided. Exiting.")
        sys.exit(0)

    run_pipeline(command=command, use_ai_planner=not args.no_ai)
