# TaskForge - Project Progress & Execution Status Report

**Last Updated:** October 2, 2026  
**Project:** TaskForge (Offline AI Automation Engine for Desktop & Browser)  
**Status:** Active Development & Modular Testing  
**Ultimate Objective:** Natural Language Desktop & Browser Automation Engine  

---

## 1. Executive Summary

TaskForge is an autonomous AI agent designed to perceive desktop/browser environments,
parse natural language queries, execute window and tab navigation, dynamically discover
and populate form fields on live websites, interact with desktop app UI controls, read
on-screen content via OCR, and perform closed-loop verification.

---

## 2. Task Breakdown & Status

### **Task 1.1: Local AI Brain Connection**
- **File:** [`module1_test_brain.py`](file:///d:/TaskForge/module1_test_brain.py)
- **Status:** ✅ Completed
- **What was done:** Offline LLM inference using local Phi-3 via Ollama (`http://localhost:11434`).

---

### **Task 2.1: Browser Navigation & Window Management**
- **Primary File:** [`task2_1_browser_navigation.py`](file:///d:/TaskForge/task2_1_browser_navigation.py)
- **Status:** ✅ Completed & Verified
- **What was done:**
  1. **Natural Language URL Resolution**: Resolves queries like `"Open Wikipedia"` or `"Go to github.com"` dynamically using regex pattern matching — no hardcoded URL dictionary.
  2. **Active Chrome Integration**: Opens URLs directly in the user's running Chrome window via `start chrome "<url>"` — no profile locks or separate sessions.
  3. **Playwright Fallback**: If Chrome dispatch fails, connects via CDP or launches a persistent context using `%LOCALAPPDATA%\Google\Chrome\User Data`.
  4. **No hardcoding**: All parameters (URL, headless mode) come from CLI args or user input.

---

### **Task 2.2: Live Web Form Filling & Action Engine**
- **Primary File:** [`task2_2_form_interaction.py`](file:///d:/TaskForge/task2_2_form_interaction.py)
- **Status:** ⚠️ Partially Working — **Known Issue Bookmarked**
- **What was done:**
  1. Local Phi-3 AI generates structured multi-step action plans from NL prompts.
  2. Generic fallback planner handles search/chat/fill patterns with regex (no hardcoded domains).
  3. Semantic DOM locator tries label, placeholder, ARIA role, and text strategies.
  4. Executes fill, select, click, check, type_chat, and submit actions.
- **Known Issue:** The browser window opened by `start chrome` is not accessible to Playwright for automated DOM interaction (Playwright needs to control the browser itself). The two flows (system Chrome dispatch vs Playwright automation) are separate. Task 2.2 opens its own Playwright browser correctly, but the page occasionally fails to find expected input fields on some complex sites (e.g. ChatGPT with shadow DOM). Form fill on simple sites (Wikipedia, GitHub search) works.
- **Next step (lower priority):** Improve shadow DOM penetration and retry logic.

---

### **Task 2.3: Screen Content Reading & OCR Engine**
- **Primary File:** [`task2_3_screen_reader.py`](file:///d:/TaskForge/task2_3_screen_reader.py)
- **Status:** ✅ Completed
- **What was done:**
  1. **Dynamic Monitor Selection**: No hardcoded monitor index — dynamically selects from all available monitors.
  2. **Dynamic Region Capture**: Accepts `--region LEFT TOP WIDTH HEIGHT` for any bounding box.
  3. **Auto Tesseract Discovery**: Finds `tesseract.exe` on PATH and common Program Files dirs — no hardcoded path.
  4. **Text Search on Screen**: `--find "text"` runs OCR and reports whether the text is visible on screen.
  5. Returns structured result dict for use by downstream pipeline steps.
- **Commands:**
  ```powershell
  .\venv\Scripts\python.exe task2_3_screen_reader.py                         # full primary monitor OCR
  .\venv\Scripts\python.exe task2_3_screen_reader.py --find "Chrome"         # check if Chrome text visible
  .\venv\Scripts\python.exe task2_3_screen_reader.py --region 0 0 800 600    # capture region
  .\venv\Scripts\python.exe task2_3_screen_reader.py --monitor 0 --output all.png  # all monitors
  ```

---

### **Task 2.4: Desktop App UI Interaction Engine**
- **Primary File:** [`task2_4_desktop_interaction.py`](file:///d:/TaskForge/task2_4_desktop_interaction.py)
- **Status:** ✅ Completed
- **What was done:**
  1. **Dynamic Window Discovery**: Finds windows by partial title keyword using `win32gui.EnumWindows` — no hardcoded titles or class names.
  2. **Multi-Strategy Control Search**: Locates buttons, inputs, checkboxes by ARIA label, title, auto_id, name, and fuzzy text match across all descendants.
  3. **Actions**: `focus`, `click`, `double_click`, `type`, `read`, `list_controls`, `list_windows`.
  4. **Closed-Loop Verification**: Reads back control values after typing to confirm success.
  5. **Auto-Launch**: If the target window is not found, attempts to launch the app.
- **Commands:**
  ```powershell
  .\venv\Scripts\python.exe task2_4_desktop_interaction.py                             # list all windows
  .\venv\Scripts\python.exe task2_4_desktop_interaction.py --app notepad --action list_controls
  .\venv\Scripts\python.exe task2_4_desktop_interaction.py --app notepad --action click --target "File"
  .\venv\Scripts\python.exe task2_4_desktop_interaction.py --app notepad --action type --target "Edit" --value "Hello World"
  .\venv\Scripts\python.exe task2_4_desktop_interaction.py --app calc --action click --target "5"
  ```

---

### **Task 2.5: End-to-End Natural Language Orchestration Pipeline**
- **Primary File:** [`task2_5_nl_pipeline.py`](file:///d:/TaskForge/task2_5_nl_pipeline.py)
- **Status:** ✅ Completed
- **What was done:**
  1. **Phi-3 AI Pipeline Planner**: Sends the NL command to local Phi-3 and receives a JSON array of typed steps (browser_navigate, web_form_fill, screen_read, desktop_interact, file_operation, notify).
  2. **Rule-Based Fallback Planner**: Pattern-matches common intents (search on site, navigate, OCR, notepad) with zero hardcoded domains — all extracted dynamically from the command.
  3. **Step Dispatcher**: Routes each step to the appropriate sub-engine (Tasks 2.1–2.4 + Module 5 orchestrator).
  4. **Multi-Engine Chaining**: A single command can trigger browser navigation + form fill + OCR + file write in sequence.
  5. **Execution Summary**: Reports ✓/✗ per step at the end.
- **Commands:**
  ```powershell
  .\venv\Scripts\python.exe task2_5_nl_pipeline.py                                            # interactive
  .\venv\Scripts\python.exe task2_5_nl_pipeline.py --command "Go to youtube and search python tutorials"
  .\venv\Scripts\python.exe task2_5_nl_pipeline.py --command "Take a screenshot and check if Chrome is on screen"
  .\venv\Scripts\python.exe task2_5_nl_pipeline.py --command "Open notepad, write a poem about winter, save it"
  .\venv\Scripts\python.exe task2_5_nl_pipeline.py --no-ai --command "search playwright on github"
  ```

---

### **Task 3.1: Windows Calculator — Natural Language Arithmetic**
- **Primary File:** [`task3_1_calculator.py`](file:///d:/TaskForge/task3_1_calculator.py)
- **Status:** ✅ Completed & Integrated into Orchestrator
- **What was done:**
  1. **Natural Language Parser**: Converts any phrasing (`"what is 245 times 12"`, `"square root of 144"`, `"100 divided by 4"`) into Calculator button sequences — no hardcoded if/elif per number.
  2. **Dynamic Button Discovery**: Finds every Calculator button at runtime by scanning UIA descendants — zero hardcoded labels or control IDs.
  3. **Dynamic Display Reader**: Reads the result from the Calculator display control using multiple auto-id/class strategies — works on Windows 10 and 11.
  4. **Closed-Loop Verification**: Computes the expected answer in Python and compares to Calculator's display result.
  5. **Orchestrator Integration**: `module5_orchestrator.py` now routes arithmetic commands (e.g. `"what is 50 times 3"`) directly to this engine.
- **Commands:**
  ```powershell
  .\venv\Scripts\python.exe task3_1_calculator.py --expr "what is 245 times 12"
  .\venv\Scripts\python.exe task3_1_calculator.py --expr "square root of 144"
  .\venv\Scripts\python.exe task3_1_calculator.py --expr "100 divided by 4"
  .\venv\Scripts\python.exe task3_1_calculator.py  # interactive mode
  # Or via master orchestrator:
  .\venv\Scripts\python.exe module5_orchestrator.py  # then type: what is 50 times 3
  ```

---

### **Task 3.2: System Settings — Natural Language System Parameter Control**
- **Primary File:** [`task3_2_system_settings.py`](file:///d:/TaskForge/task3_2_system_settings.py)
- **Status:** ✅ Completed & Integrated into Orchestrator
- **What was done:**
  1. **Natural Language Parser**: Ordered regex intent-matching converts any phrasing into a typed action dict. No hardcoded device names, adapter names, or fixed values anywhere — all numeric parameters (level, step) are extracted dynamically from the command text.
  2. **Wi-Fi Toggle** (`netsh`): Discovers the active Wi-Fi adapter name at runtime via `netsh wlan show interfaces`. Enables/disables/queries the adapter using its discovered name. Reads state back after every change for closed-loop verification. Requires Admin rights to toggle; clearly reports this if not elevated.
  3. **Display Brightness** (WMI + PowerShell fallback): Reads and sets brightness via Python `wmi` package (`WmiMonitorBrightness` / `WmiMonitorBrightnessMethods`). Falls back automatically to PowerShell WMI if the Python package fails. Reads actual brightness back after setting. Gracefully explains when a monitor doesn't expose WMI brightness (common for external displays).
  4. **Master Volume** (`pycaw` Core Audio): Gets and sets master volume scalar (0–100), mutes/unmutes, and reads back. Compatible with both old and new pycaw API (`AudioDevice.EndpointVolume` vs legacy `Activate/QueryInterface` path — auto-detected at runtime).
  5. **Closed-Loop Verification**: Every write operation reads back the hardware state before reporting success.
  6. **Orchestrator Integration**: `module5_orchestrator.py` now routes system settings commands (Wi-Fi, volume, brightness, mute) via a compiled regex `_SYSTEM_SETTINGS_RE` — deterministic, no AI call needed for these short commands.
- **Packages added:** `pycaw`, `wmi` (both `pip install`able without admin rights)
- **Commands:**
  ```powershell
  # Standalone
  .\venv\Scripts\python.exe task3_2_system_settings.py                              # interactive
  .\venv\Scripts\python.exe task3_2_system_settings.py --command "mute"
  .\venv\Scripts\python.exe task3_2_system_settings.py --command "unmute"
  .\venv\Scripts\python.exe task3_2_system_settings.py --command "set volume to 60"
  .\venv\Scripts\python.exe task3_2_system_settings.py --command "increase volume by 20"
  .\venv\Scripts\python.exe task3_2_system_settings.py --command "volume status"
  .\venv\Scripts\python.exe task3_2_system_settings.py --command "brightness 70"
  .\venv\Scripts\python.exe task3_2_system_settings.py --command "dim to 30"
  .\venv\Scripts\python.exe task3_2_system_settings.py --command "increase brightness by 15"
  .\venv\Scripts\python.exe task3_2_system_settings.py --command "what is the brightness"
  .\venv\Scripts\python.exe task3_2_system_settings.py --command "wifi status"
  .\venv\Scripts\python.exe task3_2_system_settings.py --command "turn on wifi"
  .\venv\Scripts\python.exe task3_2_system_settings.py --command "disable wifi"
  .\venv\Scripts\python.exe task3_2_system_settings.py --command "toggle wifi"
  # Or via master orchestrator:
  .\venv\Scripts\python.exe module5_orchestrator.py  # then type any of the above
  ```

---

| Module / Skill | File | Description | Status |
| :--- | :--- | :--- | :--- |
| **Screen Perception Engine** | [`module2_screenshot_test.py`](file:///d:/TaskForge/module2_screenshot_test.py) | Multi-monitor screenshot capture (`mss.MSS`) | ✅ Completed |
| **Desktop App Execution** | [`module3_execution_test.py`](file:///d:/TaskForge/module3_execution_test.py) | Application launching & clipboard paste typing | ✅ Completed |
| **Verification Loop** | [`module4_verification_test.py`](file:///d:/TaskForge/module4_verification_test.py) | Closed-loop self-correcting UI readback engine | ✅ Completed |
| **Task Orchestrator** | [`module5_orchestrator.py`](file:///d:/TaskForge/module5_orchestrator.py) | Master NL entry point & execution engine | ✅ Active |
| **Explorer Perception** | [`module6_explorer_perception_test.py`](file:///d:/TaskForge/module6_explorer_perception_test.py) | File Explorer UIA control reader | ✅ Completed |
| **Explorer Operations** | [`module7_explorer_move_test.py`](file:///d:/TaskForge/module7_explorer_move_test.py) | File Explorer movement & rename verification | ✅ Completed |
| **Browser Navigation** | [`task2_1_browser_navigation.py`](file:///d:/TaskForge/task2_1_browser_navigation.py) | NL → URL → Active Chrome dispatch | ✅ Completed |
| **Web Form Filling** | [`task2_2_form_interaction.py`](file:///d:/TaskForge/task2_2_form_interaction.py) | Playwright DOM form fill engine | ⚠️ Partial |
| **Screen OCR Reader** | [`task2_3_screen_reader.py`](file:///d:/TaskForge/task2_3_screen_reader.py) | mss capture + Tesseract OCR text reading | ✅ Completed |
| **Desktop UI Interaction** | [`task2_4_desktop_interaction.py`](file:///d:/TaskForge/task2_4_desktop_interaction.py) | pywinauto UIA control click/type/read | ✅ Completed |
| **NL Pipeline** | [`task2_5_nl_pipeline.py`](file:///d:/TaskForge/task2_5_nl_pipeline.py) | End-to-end multi-engine orchestration | ✅ Completed |
| **Calculator Engine** | [`task3_1_calculator.py`](file:///d:/TaskForge/task3_1_calculator.py) | NL arithmetic → Windows Calculator UIA | ✅ Completed |
| **System Settings** | [`task3_2_system_settings.py`](file:///d:/TaskForge/task3_2_system_settings.py) | NL Wi-Fi / Brightness / Volume control | ✅ Completed |

---

## 4. Hardcoding Policy

**All files follow zero-hardcoding rules:**
- No hardcoded URLs, domains, or site names
- No hardcoded file paths (resolved via OS env vars or registry)
- No hardcoded window titles, class names, or control IDs
- No hardcoded monitor indices or screen coordinates
- No hardcoded model names in task files (model is configured per-file at the top as a named constant that can be changed, not buried in logic)

The only accepted "constants" are configurable defaults at the top of each file (e.g., `OLLAMA_URL`, `MODEL_NAME`) which are explicitly documented and override-able via environment or CLI arguments.

---

## 5. Known Issues

| Issue | File | Description | Priority |
| :--- | :--- | :--- | :--- |
| Shadow DOM form fill | `task2_2_form_interaction.py` | Some sites (ChatGPT, complex SPAs) use shadow DOM that Playwright can't penetrate with standard locators | Low |
| Chat input detection | `task2_2_form_interaction.py` | type_chat action may miss dynamic React/Vue input components | Low |
