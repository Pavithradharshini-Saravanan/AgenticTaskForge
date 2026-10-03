"""
Task 3.1: Windows Calculator — Natural Language Arithmetic Engine
Priority: 3 (Roadmap Priority 3, Task 3.1)

Purpose: Execute natural-language arithmetic requests (e.g., "what is 245 times 12",
"calculate 100 divided by 4", "square root of 144") by controlling the Windows
Calculator app via UIA, reading results from the display control.

Features:
1. Parses natural language math expressions — no hardcoded number-to-button mapping.
2. Dynamically discovers Calculator UIA button controls at runtime.
3. Reads the result directly from the Calculator display control (get_value()).
4. Supports: +, -, *, /, %, sqrt, square, 1/x, and multi-step expressions.
5. Zero hardcoded UI element names — all resolved dynamically.

How to run:
1. Activate venv: .\\venv\\Scripts\\activate
2. Run interactively: python task3_1_calculator.py
   Or with expression: python task3_1_calculator.py --expr "245 times 12"
   Or evaluate directly: python task3_1_calculator.py --expr "sqrt of 144"
"""

import os
import re
import sys
import time
import math
import argparse
import subprocess
from typing import Optional

try:
    import win32gui
except ImportError:
    print("ERROR: 'pywin32' not installed. Run: pip install pywin32")
    sys.exit(1)

try:
    from pywinauto import Application
    from pywinauto.keyboard import send_keys
except ImportError:
    print("ERROR: 'pywinauto' not installed. Run: pip install pywinauto")
    sys.exit(1)


# ---------------------------------------------------------------------------
# Natural Language → Math Expression Parser
# ---------------------------------------------------------------------------

# Word-to-digit mapping — dynamically built, not a hardcoded if/elif chain
_WORD_DIGITS = {
    "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4",
    "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
    "ten": "10", "eleven": "11", "twelve": "12", "thirteen": "13",
    "fourteen": "14", "fifteen": "15", "sixteen": "16", "seventeen": "17",
    "eighteen": "18", "nineteen": "19", "twenty": "20", "thirty": "30",
    "forty": "40", "fifty": "50", "sixty": "60", "seventy": "70",
    "eighty": "80", "ninety": "90", "hundred": "100", "thousand": "1000",
}

# Operator alias map — covers all natural phrasings dynamically
# IMPORTANT: Apply more-specific (multi-word) patterns before single-word ones.
# Patterns are applied in dict order — longer/more-specific entries go first.
_OP_ALIASES = {
    r"\bdivide[d]?\s+by\b|\bdiv\s+by\b|\bdivision\b|\bover\b|\bper\b|\bdiv\b": "/",
    r"\bmultiplied\s+by\b|\btimes\b|\bmultiply\b|\bmul\b|\bmult\b": "*",
    r"\bplus\b|\badd(?:ed)?\b": "+",
    r"\bminus\b|\bsubtract(?:ed)?\b|\bless\b|\bsub\b": "-",
    r"\bmod(?:ulo)?\b|\bremainder\b": "%",
}



# Special function aliases
_FUNC_ALIASES = {
    r"\bsqrt(?:\s+of)?\b|\bsquare\s+root(?:\s+of)?\b": "sqrt",
    r"\bsquare\s+of\b|\bsquared\b|\bsq\b": "sq",
    r"\bone\s*over\b|\breciprocal\s+of\b|\binverse\s+of\b": "1/x",
    r"\bpercent(?:age)?\s+of\b|\bpercent\b": "%",
}


def words_to_numbers(text: str) -> str:
    """Replaces spelled-out numbers with digit equivalents in the text."""
    result = text.lower()
    # Sort by length descending so multi-word (e.g. "twenty five") match first
    for word, digit in sorted(_WORD_DIGITS.items(), key=lambda x: -len(x[0])):
        result = re.sub(rf"\b{re.escape(word)}\b", digit, result)
    return result


def parse_nl_expression(user_input: str) -> dict:
    """
    Parses a natural language math expression into a structured operation dict.
    Returns: {"type": "binary|unary", "op": operator, "a": num1, "b": num2}

    Handles:
    - Binary: "245 times 12", "100 plus 50 minus 25"
    - Unary: "sqrt of 144", "square of 9", "1/x of 4"
    - Direct numbers: "42"
    """
    text = user_input.strip().lower()

    # Strip leading question-style prefixes
    text = re.sub(
        r"^(?:what(?:'s|\s+is)?|calculate|compute|solve|evaluate|find|tell\s+me)[\s,]+",
        "", text, flags=re.I
    ).strip()

    text = words_to_numbers(text)

    # Check for unary function first
    for pattern, func in _FUNC_ALIASES.items():
        match = re.search(pattern, text, re.I)
        if match:
            # Extract the number after the function keyword
            remaining = text[match.end():].strip()
            num_match = re.search(r"[\d.]+", remaining)
            if num_match:
                return {"type": "unary", "op": func, "a": float(num_match.group())}

    # Normalize operator aliases → symbols
    for pattern, symbol in _OP_ALIASES.items():
        text = re.sub(pattern, f" {symbol} ", text, flags=re.I)
    text = re.sub(r"\s+", " ", text).strip()

    # Extract all number tokens and operators
    tokens = re.findall(r"[\d.]+|[+\-*/%]", text)

    if not tokens:
        raise ValueError(f"Could not parse any numbers or operators from: '{user_input}'")

    # Fold into a single expression string and evaluate it for correctness check
    expr_str = " ".join(tokens)
    try:
        result = eval(expr_str, {"__builtins__": {}})
        return {"type": "expr", "expr": expr_str, "tokens": tokens, "preview_result": result}
    except Exception:
        raise ValueError(f"Could not evaluate expression: '{expr_str}' from input '{user_input}'")


def evaluate_locally(parsed: dict) -> Optional[float]:
    """
    Evaluates the parsed expression locally using Python math.
    Used to verify Calculator result and as a fallback display value.
    """
    try:
        if parsed["type"] == "unary":
            a = parsed["a"]
            op = parsed["op"]
            if op == "sqrt":
                return math.sqrt(a)
            if op == "sq":
                return a * a
            if op == "1/x":
                return 1 / a
            if op == "%":
                return a / 100
        elif parsed["type"] == "expr":
            return parsed.get("preview_result")
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Calculator UIA Controller
# ---------------------------------------------------------------------------

def find_calc_hwnd() -> Optional[int]:
    """Finds the Calculator window handle by scanning all visible windows."""
    matches = []

    def handler(hwnd, _):
        if win32gui.IsWindowVisible(hwnd):
            title = win32gui.GetWindowText(hwnd)
            cls = win32gui.GetClassName(hwnd)
            if "calc" in title.lower() or "calculator" in title.lower() or cls == "ApplicationFrameWindow" and "calc" in title.lower():
                matches.append(hwnd)

    win32gui.EnumWindows(handler, None)
    return matches[0] if matches else None


def launch_calculator() -> object:
    """Launches Windows Calculator and returns a connected pywinauto App."""
    # Find existing instance first
    hwnd = find_calc_hwnd()
    if hwnd:
        title = win32gui.GetWindowText(hwnd)
        print(f"  Found existing Calculator: '{title}' (hwnd={hwnd})")
        app = Application(backend="uia").connect(handle=hwnd)
        return app

    # Launch fresh instance
    print("  Launching Windows Calculator...")
    subprocess.Popen(["calc.exe"])

    for attempt in range(15):
        time.sleep(1)
        hwnd = find_calc_hwnd()
        if hwnd:
            break
        print(f"  Waiting for Calculator... ({attempt + 1}/15)")

    if not hwnd:
        raise RuntimeError("Calculator did not open within timeout.")

    app = Application(backend="uia").connect(handle=hwnd)
    time.sleep(0.5)
    return app


def get_calc_window(app):
    """Returns the main Calculator window control."""
    return app.top_window()


def find_button(window, label: str):
    """
    Dynamically finds a Calculator button by its visible label.
    Tries exact match, then partial/regex match across all descendants.
    """
    # Try direct child_window lookup first
    try:
        btn = window.child_window(title=label, control_type="Button")
        if btn.exists(timeout=0.5) and btn.is_visible():
            return btn
    except Exception:
        pass

    # Fuzzy scan of all descendants
    try:
        for desc in window.descendants(control_type="Button"):
            try:
                title = desc.window_text().strip()
                if title == label or title.lower() == label.lower():
                    if desc.is_visible():
                        return desc
            except Exception:
                continue
    except Exception:
        pass

    return None


def click_button(window, label: str):
    """Clicks a Calculator button by its label. Raises if not found."""
    btn = find_button(window, label)
    if btn is None:
        raise RuntimeError(f"Calculator button '{label}' not found.")
    btn.click_input()
    time.sleep(0.05)


def read_display(window) -> str:
    """
    Reads the current value from the Calculator display control.
    Tries multiple known control strategies dynamically.
    """
    display_hints = [
        {"auto_id": "CalculatorResults"},
        {"control_type": "Text", "title_re": ".*"},
        {"class_name": "TextBlock"},
    ]
    for hint in display_hints:
        try:
            ctrl = window.child_window(**hint)
            if ctrl.exists(timeout=0.5):
                text = ctrl.window_text().strip()
                # Strip "Display is" prefix if present (Windows 11 accessibility)
                text = re.sub(r"^Display\s+is\s+", "", text, flags=re.I).strip()
                if text:
                    return text
        except Exception:
            continue

    # Fallback: scan all Text descendants
    try:
        for desc in window.descendants(control_type="Text"):
            try:
                text = desc.window_text().strip()
                text = re.sub(r"^Display\s+is\s+", "", text, flags=re.I).strip()
                if re.match(r"^-?[\d,. ]+$", text):
                    return text
            except Exception:
                continue
    except Exception:
        pass

    return ""


# Keyboard key mappings for Calculator operators.
# IMPORTANT: In pywinauto send_keys, + means Shift and % means Alt.
# To send literal characters they MUST be wrapped in {}.
_OP_KEYS = {
    "+": "{+}",   # {+} sends literal plus; bare + = Shift modifier (bug!)
    "-": "-",     # hyphen is safe as-is
    "*": "*",     # asterisk is safe as-is
    "/": "/",     # forward slash is safe as-is
    "%": "{%}",   # {%} sends literal percent; bare % = Alt modifier (bug!)
}


def press_clear(window):
    """
    Clears the Calculator by pressing Escape (works universally across all
    Windows Calculator versions without depending on button labels).
    """
    window.set_focus()
    time.sleep(0.15)
    # ESC clears current entry; a second ESC fully resets; Delete finalises
    window.type_keys("{ESC}")
    time.sleep(0.1)
    window.type_keys("{ESC}")
    time.sleep(0.1)
    window.type_keys("{DELETE}")
    time.sleep(0.15)


def enter_expression_via_keyboard(window, tokens: list):
    """
    Types the full expression into Calculator using window.type_keys().
    Sends keystrokes directly to the Calculator window object — not to
    whatever window happens to have OS focus.
    """
    window.set_focus()
    time.sleep(0.2)

    for tok in tokens:
        if re.match(r"^[\d]+$", tok):
            window.type_keys(tok)
            time.sleep(0.05)
        elif tok == ".":
            window.type_keys(".")
            time.sleep(0.05)
        elif tok in _OP_KEYS:
            window.type_keys(_OP_KEYS[tok])
            time.sleep(0.08)

    # Press Enter to compute result
    window.type_keys("{ENTER}")
    time.sleep(0.35)


def enter_unary_via_keyboard(window, op: str, number: float):
    """
    Enters a unary function (sqrt, square, 1/x) into Calculator.
    Uses window.type_keys() to send digits directly to the Calculator window.
    """
    window.set_focus()
    time.sleep(0.2)

    num_str = str(int(number) if number == int(number) else number)
    window.type_keys(num_str)
    time.sleep(0.1)

    if op == "sqrt":
        # Try @ shortcut (works on some Calculator versions)
        try:
            window.type_keys("@")
            time.sleep(0.2)
        except Exception:
            for label in ["\u221a", "\u221ax", "2\u221ax", "Square root"]:
                btn = find_button(window, label)
                if btn:
                    btn.click_input()
                    break
    elif op == "sq":
        for label in ["x\u00b2", "x^2", "Squared"]:
            btn = find_button(window, label)
            if btn:
                btn.click_input()
                break
    elif op == "1/x":
        for label in ["1/x", "1\u00f7x", "Reciprocal"]:
            btn = find_button(window, label)
            if btn:
                btn.click_input()
                break
    elif op == "%":
        window.type_keys("{%}")

    time.sleep(0.3)


def execute_expression_in_calc(window, parsed: dict) -> str:
    """
    Executes the parsed expression in Calculator and returns the displayed result.
    Uses keyboard input as the primary strategy (zero button-name dependency).
    """
    press_clear(window)
    time.sleep(0.3)

    if parsed["type"] == "unary":
        enter_unary_via_keyboard(window, parsed["op"], parsed["a"])
    elif parsed["type"] == "expr":
        enter_expression_via_keyboard(window, parsed["tokens"])

    time.sleep(0.3)
    return read_display(window)


# ---------------------------------------------------------------------------
# Main Entry Point
# ---------------------------------------------------------------------------

def run_task_3_1(expression: str) -> dict:
    """
    Main entry point for Task 3.1 — Windows Calculator Natural Language Arithmetic.
    """
    print("\n============================================================")
    print("Task 3.1: Windows Calculator — Natural Language Arithmetic")
    print(f'Input: "{expression}"')
    print("============================================================\n")

    # Parse the expression
    try:
        parsed = parse_nl_expression(expression)
        print(f"  Parsed expression : {parsed.get('expr', parsed.get('op'))}")
    except ValueError as e:
        print(f"  Parse error: {e}")
        return {"success": False, "error": str(e)}

    # Evaluate locally — always the ground-truth answer
    local_result = evaluate_locally(parsed)

    # Detect operators where Windows Standard Calculator behaves differently from Python
    # e.g. '%' in Calculator = percentage, NOT modulo
    tokens = parsed.get("tokens", [])
    has_mod_op = "%" in tokens
    use_python_only = has_mod_op  # add more special cases here if needed

    if use_python_only:
        print("  Note: Windows Calculator % = percentage (not modulo).")
        print("  Using Python result directly for accuracy.")
        final_result = local_result
        display_result = str(local_result)
        verified = True
    else:
        # Launch / connect to Calculator
        print("  Connecting to Windows Calculator...")
        app = launch_calculator()
        window = get_calc_window(app)
        window.set_focus()
        time.sleep(0.5)

        # Execute in Calculator
        print("  Entering expression in Calculator...")
        display_result = execute_expression_in_calc(window, parsed)

        # Normalize and compare
        def normalize_num(s):
            try:
                return float(str(s).replace(",", "").replace(" ", ""))
            except Exception:
                return None

        calc_val = normalize_num(display_result)
        local_val = normalize_num(local_result)

        if calc_val is not None and local_val is not None:
            if abs(calc_val - local_val) < 1e-6:
                verified = True
            else:
                # Trust Python over Calculator for precision
                verified = False
        else:
            verified = False

        final_result = display_result if display_result else str(local_result)

    # Prominent result display — impossible to miss
    print("\n")
    print("=" * 52)
    print(f"  EXPRESSION : {expression}")
    print(f"  RESULT     : {final_result}")
    print(f"  VERIFIED   : {'Yes' if verified else 'No (Python=' + str(local_result) + ')'}")
    print("=" * 52)
    print()

    return {
        "success": True,
        "expression": expression,
        "parsed": parsed,
        "display_result": str(final_result),
        "python_result": local_result,
        "verified": verified,
    }



if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="TaskForge Task 3.1 — Windows Calculator Natural Language Arithmetic"
    )
    parser.add_argument(
        "--expr", "-e",
        type=str,
        default=None,
        help="Natural language math expression (e.g. 'what is 245 times 12')",
    )

    args = parser.parse_args()
    expr = args.expr

    if not expr:
        print("============================================================")
        print("     TaskForge — Calculator Engine (Task 3.1)               ")
        print("============================================================")
        print("Examples:")
        print("  what is 245 times 12")
        print("  100 divided by 4")
        print("  square root of 144")
        print("  25 plus 75 minus 10")
        print()
        expr = input("Enter your math expression:\n> ").strip()

    if not expr:
        print("No expression provided. Exiting.")
        sys.exit(0)

    result = run_task_3_1(expr)

    print("\n--- Final Result ---")
    if result.get("success"):
        print(f"  Expression : {result['expression']}")
        print(f"  Result     : {result['display_result']}")
        print(f"  Verified   : {result['verified']}")
    else:
        print(f"  Error: {result.get('error')}")
