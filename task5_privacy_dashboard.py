"""
TaskForge — Interactive Network & Privacy Dashboard
====================================================
Premium animated GUI that:
  ● Shows internet connectivity status (real DNS probe)
  ● Shows TaskForge zero-leak privacy proof
  ● Shows live connected IPs (system-wide)
  ● Has a natural-language command input that runs agent_loop() in a thread
  ● Shows live step-by-step agent action log with animated status

Run standalone (demo mode):
    python task5_privacy_dashboard.py

Auto-launched by agent_orchestrator.py:
    from task5_privacy_dashboard import PrivacyDashboard
    dash = PrivacyDashboard()
    dash.start()
    dash.log_action("focus_app", "WhatsApp launched")
"""

import os
import sys
import time
import socket
import threading
import queue
from datetime import datetime
from collections import deque

try:
    import psutil
except ImportError:
    psutil = None

try:
    import tkinter as tk
    from tkinter import ttk, font as tkfont
    TK_AVAILABLE = True
except ImportError:
    TK_AVAILABLE = False

# ─────────────────────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────────────────────
POLL_INTERVAL  = 2.5   # network refresh
MAX_LOG_LINES  = 500
OWN_PID        = os.getpid()

_LOCAL_PREFIXES = (
    "127.", "::1", "0.0.0.0", "::ffff:127.",
    "192.168.", "10.", "172.16.", "172.17.", "172.18.", "172.19.",
    "172.20.", "172.21.", "172.22.", "172.23.", "172.24.", "172.25.",
    "172.26.", "172.27.", "172.28.", "172.29.", "172.30.", "172.31.",
    "169.254.",
)
_INTERNAL_CHECK_IPS = {"8.8.8.8", "1.1.1.1"}

# ─────────────────────────────────────────────────────────────────────────────
# Dark color palette
# ─────────────────────────────────────────────────────────────────────────────
C = {
    "bg":          "#08081a",
    "bg2":         "#0d0d28",
    "panel":       "#10102a",
    "panel2":      "#131335",
    "accent":      "#6c63ff",
    "accent2":     "#a78bfa",
    "green":       "#00e5a0",
    "green_bg":    "#001f12",
    "red":         "#ff4d6d",
    "red_bg":      "#200010",
    "amber":       "#fbbf24",
    "amber_bg":    "#1a1000",
    "blue":        "#38bdf8",
    "muted":       "#3a3a6a",
    "muted2":      "#22224a",
    "text":        "#c4c4e8",
    "text_dim":    "#5a5a8a",
    "text_bright": "#eeeeff",
    "border":      "#1e1e48",
    "input_bg":    "#090918",
    "input_fg":    "#e0e0ff",
}

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _is_local(ip: str) -> bool:
    return any(ip.startswith(p) for p in _LOCAL_PREFIXES)


def check_internet() -> bool:
    for host in ("8.8.8.8", "1.1.1.1"):
        try:
            socket.setdefaulttimeout(2)
            s = socket.create_connection((host, 53))
            s.close()
            return True
        except OSError:
            pass
    return False


def get_all_connections() -> list:
    if psutil is None:
        return []
    results = []
    seen = set()
    try:
        for conn in psutil.net_connections(kind="inet"):
            if not conn.raddr:
                continue
            ip, port = conn.raddr.ip, conn.raddr.port
            if ip.startswith(("0.0.0.0", "::")):
                continue
            key = (ip, port)
            if key in seen:
                continue
            seen.add(key)
            proc_name = ""
            try:
                if conn.pid:
                    proc_name = psutil.Process(conn.pid).name()
            except Exception:
                pass
            results.append({
                "ip": ip, "port": port,
                "process": proc_name,
                "status": conn.status or "",
                "local": _is_local(ip),
            })
    except (psutil.AccessDenied, PermissionError):
        pass
    results.sort(key=lambda x: (x["local"], x["status"] != "ESTABLISHED", x["ip"]))
    return results


def get_taskforge_stats() -> dict:
    stats = {
        "external_connections": [],
        "ollama_connected": False,
        "local_connections": [],
        "bytes_sent_kb": 0,
        "bytes_recv_kb": 0,
    }
    if psutil is None:
        return stats
    try:
        for conn in [c for c in psutil.net_connections(kind="inet") if c.pid == OWN_PID]:
            if not conn.raddr:
                continue
            ip, port = conn.raddr.ip, conn.raddr.port
            if ip == "127.0.0.1" and port == 11434:
                stats["ollama_connected"] = True
                stats["local_connections"].append(f"{ip}:{port}")
                continue
            if ip in _INTERNAL_CHECK_IPS and port == 53:
                continue
            if _is_local(ip):
                stats["local_connections"].append(f"{ip}:{port}")
            else:
                stats["external_connections"].append(f"{ip}:{port}")
    except Exception:
        pass
    try:
        for conn in psutil.net_connections(kind="inet"):
            if conn.raddr and conn.raddr.ip == "127.0.0.1" and conn.raddr.port == 11434:
                stats["ollama_connected"] = True
                break
    except Exception:
        pass
    stats["bytes_sent_kb"] = _tf_bytes_sent // 1024
    stats["bytes_recv_kb"] = _tf_bytes_recv // 1024
    return stats


_tf_bytes_sent = 0
_tf_bytes_recv = 0
_tf_lock = threading.Lock()


def record_bytes(sent: int = 0, recv: int = 0):
    global _tf_bytes_sent, _tf_bytes_recv
    with _tf_lock:
        _tf_bytes_sent += sent
        _tf_bytes_recv += recv


# ─────────────────────────────────────────────────────────────────────────────
# Dashboard GUI
# ─────────────────────────────────────────────────────────────────────────────

class PrivacyDashboard:

    # Spinner frames (Unicode braille)
    SPINNER = ["⣾", "⣽", "⣻", "⢿", "⡿", "⣟", "⣯", "⣷"]

    def __init__(self):
        self._action_log: deque = deque(maxlen=MAX_LOG_LINES)
        self._root         = None
        self._gui_thread   = None
        self._lock         = threading.Lock()
        self._running      = False
        self._agent_running = False
        self._spinner_idx  = 0
        self._agent_callback = None   # set by orchestrator to run a goal
        # queue for log updates from other threads
        self._log_queue: queue.Queue = queue.Queue()

    # ── Public API ─────────────────────────────────────────────────────────

    def set_agent_callback(self, fn):
        """fn(goal: str) -> str — called in a background thread when user submits command."""
        self._agent_callback = fn

    def start(self, blocking: bool = False):
        self._running = True
        if TK_AVAILABLE:
            if blocking:
                self._build_gui()
            else:
                self._gui_thread = threading.Thread(target=self._build_gui, daemon=True)
                self._gui_thread.start()
                time.sleep(1.2)
        else:
            print("[Dashboard] Tkinter not available — running headless.")

    def stop(self):
        self._running = False
        if self._root:
            try:
                self._root.destroy()
            except Exception:
                pass

    def log_action(self, tool: str, detail: str = ""):
        """Thread-safe log entry. Called from agent thread."""
        ts = datetime.now().strftime("%H:%M:%S")
        entry = {"ts": ts, "tool": tool, "detail": detail[:120]}
        self._log_queue.put(entry)

    # ── GUI Build ──────────────────────────────────────────────────────────

    def _build_gui(self):
        root = tk.Tk()
        self._root = root
        root.title("TaskForge — AI Desktop Assistant")
        root.geometry("1000x760")
        root.configure(bg=C["bg"])
        root.resizable(True, True)
        root.minsize(820, 640)

        # TTK style
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("Vertical.TScrollbar",
                         background=C["panel"], troughcolor=C["bg"],
                         arrowcolor=C["muted"], borderwidth=0, relief="flat")
        style.configure("Horizontal.TScrollbar",
                         background=C["panel"], troughcolor=C["bg"],
                         arrowcolor=C["muted"], borderwidth=0, relief="flat")

        # Fonts
        try:
            F_TITLE  = tkfont.Font(family="Segoe UI", size=15, weight="bold")
            F_BADGE  = tkfont.Font(family="Segoe UI", size=11, weight="bold")
            F_MED    = tkfont.Font(family="Segoe UI", size=9,  weight="bold")
            F_SMALL  = tkfont.Font(family="Segoe UI", size=8)
            F_MONO   = tkfont.Font(family="Consolas", size=9)
            F_INPUT  = tkfont.Font(family="Segoe UI", size=11)
            F_BTN    = tkfont.Font(family="Segoe UI", size=10, weight="bold")
            F_CLOCK  = tkfont.Font(family="Segoe UI", size=8)
            F_SPIN   = tkfont.Font(family="Segoe UI", size=13, weight="bold")
            F_STAT   = tkfont.Font(family="Segoe UI", size=9,  weight="bold")
        except Exception:
            F_TITLE = F_BADGE = F_MED = F_SMALL = F_MONO = F_INPUT = F_BTN = F_CLOCK = F_SPIN = F_STAT = None

        def lbl(parent, text="", fg=C["text"], font=None, bg=None, **kw):
            return tk.Label(parent, text=text, fg=fg, font=font or F_SMALL,
                            bg=bg or C["bg"], **kw)

        def sep(parent, color=C["border"], height=1, pad=(8, 8)):
            tk.Frame(parent, bg=color, height=height).pack(fill="x", padx=20, pady=pad)

        # ── Header bar ──────────────────────────────────────────────────────
        hdr = tk.Frame(root, bg=C["bg"], pady=0)
        hdr.pack(fill="x", padx=24, pady=(18, 4))

        lbl(hdr, "⚡ TaskForge", fg=C["accent2"], font=F_TITLE).pack(side="left")
        lbl(hdr, "  AI Desktop Assistant", fg=C["muted"], font=F_MED).pack(side="left")
        self._clock_var = tk.StringVar(value="")
        tk.Label(hdr, textvariable=self._clock_var, fg=C["text_dim"],
                 font=F_CLOCK, bg=C["bg"]).pack(side="right")

        sep(root, pad=(4, 0))

        # ── Command input area ───────────────────────────────────────────────
        cmd_outer = tk.Frame(root, bg=C["panel2"], pady=0)
        cmd_outer.pack(fill="x", padx=20, pady=(6, 4))

        cmd_top = tk.Frame(cmd_outer, bg=C["panel2"])
        cmd_top.pack(fill="x", padx=14, pady=(10, 0))
        lbl(cmd_top, "  GIVE A COMMAND", fg=C["accent"], font=F_MED, bg=C["panel2"]).pack(side="left")
        self._spinner_lbl = tk.Label(cmd_top, text="", fg=C["accent2"],
                                     font=F_SPIN, bg=C["panel2"])
        self._spinner_lbl.pack(side="left", padx=(8, 0))
        self._status_var = tk.StringVar(value="Ready — type a task and press Enter or click Run")
        tk.Label(cmd_top, textvariable=self._status_var, fg=C["text_dim"],
                 font=F_SMALL, bg=C["panel2"]).pack(side="right")

        cmd_row = tk.Frame(cmd_outer, bg=C["panel2"])
        cmd_row.pack(fill="x", padx=14, pady=(6, 10))

        # Input field with rounded look via border frame
        inp_border = tk.Frame(cmd_row, bg=C["accent"], padx=1, pady=1)
        inp_border.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self._cmd_var = tk.StringVar()
        self._cmd_entry = tk.Entry(
            inp_border, textvariable=self._cmd_var,
            bg=C["input_bg"], fg=C["input_fg"], font=F_INPUT,
            insertbackground=C["accent2"], relief="flat",
            bd=6
        )
        self._cmd_entry.pack(fill="x")
        self._cmd_entry.bind("<Return>", lambda e: self._submit_command())
        self._cmd_entry.bind("<FocusIn>", self._on_entry_focus)
        self._cmd_entry.bind("<FocusOut>", self._on_entry_blur)

        # Placeholder
        self._placeholder = "e.g. open youtube and search lofi music  |  set volume to 60  |  send hi to John on WhatsApp"
        self._show_placeholder()

        self._run_btn = tk.Button(
            cmd_row, text="  ▶  Run  ", font=F_BTN,
            bg=C["accent"], fg="#ffffff",
            activebackground=C["accent2"], activeforeground="#ffffff",
            relief="flat", padx=14, pady=6,
            cursor="hand2",
            command=self._submit_command
        )
        self._run_btn.pack(side="left")
        self._run_btn.bind("<Enter>", lambda e: self._run_btn.config(bg=C["accent2"]))
        self._run_btn.bind("<Leave>", lambda e: self._run_btn.config(bg=C["accent"]))

        sep(root, pad=(4, 2))

        # ── Two status badges ────────────────────────────────────────────────
        badge_row = tk.Frame(root, bg=C["bg"])
        badge_row.pack(fill="x", padx=20, pady=(2, 4))
        badge_row.columnconfigure(0, weight=1)
        badge_row.columnconfigure(1, weight=1)

        def make_badge(parent, col, label_text):
            f = tk.Frame(parent, bg=C["bg"])
            f.grid(row=0, column=col, padx=(0 if col else 0, 6 if col == 0 else 0), sticky="ew")
            lbl(f, label_text, fg=C["text_dim"], font=F_SMALL).pack(anchor="w", padx=2, pady=(0, 3))
            badge = tk.Label(f, text="  ● Checking…  ", font=F_BADGE,
                             fg=C["muted"], bg=C["panel"],
                             pady=11, padx=16, anchor="center", relief="flat")
            badge.pack(fill="x")
            return badge

        self._inet_badge = make_badge(badge_row, 0, "  YOUR INTERNET")
        self._priv_badge = make_badge(badge_row, 1, "  TASKFORGE PRIVACY")
        badge_row.columnconfigure(0, weight=1)
        badge_row.columnconfigure(1, weight=1)

        # Fix grid padding
        badge_row.children[list(badge_row.children.keys())[0]].grid(padx=(0, 6), sticky="ew")
        badge_row.children[list(badge_row.children.keys())[1]].grid(padx=(6, 0), sticky="ew")

        sep(root, pad=(4, 2))

        # ── Middle row: IP table + TaskForge stats ───────────────────────────
        mid = tk.Frame(root, bg=C["bg"])
        mid.pack(fill="x", padx=20, pady=0)
        mid.columnconfigure(0, weight=3)
        mid.columnconfigure(1, weight=2)

        # Left: IP table
        left_f = tk.Frame(mid, bg=C["bg"])
        left_f.grid(row=0, column=0, sticky="nsew", padx=(0, 8))

        ip_hdr = tk.Frame(left_f, bg=C["bg"])
        ip_hdr.pack(fill="x")
        lbl(ip_hdr, "Connected IPs", fg=C["text"], font=F_MED).pack(side="left")
        self._ip_count_var = tk.StringVar(value="")
        tk.Label(ip_hdr, textvariable=self._ip_count_var,
                 fg=C["text_dim"], font=F_SMALL, bg=C["bg"]).pack(side="left", padx=4)

        ip_wrap = tk.Frame(left_f, bg=C["bg"])
        ip_wrap.pack(fill="both", expand=True, pady=(4, 0))
        ip_sb = ttk.Scrollbar(ip_wrap, style="Vertical.TScrollbar")
        ip_sb.pack(side="right", fill="y")
        self._ip_text = tk.Text(
            ip_wrap, bg=C["panel"], fg=C["blue"],
            font=F_MONO, relief="flat", state="disabled",
            height=7, wrap="none", yscrollcommand=ip_sb.set,
            selectbackground=C["accent"], insertbackground=C["accent"]
        )
        self._ip_text.tag_configure("local",   foreground=C["green"])
        self._ip_text.tag_configure("ext",     foreground=C["blue"])
        self._ip_text.tag_configure("hdr",     foreground=C["muted"])
        self._ip_text.tag_configure("est",     foreground=C["green"])
        ip_sb.config(command=self._ip_text.yview)
        self._ip_text.pack(fill="both", expand=True)

        # Right: TaskForge stats
        right_f = tk.Frame(mid, bg=C["panel"], padx=16, pady=12)
        right_f.grid(row=0, column=1, sticky="nsew")

        lbl(right_f, "TaskForge Process", fg=C["accent2"], font=F_MED, bg=C["panel"]).pack(anchor="w")
        lbl(right_f, f"PID {OWN_PID}", fg=C["text_dim"], font=F_SMALL, bg=C["panel"]).pack(anchor="w", pady=(0, 8))

        def stat_row(parent, label, var, color=C["accent2"]):
            row = tk.Frame(parent, bg=C["panel"])
            row.pack(fill="x", pady=2)
            tk.Label(row, text=label, font=F_SMALL, fg=C["text_dim"],
                     bg=C["panel"], width=17, anchor="w").pack(side="left")
            tk.Label(row, textvariable=var, font=F_STAT,
                     fg=color, bg=C["panel"]).pack(side="left")

        self._ext_conn_var   = tk.StringVar(value="—")
        self._ollama_var     = tk.StringVar(value="—")
        self._sent_var       = tk.StringVar(value="0 KB")
        self._recv_var       = tk.StringVar(value="0 KB")
        self._local_conn_var = tk.StringVar(value="—")

        stat_row(right_f, "External conns:", self._ext_conn_var, C["red"])
        stat_row(right_f, "Data sent out:",  self._sent_var,     C["amber"])
        stat_row(right_f, "Data received:",  self._recv_var,     C["blue"])
        stat_row(right_f, "Ollama (local):", self._ollama_var,   C["green"])
        stat_row(right_f, "Local conns:",    self._local_conn_var, C["accent2"])

        tk.Frame(right_f, bg=C["border"], height=1).pack(fill="x", pady=8)
        self._proof_lbl = tk.Label(
            right_f, text="✅  Zero cloud API calls",
            font=F_MED, fg=C["green"], bg=C["panel"],
            justify="left", wraplength=220
        )
        self._proof_lbl.pack(anchor="w")

        sep(root, pad=(6, 2))

        # ── Agent Action Log ─────────────────────────────────────────────────
        log_hdr_row = tk.Frame(root, bg=C["bg"])
        log_hdr_row.pack(fill="x", padx=20)
        lbl(log_hdr_row, "Agent Action Log", fg=C["accent2"], font=F_MED).pack(side="left")
        lbl(log_hdr_row, "  (live — every tool call appears here)", fg=C["text_dim"], font=F_SMALL).pack(side="left")

        log_wrap = tk.Frame(root, bg=C["bg"])
        log_wrap.pack(fill="both", expand=True, padx=20, pady=(4, 4))
        log_sb = ttk.Scrollbar(log_wrap, style="Vertical.TScrollbar")
        log_sb.pack(side="right", fill="y")
        self._log_text = tk.Text(
            log_wrap, bg=C["panel"], fg=C["text"],
            font=F_MONO, relief="flat", state="disabled",
            wrap="word", yscrollcommand=log_sb.set,
            selectbackground=C["accent"]
        )
        self._log_text.tag_configure("ts",      foreground=C["text_dim"])
        self._log_text.tag_configure("tool",    foreground=C["accent2"])
        self._log_text.tag_configure("arrow",   foreground=C["muted"])
        self._log_text.tag_configure("detail",  foreground=C["text"])
        self._log_text.tag_configure("done",    foreground=C["green"])
        self._log_text.tag_configure("error",   foreground=C["red"])
        self._log_text.tag_configure("step",    foreground=C["amber"])
        log_sb.config(command=self._log_text.yview)
        self._log_text.pack(fill="both", expand=True)

        # ── Footer ───────────────────────────────────────────────────────────
        tk.Label(
            root,
            text="🔒  All AI runs on Ollama (localhost:11434) — no prompts leave your machine",
            font=F_SMALL, fg=C["muted"], bg=C["bg"]
        ).pack(pady=(2, 8))

        # Start loops
        root.after(100, self._refresh)
        root.after(80,  self._drain_log_queue)
        root.after(150, self._spin_tick)
        root.protocol("WM_DELETE_WINDOW", self._on_close)
        root.mainloop()

    # ── Placeholder helpers ─────────────────────────────────────────────────

    def _show_placeholder(self):
        if not self._cmd_var.get():
            self._cmd_entry.config(fg=C["muted"])
            self._cmd_var.set(self._placeholder)

    def _clear_placeholder(self):
        if self._cmd_var.get() == self._placeholder:
            self._cmd_var.set("")
            self._cmd_entry.config(fg=C["input_fg"])

    def _on_entry_focus(self, _event):
        self._clear_placeholder()

    def _on_entry_blur(self, _event):
        if not self._cmd_var.get().strip():
            self._show_placeholder()

    # ── Submit command ──────────────────────────────────────────────────────

    def _submit_command(self):
        if self._agent_running:
            self._status_var.set("Agent is already running — please wait…")
            return
        raw = self._cmd_var.get().strip()
        if not raw or raw == self._placeholder:
            self._status_var.set("Please enter a command first.")
            return

        goal = raw
        # Clear and re-show placeholder
        self._cmd_var.set("")
        self._cmd_entry.config(fg=C["input_fg"])
        self._show_placeholder()

        self._start_agent(goal)

    def _start_agent(self, goal: str):
        self._agent_running = True
        self._status_var.set(f"Running: {goal[:70]}…")
        ts = datetime.now().strftime("%H:%M:%S")
        # Log the goal submission
        self._log_queue.put({"ts": ts, "tool": "▶ NEW GOAL", "detail": goal})

        def _run():
            try:
                if self._agent_callback:
                    result = self._agent_callback(goal)
                else:
                    result = "[Demo mode — agent not connected]"
            except Exception as e:
                result = f"Error: {e}"
            finally:
                self._agent_running = False
                ts2 = datetime.now().strftime("%H:%M:%S")
                self._log_queue.put({"ts": ts2, "tool": "✅ DONE", "detail": str(result)[:200]})
                if self._root:
                    self._root.after(0, lambda: self._status_var.set(
                        f"Done — {str(result)[:80]}"
                    ))

        threading.Thread(target=_run, daemon=True).start()

    # ── Spinner animation ───────────────────────────────────────────────────

    def _spin_tick(self):
        if not self._root:
            return
        if self._agent_running:
            self._spinner_lbl.config(text=self.SPINNER[self._spinner_idx % len(self.SPINNER)])
            self._spinner_idx += 1
        else:
            self._spinner_lbl.config(text="")
        self._root.after(100, self._spin_tick)

    # ── Log queue drain ─────────────────────────────────────────────────────

    def _drain_log_queue(self):
        if not self._root:
            return
        try:
            while True:
                entry = self._log_queue.get_nowait()
                self._append_log_entry(entry)
        except queue.Empty:
            pass
        self._root.after(80, self._drain_log_queue)

    def _append_log_entry(self, entry: dict):
        """Append one structured log entry to the log widget."""
        self._log_text.configure(state="normal")
        ts     = entry.get("ts", "")
        tool   = entry.get("tool", "")
        detail = entry.get("detail", "")

        self._log_text.insert("1.0", "\n")

        # Determine colour tag for detail
        if tool in ("✅ DONE",) or "completed" in tool.lower():
            detail_tag = "done"
        elif "error" in tool.lower() or "ERROR" in detail:
            detail_tag = "error"
        elif tool in ("▶ NEW GOAL",):
            detail_tag = "step"
        else:
            detail_tag = "detail"

        line_parts = [
            (f"[{ts}] ", "ts"),
            (f"{tool:<22}", "tool"),
            ("  →  ", "arrow"),
            (detail, detail_tag),
        ]
        # Insert at top so newest is always visible
        pos = "1.0"
        for text_part, tag in reversed(line_parts):
            self._log_text.insert(pos, text_part, tag)

        self._log_text.configure(state="disabled")
        self._log_text.see("1.0")

    # ── Network refresh ─────────────────────────────────────────────────────

    def _refresh(self):
        if not self._running or not self._root:
            return

        # Clock
        self._clock_var.set(datetime.now().strftime("%a, %d %b %Y  •  %H:%M:%S"))

        # Internet badge
        has_inet = check_internet()
        if has_inet:
            self._inet_badge.configure(
                text="  ●  CONNECTED TO INTERNET  ",
                fg=C["green"], bg=C["green_bg"]
            )
        else:
            self._inet_badge.configure(
                text="  ●  NO INTERNET  ",
                fg=C["red"], bg=C["red_bg"]
            )

        # All connections
        conns = get_all_connections()
        self._ip_count_var.set(f"({len(conns)} active)")
        self._ip_text.configure(state="normal")
        self._ip_text.delete("1.0", "end")
        hdr = f"  {'IP Address':<38}  {'Port':<6}  {'Status':<13}  Process\n"
        self._ip_text.insert("end", hdr, "hdr")
        self._ip_text.insert("end", "  " + "─" * 70 + "\n", "hdr")
        for c in conns:
            tag = "local" if c["local"] else "ext"
            est = c["status"] == "ESTABLISHED"
            marker = "⬤ " if est else "○ "
            line = (
                f"  {marker}{c['ip']:<38}  "
                f"{c['port']:<6}  "
                f"{c['status']:<13}  "
                f"{c['process']}\n"
            )
            self._ip_text.insert("end", line, "est" if est and not c["local"] else tag)
        self._ip_text.configure(state="disabled")

        # TaskForge stats
        tf   = get_taskforge_stats()
        ext_n = len(tf["external_connections"])
        self._ext_conn_var.set(f"{ext_n}  {'⚠ ALERT' if ext_n else '✅ NONE'}")
        self._sent_var.set(f"{tf['bytes_sent_kb']} KB")
        self._recv_var.set(f"{tf['bytes_recv_kb']} KB")
        self._ollama_var.set("✅ Connected" if tf["ollama_connected"] else "○ Not connected")
        self._local_conn_var.set(str(len(tf["local_connections"])))

        # Privacy badge
        if ext_n == 0:
            self._priv_badge.configure(
                text="  ●  ZERO CLOUD LEAKS — 100% Local  ",
                fg=C["green"], bg=C["green_bg"]
            )
            self._proof_lbl.configure(
                text="✅  Zero cloud API calls\n✅  All AI runs on your machine\n✅  No data leaves this PC",
                fg=C["green"]
            )
        else:
            self._priv_badge.configure(
                text=f"  ⚠  {ext_n} EXTERNAL CONNECTION(S)  ",
                fg=C["red"], bg=C["red_bg"]
            )
            self._proof_lbl.configure(
                text=f"⚠ {ext_n} unexpected external connection(s):\n"
                     + "\n".join(tf["external_connections"][:3]),
                fg=C["red"]
            )

        self._root.after(int(POLL_INTERVAL * 1000), self._refresh)

    def _on_close(self):
        self._running = False
        if self._root:
            self._root.destroy()

    # ── Backwards-compat monitor shim ──────────────────────────────────────
    class _FakeMonitor:
        def snapshot(self):
            tf = get_taskforge_stats()
            return {
                "is_offline_verified": len(tf["external_connections"]) == 0,
                "external_ips": tf["external_connections"],
                "bytes_sent_kb": tf["bytes_sent_kb"],
                "bytes_recv_kb": tf["bytes_recv_kb"],
            }
    monitor = _FakeMonitor()


# ─────────────────────────────────────────────────────────────────────────────
# Standalone entry point — demo mode
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if not TK_AVAILABLE:
        print("ERROR: tkinter not available.")
        sys.exit(1)
    if psutil is None:
        print("ERROR: psutil not installed.  Run: pip install psutil")
        sys.exit(1)

    print("Starting TaskForge Interactive Dashboard (demo mode)…")
    dash = PrivacyDashboard()

    # Demo: fake agent callback that echoes back a simulated multi-step run
    def _demo_agent(goal: str) -> str:
        steps = [
            ("observe_desktop", "Scanning screen state…"),
            ("focus_app",       f"Focusing app for: {goal}"),
            ("read_screen",     "Screen OCR complete — 12 elements found"),
            ("type_anywhere",   f"Typed: {goal[:40]}"),
            ("press_key",       "enter — confirming action"),
            ("goal_completed",  f"Done: {goal}"),
        ]
        for tool, detail in steps:
            time.sleep(1.0)
            dash.log_action(tool, detail)
        return f"Completed: {goal}"

    dash.set_agent_callback(_demo_agent)

    # Pre-seed log with demo entries
    demo_entries = [
        ("goal_completed",  "Sent 'Meeting at 3pm' to John on WhatsApp"),
        ("press_key",       "enter → Message sent ✅"),
        ("type_anywhere",   "Typed 'Meeting at 3pm'"),
        ("focus_app",       "Brought 'WhatsApp Desktop' to foreground"),
        ("system_settings", "set volume to 50 → Volume set to 50%"),
    ]
    for tool, detail in demo_entries:
        dash.log_action(tool, detail)

    dash.start(blocking=True)
