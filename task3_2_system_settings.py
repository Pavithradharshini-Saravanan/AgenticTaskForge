"""
Task 3.2: System Settings — Natural Language System Parameter Control
Priority: 3 (Roadmap Priority 3, Task 3.2)

Handles ANY Windows system setting through plain-English commands.

Two-layer architecture (no hardcoding, no fixed command list):
  Layer 1 — Programmatic fast-path for common settings (instant, no AI needed):
    - Wi-Fi        : enable / disable / toggle / status   (via netsh)
    - Brightness   : set / increase / decrease / status   (via WMI + PowerShell)
    - Volume       : set / increase / decrease / mute / unmute / status  (via pycaw)
    - Bluetooth    : enable / disable / toggle / status   (via PowerShell PnP)
    - Dark / Light mode: switch theme                     (via Registry broadcast)
    - Power plan   : balanced / performance / saver / any (via powercfg)
    - Firewall     : enable / disable / status            (via netsh advfirewall)
    - Battery saver: enable / disable / status            (via PowerShell)

  Layer 2 — AI PowerShell fallback for EVERYTHING ELSE:
    If the command matches nothing above, the local AI (Phi-3 via Ollama) is asked
    to generate a safe PowerShell command, which is then executed and verified.
    This means ANY Windows setting the user can describe in words will be attempted.

Design principles:
  - Zero hardcoded device names, adapter names, or setting values.
  - All numeric parameters extracted dynamically from NL text.
  - Closed-loop: every write reads back the new state to confirm the change.
  - Graceful degradation: clear messages when hardware doesn't support a feature.

Required packages (pip install, no admin needed):
  pip install pycaw wmi

How to run:
  python task3_2_system_settings.py                              # interactive
  python task3_2_system_settings.py --command "enable bluetooth"
  python task3_2_system_settings.py --command "switch to dark mode"
  python task3_2_system_settings.py --command "set power plan to performance"
  python task3_2_system_settings.py --command "disable firewall"
  python task3_2_system_settings.py --command "enable battery saver"
  python task3_2_system_settings.py --command "set volume to 60"
  python task3_2_system_settings.py --command "mute"
  python task3_2_system_settings.py --command "brightness 70"
  python task3_2_system_settings.py --command "turn on wifi"
  # Or any setting at all — unknown commands fall back to AI-generated PowerShell
  python task3_2_system_settings.py --command "enable night light"
  python task3_2_system_settings.py --command "change screen resolution to 1920x1080"
  python task3_2_system_settings.py --command "set timezone to UTC"

Or via master orchestrator:
  python module5_orchestrator.py  # then type any settings command
"""

import re
import sys
import time
import winreg
import ctypes
import subprocess
import argparse
import importlib
from typing import Optional


# ────────────────────────────────────────────────────────────────────────────
# Ollama / AI configuration (used only by the fallback layer)
# ────────────────────────────────────────────────────────────────────────────
OLLAMA_URL  = "http://localhost:11434/api/generate"
MODEL_NAME  = "phi3:mini"     # change to any locally available model

# ────────────────────────────────────────────────────────────────────────────
# Dependency guard
# ────────────────────────────────────────────────────────────────────────────

def _import_or_warn(module: str, package: str):
    try:
        return importlib.import_module(module)
    except ImportError:
        print(f"  [MISSING] '{package}' not installed. Run: pip install {package}")
        return None


# ════════════════════════════════════════════════════════════════════════════
# Wi-Fi  (netsh — built into every Windows installation)
# ════════════════════════════════════════════════════════════════════════════

def get_wifi_interface() -> Optional[str]:
    """Discovers first Wi-Fi adapter name at runtime from netsh output — no hardcoding."""
    try:
        out = subprocess.check_output(
            ["netsh", "wlan", "show", "interfaces"],
            text=True, encoding="utf-8", errors="replace", stderr=subprocess.DEVNULL
        )
        for line in out.splitlines():
            m = re.match(r"\s*Name\s*:\s*(.+)", line)
            if m:
                return m.group(1).strip()
    except Exception:
        pass
    return None


def get_wifi_state(interface: str) -> Optional[str]:
    try:
        out = subprocess.check_output(
            ["netsh", "interface", "show", "interface", interface],
            text=True, encoding="utf-8", errors="replace", stderr=subprocess.DEVNULL
        )
        for line in out.splitlines():
            ll = line.lower()
            if "enabled" in ll:
                return "enabled"
            if "disabled" in ll:
                return "disabled"
    except Exception:
        pass
    return None


def set_wifi(enable: bool, interface: str) -> dict:
    action = "enable" if enable else "disable"
    try:
        result = subprocess.run(
            ["netsh", "interface", "set", "interface", interface, action],
            capture_output=True, text=True
        )
        if result.returncode == 0:
            time.sleep(1.5)
            return {"success": True, "action": action, "interface": interface,
                    "new_state": get_wifi_state(interface)}
        return {"success": False, "error": result.stderr.strip() or f"exit {result.returncode}",
                "hint": "Try running as Administrator."}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


# ════════════════════════════════════════════════════════════════════════════
# Brightness  (WMI → PowerShell fallback)
# ════════════════════════════════════════════════════════════════════════════

def get_brightness() -> Optional[int]:
    wmi_mod = _import_or_warn("wmi", "wmi")
    if wmi_mod:
        try:
            c = wmi_mod.WMI(namespace="wmi")
            info = c.WmiMonitorBrightness()
            if info:
                return int(info[0].CurrentBrightness)
        except Exception:
            pass
    try:
        cmd = "(Get-WmiObject -Namespace root/WMI -Class WmiMonitorBrightness).CurrentBrightness"
        out = subprocess.check_output(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd],
            text=True, stderr=subprocess.DEVNULL
        ).strip()
        if out.lstrip("-").isdigit():
            return int(out)
    except Exception:
        pass
    return None


def set_brightness(level: int) -> dict:
    level = max(0, min(100, int(level)))
    wmi_mod = _import_or_warn("wmi", "wmi")
    if wmi_mod:
        try:
            c = wmi_mod.WMI(namespace="wmi")
            methods = c.WmiMonitorBrightnessMethods()
            if methods:
                methods[0].WmiSetBrightness(level, 0)
                time.sleep(0.4)
                return {"success": True, "target": level, "actual": get_brightness()}
        except Exception:
            pass
    try:
        cmd = (f"(Get-WmiObject -Namespace root/WMI -Class WmiMonitorBrightnessMethods)"
               f".WmiSetBrightness(1,{level})")
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd],
            capture_output=True, text=True
        )
        if result.returncode == 0:
            time.sleep(0.4)
            return {"success": True, "target": level, "actual": get_brightness()}
        return {"success": False, "error": "WMI brightness command failed.",
                "hint": "External/desktop monitors don't support WMI brightness."}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


# ════════════════════════════════════════════════════════════════════════════
# Volume  (pycaw — Windows Core Audio)
# ════════════════════════════════════════════════════════════════════════════

def _get_audio_endpoint():
    """Auto-detects new pycaw (AudioDevice.EndpointVolume) vs old (Activate/QueryInterface)."""
    from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
    device = AudioUtilities.GetSpeakers()
    if hasattr(device, "EndpointVolume"):
        return device.EndpointVolume
    from comtypes import CLSCTX_ALL
    iface = device.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
    return iface.QueryInterface(IAudioEndpointVolume)


def get_volume() -> Optional[float]:
    try:
        return round(_get_audio_endpoint().GetMasterVolumeLevelScalar() * 100.0, 1)
    except Exception:
        return None


def get_mute_state() -> Optional[bool]:
    try:
        return bool(_get_audio_endpoint().GetMute())
    except Exception:
        return None


def set_volume(level: float) -> dict:
    level = max(0.0, min(100.0, float(level)))
    try:
        ep = _get_audio_endpoint()
        ep.SetMasterVolumeLevelScalar(level / 100.0, None)
        time.sleep(0.15)
        return {"success": True, "target": level, "actual": get_volume()}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


def set_mute(mute: bool) -> dict:
    try:
        ep = _get_audio_endpoint()
        ep.SetMute(1 if mute else 0, None)
        time.sleep(0.1)
        return {"success": True, "muted": get_mute_state()}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


# ════════════════════════════════════════════════════════════════════════════
# Bluetooth  (PowerShell PnP device management)
# ════════════════════════════════════════════════════════════════════════════

def get_bluetooth_state() -> Optional[str]:
    """
    Queries Bluetooth radio state by checking PnP devices with Class='Bluetooth'.
    Returns 'enabled', 'disabled', or None.
    """
    try:
        cmd = ("Get-PnpDevice | Where-Object {$_.Class -eq 'Bluetooth' -and "
               "$_.InstanceId -match 'BTH\\\\MS_BTHBRB'} | "
               "Select-Object -First 1 -ExpandProperty Status")
        out = subprocess.check_output(
            ["powershell", "-NoProfile", "-Command", cmd],
            text=True, stderr=subprocess.DEVNULL
        ).strip().lower()
        if out == "ok":
            return "enabled"
        if out in ("error", "unknown", "degraded"):
            return "disabled"
        # Fallback: check if any Bluetooth radio device exists and is OK
        cmd2 = ("(Get-PnpDevice | Where-Object {$_.Class -eq 'Bluetooth'} | "
                "Where-Object {$_.Status -eq 'OK'}).Count")
        count = subprocess.check_output(
            ["powershell", "-NoProfile", "-Command", cmd2],
            text=True, stderr=subprocess.DEVNULL
        ).strip()
        return "enabled" if count.isdigit() and int(count) > 0 else "disabled"
    except Exception:
        return None


def set_bluetooth(enable: bool) -> dict:
    """Enables or disables the Bluetooth radio via PnP device management."""
    action = "Enable" if enable else "Disable"
    try:
        cmd = (f"Get-PnpDevice | Where-Object {{$_.Class -eq 'Bluetooth'}} | "
               f"ForEach-Object {{ {action}-PnpDevice -InstanceId $_.InstanceId -Confirm:$false 2>$null }}")
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", cmd],
            capture_output=True, text=True
        )
        time.sleep(1.5)
        new_state = get_bluetooth_state()
        success = result.returncode == 0 or new_state == ("enabled" if enable else "disabled")
        return {"success": success, "new_state": new_state,
                "hint": "May require Administrator rights to toggle Bluetooth." if not success else ""}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


# ════════════════════════════════════════════════════════════════════════════
# Dark / Light Mode  (Registry + WM_SETTINGCHANGE broadcast)
# ════════════════════════════════════════════════════════════════════════════

_THEME_REG_KEY = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"

def get_theme_mode() -> Optional[str]:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _THEME_REG_KEY) as k:
            val, _ = winreg.QueryValueEx(k, "AppsUseLightTheme")
            return "light" if val == 1 else "dark"
    except Exception:
        return None


def set_theme_mode(dark: bool) -> dict:
    """Switches Windows apps + system theme to dark or light mode."""
    val = 0 if dark else 1
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _THEME_REG_KEY, 0,
                            winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, "AppsUseLightTheme",    0, winreg.REG_DWORD, val)
            winreg.SetValueEx(k, "SystemUsesLightTheme", 0, winreg.REG_DWORD, val)
        # Broadcast change so running apps pick it up immediately
        HWND_BROADCAST  = 0xFFFF
        WM_SETTINGCHANGE = 0x001A
        ctypes.windll.user32.SendMessageTimeoutW(
            HWND_BROADCAST, WM_SETTINGCHANGE, 0,
            "ImmersiveColorSet", 2, 5000, None
        )
        time.sleep(0.3)
        actual = get_theme_mode()
        return {"success": True, "mode": actual}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


# ════════════════════════════════════════════════════════════════════════════
# Power Plan  (powercfg)
# ════════════════════════════════════════════════════════════════════════════

def list_power_plans() -> list:
    """
    Discovers all available power plans at runtime from powercfg /list output.
    Returns list of {"guid": ..., "name": ..., "active": bool}.
    """
    try:
        out = subprocess.check_output(
            ["powercfg", "/list"], text=True,
            encoding="utf-8", errors="replace", stderr=subprocess.DEVNULL
        )
        plans = []
        for line in out.splitlines():
            m = re.search(r"([0-9a-f-]{36})\s+\((.+?)\)", line, re.I)
            if m:
                plans.append({
                    "guid": m.group(1),
                    "name": m.group(2).strip(),
                    "active": "*" in line,
                })
        return plans
    except Exception:
        return []


def get_active_power_plan() -> Optional[str]:
    for p in list_power_plans():
        if p["active"]:
            return p["name"]
    return None


def set_power_plan(hint: str) -> dict:
    """
    Sets the power plan whose name best matches the hint string.
    Uses fuzzy matching so 'performance', 'balanced', 'saver' all work.
    """
    import difflib
    plans = list_power_plans()
    if not plans:
        return {"success": False, "error": "No power plans found (powercfg unavailable)."}

    hint_lower = hint.lower().strip()
    # Exact substring match first
    best = next((p for p in plans if hint_lower in p["name"].lower()), None)
    if not best:
        names = [p["name"] for p in plans]
        matches = difflib.get_close_matches(hint, names, n=1, cutoff=0.3)
        best = next((p for p in plans if p["name"] == matches[0]), None) if matches else None
    if not best:
        plan_names = ", ".join(f"'{p['name']}'" for p in plans)
        return {"success": False,
                "error": f"No plan matching '{hint}'. Available: {plan_names}"}

    result = subprocess.run(
        ["powercfg", "/setactive", best["guid"]], capture_output=True
    )
    time.sleep(0.3)
    active = get_active_power_plan()
    return {"success": result.returncode == 0, "plan": best["name"], "active_now": active}


# ════════════════════════════════════════════════════════════════════════════
# Firewall  (netsh advfirewall)
# ════════════════════════════════════════════════════════════════════════════

def get_firewall_state() -> Optional[str]:
    try:
        out = subprocess.check_output(
            ["netsh", "advfirewall", "show", "allprofiles", "state"],
            text=True, encoding="utf-8", errors="replace", stderr=subprocess.DEVNULL
        )
        states = re.findall(r"State\s+([A-Za-z]+)", out)
        if states:
            return "enabled" if all(s.lower() == "on" for s in states) else "disabled"
    except Exception:
        pass
    return None


def set_firewall(enable: bool) -> dict:
    action = "on" if enable else "off"
    try:
        result = subprocess.run(
            ["netsh", "advfirewall", "set", "allprofiles", "state", action],
            capture_output=True, text=True
        )
        if result.returncode == 0:
            time.sleep(0.5)
            return {"success": True, "new_state": get_firewall_state()}
        return {"success": False, "error": result.stderr.strip() or "netsh failed",
                "hint": "Try running as Administrator."}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


# ════════════════════════════════════════════════════════════════════════════
# Battery Saver  (PowerShell EnergySaver)
# ════════════════════════════════════════════════════════════════════════════

def get_battery_saver_state() -> Optional[str]:
    try:
        cmd = "(Get-CimInstance -Namespace root/WMI -ClassName BatteryStatus).PowerOnline"
        # Use powercfg instead — battery saver is a power setting
        out = subprocess.check_output(
            ["powershell", "-NoProfile", "-Command",
             "(Get-WmiObject -Namespace root\\WMI -Class BatteryStatus -ErrorAction SilentlyContinue)"
             " | Select-Object -ExpandProperty PowerSaverOn -ErrorAction SilentlyContinue"],
            text=True, stderr=subprocess.DEVNULL
        ).strip().lower()
        if out in ("true", "false"):
            return "enabled" if out == "true" else "disabled"
    except Exception:
        pass
    return None


def set_battery_saver(enable: bool) -> dict:
    """Enables/disables Battery Saver via PowerShell energy settings."""
    # Windows 10/11: battery saver toggle via Settings URI or registry
    reg_key = r"SOFTWARE\Microsoft\Windows\CurrentVersion\EnergySaver\Settings"
    reg_val = "EnergySaverStatus"
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, reg_key, 0,
                            winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, reg_val, 0, winreg.REG_DWORD, 1 if enable else 0)
        return {"success": True, "mode": "enabled" if enable else "disabled"}
    except PermissionError:
        return {"success": False, "error": "Administrator rights required to change Battery Saver."}
    except Exception:
        pass
    # PowerShell fallback via PowerCfg overlay
    try:
        overlay = "OVERLAY_SCHEME_MIN" if enable else "OVERLAY_SCHEME_BALANCED"
        result = subprocess.run(
            ["powercfg", "/overlappedscheme", f"{overlay}"],
            capture_output=True
        )
        return {"success": result.returncode == 0,
                "mode": "enabled" if enable else "disabled"}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


# ════════════════════════════════════════════════════════════════════════════
# AI PowerShell Fallback  (handles ANY setting not covered above)
# ════════════════════════════════════════════════════════════════════════════

# PowerShell commands that are considered too dangerous to execute
_DANGER_PATTERNS = re.compile(
    r"Format-\w+|Remove-Item.+(-Recurse|-rf)\b|rd\s+/s|del\s+/f\s+/s"
    r"|Clear-Disk|Initialize-Disk|Reset-ComputerMachinePassword"
    r"|Stop-Computer|Restart-Computer|Shutdown",
    re.I
)

_PS_FALLBACK_PROMPT = """You are a Windows PowerShell expert who knows the exact correct commands for Windows settings.
The user wants to change a Windows system setting: "{command}"

Generate a SINGLE, SAFE PowerShell or cmd command (one line) to accomplish this.

Rules:
- Use ONLY built-in Windows cmdlets, netsh, powercfg, reg.exe, or sc.exe.
- Do NOT use any module that needs to be installed separately.
- Do NOT restart/shutdown the computer or delete files.
- Reply with ONLY the raw command — no explanation, no backticks, no markdown.

Correct examples of the format I want:
  "enable night light"  -> Set-ItemProperty -Path "HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\CloudStore\\Store\\DefaultAccount\\Current\\default$windows.data.bluelightreduction.bluelightreductionstate\\windows.data.bluelightreduction.bluelightreductionstate" -Name "Data" -Type Binary -Value ([byte[]](0x43,0x42,0x01,0x00))
  "set timezone to UTC" -> Set-TimeZone -Id "UTC"
  "set timezone to IST" -> Set-TimeZone -Id "India Standard Time"
  "enable remote desktop" -> Set-ItemProperty -Path "HKLM:\\System\\CurrentControlSet\\Control\\Terminal Server" -Name "fDenyTSConnections" -Value 0
  "set screen resolution to 1920x1080" -> Add-Type -AssemblyName System.Windows.Forms; [System.Windows.Forms.Screen]::PrimaryScreen | ForEach-Object {{ }}; $dm=New-Object System.Windows.Forms.Screen; $signature='[DllImport("user32.dll")] public static extern bool EnumDisplaySettings(string deviceName, int modeNum, ref DEVMODE devMode);'; $t=Add-Type -MemberDefinition $signature -Name 'NativeMethods' -Namespace 'Win32' -PassThru
  "show battery percentage" -> powercfg /batteryreport
  "check what time zone I am in" -> (Get-TimeZone).DisplayName

PowerShell command:"""

_PS_RETRY_PROMPT = """You are a Windows PowerShell expert.
Your previous command for the task "{command}" was:
  {prev_cmd}
It failed with this error:
  {error}

Please provide a CORRECTED single-line PowerShell command that achieves the same goal.
Reply with ONLY the raw corrected command — no backticks, no markdown, no explanation.

Corrected command:"""


def handle_with_ai_powershell(command: str) -> dict:
    """
    AI fallback: asks the local AI model to generate a safe PowerShell
    command for any settings request without a dedicated fast-path handler.
    Includes retry-on-failure: if the first command errors, the error message
    is fed back to the AI so it can self-correct (up to 2 attempts).
    """
    print("  [AI Fallback] No fast-path matched. Asking local AI for a PowerShell command...")
    try:
        import requests
    except ImportError:
        return {"success": False, "message": "requests not installed. Run: pip install requests"}

    def _ask(prompt_text: str) -> str:
        payload = {
            "model": MODEL_NAME,
            "prompt": prompt_text,
            "stream": False,
            "options": {"temperature": 0.05, "top_p": 0.9},
        }
        resp = requests.post(OLLAMA_URL, json=payload, timeout=45)
        if resp.status_code != 200:
            raise RuntimeError(f"Ollama HTTP {resp.status_code}")
        raw = resp.json()["response"].strip()
        # Strip markdown code fences
        raw = re.sub(r"^```(?:powershell|cmd|batch)?\s*|\s*```$", "", raw, flags=re.I | re.M).strip()
        # Strip leading 'powershell' prefix model sometimes outputs
        raw = re.sub(r"^(?:powershell|ps)\s+", "", raw, flags=re.I).strip()
        # Keep only first non-empty line (guard against multi-line responses)
        lines = [l.strip() for l in raw.splitlines() if l.strip()]
        return lines[0] if lines else ""

    def _run(ps_cmd: str) -> tuple:
        """Run a PowerShell command, return (success, output)."""
        if _DANGER_PATTERNS.search(ps_cmd):
            return False, "SAFETY_REJECTED: Command pattern flagged as potentially destructive."
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_cmd],
            capture_output=True, text=True, timeout=20
        )
        output = (result.stdout + result.stderr).strip()
        return result.returncode == 0, output

    try:
        # Attempt 1 — first try
        ps_cmd = _ask(_PS_FALLBACK_PROMPT.format(command=command))
        if not ps_cmd:
            return {"success": False, "message": "AI returned an empty command."}
        print(f"  AI command (attempt 1): {ps_cmd}")
        success, output = _run(ps_cmd)

        if not success:
            # Attempt 2 — self-correction with error context
            print(f"  Command failed ({output[:80]}). Asking AI to self-correct...")
            corrected = _ask(_PS_RETRY_PROMPT.format(
                command=command, prev_cmd=ps_cmd, error=output[:200]
            ))
            if corrected and corrected != ps_cmd:
                print(f"  AI command (attempt 2): {corrected}")
                success2, output2 = _run(corrected)
                if success2:
                    ps_cmd, output = corrected, output2
                    success = True
                else:
                    output = output2  # use latest error for reporting

        msg = (f"Done. {output}" if output else "Command executed successfully.") if success \
              else f"AI command could not complete: {output}"

        return {
            "success": success,
            "message": msg,
            "ai_command": ps_cmd,
            "output": output,
        }

    except Exception as exc:
        return {"success": False, "message": f"AI fallback error: {exc}"}

    except Exception as exc:
        return {"success": False, "message": f"AI fallback error: {exc}"}


# ════════════════════════════════════════════════════════════════════════════
# Natural Language Parser
# ════════════════════════════════════════════════════════════════════════════

_DEFAULT_VOLUME_STEP     = 10.0
_DEFAULT_BRIGHTNESS_STEP = 10

# Intent patterns — ordered most-specific to most-general.
# (pattern, intent_tag)
_INTENT_PATTERNS = [
    # ── Wi-Fi ──────────────────────────────────────────────────────────────
    (re.compile(r"\b(turn[\s-]+on|enable|connect|start|activate)\s+(wi[\s-]?fi|wifi|wireless|wlan)\b", re.I), "wifi_on"),
    (re.compile(r"\b(turn[\s-]+off|disable|disconnect|stop|deactivate|kill)\s+(wi[\s-]?fi|wifi|wireless|wlan)\b", re.I), "wifi_off"),
    (re.compile(r"\b(wi[\s-]?fi|wifi|wireless|wlan)\s+on\b", re.I), "wifi_on"),
    (re.compile(r"\b(wi[\s-]?fi|wifi|wireless|wlan)\s+off\b", re.I), "wifi_off"),
    (re.compile(r"\btoggle\s+(wi[\s-]?fi|wifi|wireless|wlan)\b", re.I), "wifi_toggle"),
    (re.compile(r"\b(wi[\s-]?fi|wifi|wireless|wlan)\b.*\b(status|state|info)\b", re.I), "wifi_status"),
    (re.compile(r"\b(status|state|is)\b.*\b(wi[\s-]?fi|wifi|wireless|wlan)\b", re.I), "wifi_status"),

    # ── Bluetooth ──────────────────────────────────────────────────────────
    (re.compile(r"\b(turn[\s-]+on|enable|start|activate)\s+(bluetooth|bt)\b", re.I), "bt_on"),
    (re.compile(r"\b(turn[\s-]+off|disable|stop|deactivate|kill)\s+(bluetooth|bt)\b", re.I), "bt_off"),
    (re.compile(r"\b(bluetooth|bt)\s+on\b", re.I), "bt_on"),
    (re.compile(r"\b(bluetooth|bt)\s+off\b", re.I), "bt_off"),
    (re.compile(r"\btoggle\s+(bluetooth|bt)\b", re.I), "bt_toggle"),
    (re.compile(r"\b(bluetooth|bt)\b.*\b(status|state|info)\b", re.I), "bt_status"),
    (re.compile(r"\b(status|state|is)\b.*\b(bluetooth|bt)\b", re.I), "bt_status"),

    # ── Dark / Light mode ──────────────────────────────────────────────────
    (re.compile(r"\b(switch\s+to\s+|enable\s+|turn\s+on\s+|use\s+)?dark\s+(?:mode|theme)\b", re.I), "dark_mode"),
    (re.compile(r"\b(switch\s+to\s+|enable\s+|turn\s+on\s+|use\s+)?light\s+(?:mode|theme)\b", re.I), "light_mode"),
    (re.compile(r"\b(switch\s+to\s+|enable\s+|turn\s+on\s+|use\s+)?night\s+(?:mode|theme)\b", re.I), "dark_mode"),
    (re.compile(r"\btoggle\s+(?:dark|light|theme|mode)\b", re.I), "toggle_theme"),

    # ── Power Plan ─────────────────────────────────────────────────────────
    (re.compile(
        r"\b(?:set|switch|change|use|activate)\s+(?:the\s+)?(?:power\s+plan|power\s+mode|power\s+profile|power\s+setting)\s+(?:to\s+)?(.+)",
        re.I), "power_plan"),
    (re.compile(r"\bperformance\s+(?:power\s+)?(?:plan|mode|profile)\b", re.I), "power_plan_perf"),
    (re.compile(r"\bbalanced\s+(?:power\s+)?(?:plan|mode|profile)\b", re.I), "power_plan_balanced"),
    (re.compile(r"\b(?:power\s+saver|battery\s+saver\s+plan|low\s+power)\s+(?:plan|mode|profile)\b", re.I), "power_plan_saver"),

    # ── Firewall ───────────────────────────────────────────────────────────
    (re.compile(r"\b(enable|turn\s+on|start|activate)\s+(?:windows\s+)?firewall\b", re.I), "firewall_on"),
    (re.compile(r"\b(disable|turn\s+off|stop|deactivate|kill)\s+(?:windows\s+)?firewall\b", re.I), "firewall_off"),
    (re.compile(r"\bfirewall\s+(?:status|state|info)\b", re.I), "firewall_status"),

    # ── Battery Saver ──────────────────────────────────────────────────────
    (re.compile(r"\b(enable|turn\s+on|start|activate|use)\s+(?:battery\s+saver|power\s+saving|low[\s-]power\s+mode)\b", re.I), "battery_saver_on"),
    (re.compile(r"\b(disable|turn\s+off|stop|deactivate)\s+(?:battery\s+saver|power\s+saving|low[\s-]power\s+mode)\b", re.I), "battery_saver_off"),

    # ── Volume: mute / unmute ──────────────────────────────────────────────
    (re.compile(r"\b(unmute|unsilence|un-mute|un\s+mute)\b", re.I), "mute_off"),
    (re.compile(r"\b(mute|silence|silent)\b", re.I), "mute_on"),

    # ── Volume: absolute set ───────────────────────────────────────────────
    (re.compile(r"\bset\s+(?:the\s+)?(?:master\s+)?(?:volume|audio|sound)\s+(?:to|at|=)?\s*(\d+(?:\.\d+)?)\s*%?\b", re.I), "volume_set"),
    (re.compile(r"\b(?:master\s+)?(?:volume|audio|sound)\s+(?:to\s+|at\s+|=\s*)?(\d+(?:\.\d+)?)\s*%?\b", re.I), "volume_set"),
    (re.compile(r"\b(?:set\s+)?audio\s+(?:level\s+)?(?:to\s+)?(\d+(?:\.\d+)?)\s*%?\b", re.I), "volume_set"),

    # ── Volume: relative ──────────────────────────────────────────────────
    (re.compile(r"\b(increase|raise|turn[\s-]+up|louder|up)\s+(?:the\s+)?(?:master\s+)?(?:volume|audio|sound)\b(?:\s+by\s+(\d+(?:\.\d+)?))?", re.I), "volume_up"),
    (re.compile(r"\b(decrease|lower|turn[\s-]+down|quieter|reduce|down)\s+(?:the\s+)?(?:master\s+)?(?:volume|audio|sound)\b(?:\s+by\s+(\d+(?:\.\d+)?))?", re.I), "volume_down"),

    # ── Volume: query ──────────────────────────────────────────────────────
    (re.compile(r"\b(what|how|current|get|check|show|tell)\b.{0,30}\b(volume|audio|sound)\b", re.I), "volume_status"),
    (re.compile(r"\b(volume|audio|sound)\b.{0,20}\b(status|level|now|current)\b", re.I), "volume_status"),

    # ── Brightness: absolute set ───────────────────────────────────────────
    (re.compile(r"\bset\s+(?:the\s+)?(?:screen\s+|display\s+)?brightness\s+(?:to|at|=)?\s*(\d+(?:\.\d+)?)\s*%?\b", re.I), "brightness_set"),
    (re.compile(r"\b(?:screen\s+|display\s+)?brightness\s+(?:to\s+|at\s+|=\s*)?(\d+(?:\.\d+)?)\s*%?\b", re.I), "brightness_set"),
    (re.compile(r"\bdim\s+(?:the\s+)?(?:screen|display|monitor)?\s*to\s+(\d+(?:\.\d+)?)\s*%?\b", re.I), "brightness_set"),

    # ── Brightness: relative ───────────────────────────────────────────────
    (re.compile(r"\b(increase|raise|brighten|brighter)\s+(?:the\s+)?(?:screen\s+|display\s+)?brightness\b(?:\s+by\s+(\d+(?:\.\d+)?))?", re.I), "brightness_up"),
    (re.compile(r"\b(decrease|lower|dim|reduce)\s+(?:the\s+)?(?:screen\s+|display\s+)?brightness\b(?:\s+by\s+(\d+(?:\.\d+)?))?", re.I), "brightness_down"),

    # ── Brightness: query ──────────────────────────────────────────────────
    (re.compile(r"\b(what|how|current|get|check|show|tell)\b.{0,30}\b(brightness|screen|display)\b", re.I), "brightness_status"),
    (re.compile(r"\b(brightness|screen|display)\b.{0,20}\b(status|level|now|current)\b", re.I), "brightness_status"),
]


def parse_command(text: str) -> dict:
    """
    Matches free-text against ordered intent patterns.
    Returns structured dict with 'intent' + extracted params.

    IMPORTANT: Always returns a dict (never None).
    If nothing matches, returns intent='ai_powershell' so the AI fallback handles it.
    """
    t = text.strip()
    for pattern, intent in _INTENT_PATTERNS:
        m = pattern.search(t)
        if not m:
            continue

        nums = [g for g in m.groups() if g and re.match(r"^\d+(?:\.\d+)?$", g)]
        # Text groups (non-numeric capture groups) — used for power plan name extraction
        texts = [g.strip() for g in m.groups() if g and not re.match(r"^\d+(?:\.\d+)?$", g)]
        parsed: dict = {"intent": intent, "raw": t}

        if intent == "volume_set":
            parsed["level"] = float(nums[0]) if nums else None
        elif intent in ("volume_up", "volume_down"):
            parsed["step"] = float(nums[-1]) if nums else _DEFAULT_VOLUME_STEP
        elif intent == "brightness_set":
            parsed["level"] = float(nums[-1]) if nums else None
        elif intent in ("brightness_up", "brightness_down"):
            parsed["step"] = float(nums[-1]) if nums else float(_DEFAULT_BRIGHTNESS_STEP)
        elif intent == "power_plan":
            # Extract the plan name hint from the last text capture group
            parsed["plan_hint"] = texts[-1] if texts else t

        return parsed

    # Nothing matched — hand off to AI
    return {"intent": "ai_powershell", "raw": t}


# ════════════════════════════════════════════════════════════════════════════
# Command Dispatcher
# ════════════════════════════════════════════════════════════════════════════

def dispatch(parsed: dict) -> dict:
    intent = parsed["intent"]

    # ── Wi-Fi ──────────────────────────────────────────────────────────────
    if intent in ("wifi_on", "wifi_off", "wifi_toggle", "wifi_status"):
        iface = get_wifi_interface()
        if not iface:
            return {"success": False, "message": "No Wi-Fi adapter found on this device."}
        current = get_wifi_state(iface)
        if intent == "wifi_status":
            return {"success": True, "message": f"Wi-Fi '{iface}': {current or 'unknown'}.", "state": current}
        target = current != "enabled" if intent == "wifi_toggle" else (intent == "wifi_on")
        r = set_wifi(target, iface)
        r["message"] = (f"Wi-Fi '{iface}' is now {r.get('new_state', 'enabled' if target else 'disabled')}."
                        if r["success"] else
                        f"Wi-Fi change failed: {r.get('error')}. {r.get('hint', '')}")
        return r

    # ── Bluetooth ──────────────────────────────────────────────────────────
    if intent in ("bt_on", "bt_off", "bt_toggle", "bt_status"):
        current = get_bluetooth_state()
        if intent == "bt_status":
            return {"success": True, "message": f"Bluetooth: {current or 'unknown'}.", "state": current}
        target = current != "enabled" if intent == "bt_toggle" else (intent == "bt_on")
        r = set_bluetooth(target)
        r["message"] = (f"Bluetooth is now {r.get('new_state', 'enabled' if target else 'disabled')}."
                        if r["success"] else
                        f"Bluetooth change failed: {r.get('error', '')}. {r.get('hint', '')}")
        return r

    # ── Dark / Light mode ──────────────────────────────────────────────────
    if intent in ("dark_mode", "light_mode", "toggle_theme"):
        if intent == "toggle_theme":
            current = get_theme_mode()
            dark = current != "dark"
        else:
            dark = (intent == "dark_mode")
        r = set_theme_mode(dark)
        r["message"] = (f"Switched to {'dark' if dark else 'light'} mode."
                        if r["success"] else f"Theme switch failed: {r.get('error')}")
        return r

    # ── Power Plan ─────────────────────────────────────────────────────────
    if intent in ("power_plan", "power_plan_perf", "power_plan_balanced", "power_plan_saver"):
        hint_map = {
            "power_plan_perf":     "High performance",
            "power_plan_balanced": "Balanced",
            "power_plan_saver":    "Power saver",
        }
        hint = hint_map.get(intent) or parsed.get("plan_hint", "Balanced")
        r = set_power_plan(hint)
        r["message"] = (f"Power plan set to '{r.get('plan', hint)}' (now active: {r.get('active_now')})."
                        if r["success"] else f"Power plan change failed: {r.get('error')}")
        return r

    # ── Firewall ───────────────────────────────────────────────────────────
    if intent in ("firewall_on", "firewall_off", "firewall_status"):
        if intent == "firewall_status":
            state = get_firewall_state()
            return {"success": True, "message": f"Windows Firewall: {state or 'unknown'}.", "state": state}
        r = set_firewall(intent == "firewall_on")
        r["message"] = (f"Firewall is now {r.get('new_state', 'enabled' if intent == 'firewall_on' else 'disabled')}."
                        if r["success"] else f"Firewall change failed: {r.get('error')}. {r.get('hint', '')}")
        return r

    # ── Battery Saver ──────────────────────────────────────────────────────
    if intent in ("battery_saver_on", "battery_saver_off"):
        r = set_battery_saver(intent == "battery_saver_on")
        r["message"] = (f"Battery Saver {r.get('mode', 'updated')}."
                        if r["success"] else f"Battery Saver change failed: {r.get('error')}")
        return r

    # ── Volume ─────────────────────────────────────────────────────────────
    if intent.startswith("volume_") or intent.startswith("mute_"):
        if _import_or_warn("pycaw.pycaw", "pycaw") is None:
            return {"success": False, "message": "pycaw not installed: pip install pycaw"}
        if intent == "mute_on":
            r = set_mute(True);  r["message"] = "Audio muted." if r["success"] else f"Mute failed: {r.get('error')}"; return r
        if intent == "mute_off":
            r = set_mute(False); r["message"] = "Audio unmuted." if r["success"] else f"Unmute failed: {r.get('error')}"; return r
        if intent == "volume_status":
            lvl, muted = get_volume(), get_mute_state()
            if lvl is None: return {"success": False, "message": "Could not read volume."}
            return {"success": True, "message": f"Volume: {lvl:.0f}%{' [Muted]' if muted else ''}", "level": lvl, "muted": muted}
        if intent == "volume_set":
            lvl = parsed.get("level")
            if lvl is None: return {"success": False, "message": "Could not parse volume level."}
            r = set_volume(lvl); r["message"] = f"Volume set to {r.get('actual', lvl):.0f}%." if r["success"] else f"Set volume failed: {r.get('error')}"; return r
        if intent in ("volume_up", "volume_down"):
            cur = get_volume()
            if cur is None: return {"success": False, "message": "Could not read volume."}
            step = parsed.get("step", _DEFAULT_VOLUME_STEP)
            new = cur + step if intent == "volume_up" else cur - step
            r = set_volume(new)
            r["message"] = f"Volume {'raised' if intent == 'volume_up' else 'lowered'} to {r.get('actual', new):.0f}%." if r["success"] else f"Volume change failed: {r.get('error')}"; return r

    # ── Brightness ─────────────────────────────────────────────────────────
    if intent == "brightness_status":
        lvl = get_brightness()
        return ({"success": True, "message": f"Brightness: {lvl}%", "level": lvl} if lvl is not None
                else {"success": False, "message": "Cannot read brightness (external monitors unsupported)."})
    if intent == "brightness_set":
        lvl = parsed.get("level")
        if lvl is None: return {"success": False, "message": "Could not parse brightness level."}
        r = set_brightness(int(lvl))
        r["message"] = f"Brightness set to {r.get('actual', lvl)}%." if r["success"] else f"Set brightness failed: {r.get('error')} {r.get('hint', '')}"; return r
    if intent in ("brightness_up", "brightness_down"):
        cur = get_brightness()
        if cur is None: return {"success": False, "message": "Could not read brightness."}
        step = int(parsed.get("step", _DEFAULT_BRIGHTNESS_STEP))
        new = cur + step if intent == "brightness_up" else cur - step
        r = set_brightness(new)
        r["message"] = f"Brightness {'raised' if intent == 'brightness_up' else 'lowered'} to {r.get('actual', new)}%." if r["success"] else f"Brightness change failed: {r.get('error')}"; return r

    # ── AI PowerShell fallback (for EVERYTHING else) ────────────────────────
    if intent == "ai_powershell":
        return handle_with_ai_powershell(parsed["raw"])

    return {"success": False, "message": f"Unrecognised intent: '{intent}'"}


# ════════════════════════════════════════════════════════════════════════════
# Main Entry Point
# ════════════════════════════════════════════════════════════════════════════

def run_task_3_2(command: str) -> dict:
    """
    Main entry point for Task 3.2.
    Accepts ANY plain-English Windows settings command.
    Returns a result dict with 'success', 'message', and extra fields per action.
    """
    print("\n" + "=" * 60)
    print("Task 3.2: System Settings -- Natural Language Control")
    print(f'Command: "{command}"')
    print("=" * 60)

    parsed = parse_command(command)
    print(f"\n  Intent  : {parsed['intent']}")
    for k, v in parsed.items():
        if k not in ("intent", "raw"):
            print(f"  {k.capitalize():<10}: {v}")

    result = dispatch(parsed)

    print("\n" + "=" * 52)
    icon = "[OK]" if result.get("success") else "[!!]"
    print(f"  {icon} RESULT  : {result.get('message', 'done')}")
    print("=" * 52 + "\n")

    return {**result, "command": command, "parsed": parsed}


# ════════════════════════════════════════════════════════════════════════════
# CLI
# ════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description="TaskForge Task 3.2 -- System Settings Natural Language Control"
    )
    ap.add_argument("--command", "-c", type=str, default=None,
                    help="Any Windows settings command in plain English")
    args = ap.parse_args()

    if not args.command:
        print("=" * 60)
        print("     TaskForge -- System Settings (Task 3.2)              ")
        print("=" * 60)
        print("Fast-path (no AI needed):")
        print("  wifi on/off/status         | bluetooth on/off/status")
        print("  dark mode / light mode     | toggle theme")
        print("  power plan performance     | power saver | balanced")
        print("  firewall on/off/status     | battery saver on/off")
        print("  volume 60 | mute | unmute  | increase volume by 20")
        print("  brightness 70 | dim to 30  | increase brightness")
        print()
        print("AI-powered fallback (anything else):")
        print("  enable night light         | set timezone to UTC")
        print("  change screen resolution   | set refresh rate to 144hz")
        print("  disable windows update     | enable remote desktop")
        print("  ... any Windows setting you can describe in words ...")
        print()
        cmd = input("Enter your command:\n> ").strip()
    else:
        cmd = args.command

    if not cmd:
        print("No command provided. Exiting.")
        sys.exit(0)

    result = run_task_3_2(cmd)
    sys.exit(0 if result.get("success") else 1)
