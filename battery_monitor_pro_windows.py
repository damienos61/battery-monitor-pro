import json
import os
import platform
import psutil
import subprocess
import sys
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox

# Optional libs
try:
    from plyer import notification as plyer_notification
except Exception:
    plyer_notification = None

try:
    from win10toast import ToastNotifier
except Exception:
    ToastNotifier = None

try:
    import pystray
    from PIL import Image, ImageDraw
except Exception:
    pystray = None
    Image = None

# Windows-specific registry for auto-start
IS_WINDOWS = platform.system() == "Windows"
if IS_WINDOWS:
    try:
        import winreg
    except Exception:
        winreg = None

# Paths & constants
APP_NAME = "Battery Monitor Pro"
if IS_WINDOWS:
    CONFIG_DIR = Path(os.getenv("APPDATA", Path.home()))
else:
    CONFIG_DIR = Path.home() / ".config"
CONFIG_DIR = CONFIG_DIR / "battery_monitor_pro"
CONFIG_DIR.mkdir(parents=True, exist_ok=True)
CONFIG_FILE = CONFIG_DIR / "config.json"
LOG_FILE = CONFIG_DIR / "monitor.log"

DEFAULT_CONFIG = {
    "seuil_critique": 15,
    "seuil_plein": 95,
    "interval_ms": 5000,
    "history_size": 60,
    "start_minimized": False,
    "enable_notifications": True,
    "play_sound": True,
    "auto_hibernate": False,
    "hibernate_threshold": 5,
    "enable_power_saver_auto": False,
    "power_saver_threshold": 20,
    "custom_command": "",  # command to run on critical (optional)
    "autorun": False,
}

# Simple file logger
def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}\n"
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass
    print(line, end="")

# Notification helpers
class Notifier:
    def __init__(self):
        self.win_toaster = ToastNotifier() if ToastNotifier and IS_WINDOWS else None

    def notify(self, title, msg, duration=6):
        log(f"Notification: {title} - {msg}")
        try:
            if IS_WINDOWS and self.win_toaster:
                # ToastNotifier shows as Windows toast (works best from .exe)
                self.win_toaster.show_toast(title, msg, duration=duration, threaded=True)
                return
            if plyer_notification:
                plyer_notification.notify(title=title, message=msg, app_name=APP_NAME, timeout=duration)
                return
            # fallback: simple messagebox (non-blocking thread)
            threading.Thread(target=lambda: messagebox.showinfo(title, msg), daemon=True).start()
        except Exception as e:
            log(f"Notification erreur: {e}")

notifier = Notifier()

# Utility functions for Windows actions
def windows_set_power_saver():
    """Try to set the active power scheme to the 'Power saver' scheme by parsing powercfg output."""
    if not IS_WINDOWS:
        log("Power saver only supported on Windows.")
        return False, "Non-Windows"
    try:
        res = subprocess.run(["powercfg", "-l"], capture_output=True, text=True, check=False)
        out = res.stdout + res.stderr
        # Look for 'Power saver' line and GUID between parentheses
        import re
        m = re.search(r"([0-9A-Fa-f\-]{36})\s+\(Power saver\)", out)
        if m:
            guid = m.group(1)
            subprocess.run(["powercfg", "/setactive", guid], check=False)
            log(f"Power saver activé (GUID {guid})")
            return True, "Power saver activé"
        # If not found, try known GUID (may fail on some systems)
        known = "a1841308-3541-4fab-bc81-f71556f20b4a"
        subprocess.run(["powercfg", "/setactive", known], check=False)
        log("Tentative activation Power saver (GUID connu)")
        return True, "Tentative activation Power saver"
    except Exception as e:
        log(f"Erreur powercfg: {e}")
        return False, str(e)

def windows_hibernate():
    """Hibernates the system (shutdown /h) - requires that Hibernation is enabled."""
    if not IS_WINDOWS:
        return False, "Non-Windows"
    try:
        # Confirm that hibernate is enabled? We just attempt
        subprocess.run(["shutdown", "/h"], check=False)
        log("Commande hibernate lancée")
        return True, "Hibernation lancée"
    except Exception as e:
        log(f"Erreur hibernate: {e}")
        return False, str(e)

def set_autorun(enabled: bool, name=APP_NAME, target=None):
    """Enable or disable autorun for current user via HKCU Run."""
    if not IS_WINDOWS or winreg is None:
        log("Autorun non supporté (non-Windows).")
        return False, "Non-Windows"
    try:
        if target is None:
            # default: path to running script or exe
            if getattr(sys, "frozen", False):
                target = sys.executable
            else:
                target = f'"{sys.executable}" "{os.path.abspath(sys.argv[0])}"'
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_ALL_ACCESS) as key:
            if enabled:
                winreg.SetValueEx(key, name, 0, winreg.REG_SZ, target)
                log(f"Autorun activé: {target}")
            else:
                try:
                    winreg.DeleteValue(key, name)
                    log("Autorun désactivé")
                except FileNotFoundError:
                    log("Autorun déjà désactivé")
        return True, "OK"
    except Exception as e:
        log(f"Erreur autorun: {e}")
        return False, str(e)

# Main Application
class BatteryMonitorPro:
    def __init__(self):
        self.config = self.load_config()
        self.seuil_critique = int(self.config.get("seuil_critique", DEFAULT_CONFIG["seuil_critique"]))
        self.seuil_plein = int(self.config.get("seuil_plein", DEFAULT_CONFIG["seuil_plein"]))
        self.interval_ms = int(self.config.get("interval_ms", DEFAULT_CONFIG["interval_ms"]))
        self.history_size = int(self.config.get("history_size", DEFAULT_CONFIG["history_size"]))
        self.start_minimized = bool(self.config.get("start_minimized", DEFAULT_CONFIG["start_minimized"]))
        self.enable_notifications = bool(self.config.get("enable_notifications", DEFAULT_CONFIG["enable_notifications"]))
        self.play_sound = bool(self.config.get("play_sound", DEFAULT_CONFIG["play_sound"]))
        self.auto_hibernate = bool(self.config.get("auto_hibernate", DEFAULT_CONFIG["auto_hibernate"]))
        self.hibernate_threshold = int(self.config.get("hibernate_threshold", DEFAULT_CONFIG["hibernate_threshold"]))
        self.enable_power_saver_auto = bool(self.config.get("enable_power_saver_auto", DEFAULT_CONFIG["enable_power_saver_auto"]))
        self.power_saver_threshold = int(self.config.get("power_saver_threshold", DEFAULT_CONFIG["power_saver_threshold"]))
        self.custom_command = str(self.config.get("custom_command", DEFAULT_CONFIG["custom_command"]))
        self.autorun = bool(self.config.get("autorun", DEFAULT_CONFIG["autorun"]))

        self.deja_alerte_critique = False
        self.deja_alerte_plein = False
        self.systeme_os = platform.system()
        self.history = deque(maxlen=self.history_size)

        # Tkinter setup
        self.root = tk.Tk()
        self.root.title(APP_NAME)
        self.root.geometry("420x540")
        self.root.configure(bg="#0f1724")
        self.root.resizable(False, False)

        # Styles
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except Exception:
            pass

        # Top frame: gauge and main info
        top = tk.Frame(self.root, bg="#0f1724")
        top.pack(padx=12, pady=12, fill="x")
        self.canvas = tk.Canvas(top, width=320, height=320, bg="#0f1724", bd=0, highlightthickness=0)
        self.canvas.pack()

        # Status labels
        self.label_state = tk.Label(self.root, text="--", font=("Segoe UI", 12, "bold"), bg="#0f1724", fg="#cbd5e1")
        self.label_state.pack()
        self.label_time = tk.Label(self.root, text="--", font=("Segoe UI", 10), bg="#0f1724", fg="#94a3b8")
        self.label_time.pack(pady=(2, 6))

        # Sparkline / history
        self.history_canvas = tk.Canvas(self.root, width=380, height=70, bg="#071026", bd=0, highlightthickness=0)
        self.history_canvas.pack(pady=(6, 10))

        # Info frame: charge rate, avg rate, last update
        info_frame = tk.Frame(self.root, bg="#0f1724")
        info_frame.pack(fill="x", padx=12)
        self.label_rate = tk.Label(info_frame, text="Taux: -- %/h", bg="#0f1724", fg="#94a3b8")
        self.label_rate.pack(side="left")
        self.label_last = tk.Label(info_frame, text="Dernière mise à jour: --", bg="#0f1724", fg="#94a3b8")
        self.label_last.pack(side="right")

        # Buttons
        btn_frame = tk.Frame(self.root, bg="#0f1724")
        btn_frame.pack(fill="x", padx=12, pady=(8, 0))
        self.btn_settings = tk.Button(btn_frame, text="⚙️ Réglages", command=self.open_settings, bg="#0b1220", fg="white", bd=0, padx=12, pady=8, cursor="hand2")
        self.btn_settings.pack(side="left")
        self.btn_minimize = tk.Button(btn_frame, text="🗕 Minimiser", command=self.minimize_to_tray_or_icon, bg="#0b1220", fg="white", bd=0, padx=12, pady=8, cursor="hand2")
        self.btn_minimize.pack(side="left", padx=8)
        self.btn_action = tk.Button(btn_frame, text="🔧 Actions", command=self.open_actions_menu, bg="#0b1220", fg="white", bd=0, padx=12, pady=8, cursor="hand2")
        self.btn_action.pack(side="left", padx=8)
        self.btn_quit = tk.Button(self.root, text="FERMER", command=self.quit_app, bg="#ef4444", fg="white", bd=0, padx=16, pady=8, cursor="hand2")
        self.btn_quit.pack(side="bottom", pady=12)

        # Tray icon
        self.tray_icon = None
        if pystray and Image:
            try:
                self.setup_tray_icon()
            except Exception as e:
                log(f"Tray init failed: {e}")

        # Auto-start on Windows if configured
        if IS_WINDOWS and self.autorun:
            set_autorun(True)

        # Start minimized if requested
        if self.start_minimized:
            self.root.withdraw()

        # Control variables
        self._running = True
        self.last_percent = None
        self.last_update = None

        # Begin update loop
        self.root.protocol("WM_DELETE_WINDOW", self.quit_app)
        self.update_interface()

    # ---------------- Config ----------------
    def load_config(self):
        try:
            if CONFIG_FILE.exists():
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                    log("Configuration chargée.")
                    return cfg
        except Exception as e:
            log(f"Erreur lecture config: {e}")
        # save defaults
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(DEFAULT_CONFIG, f, indent=2)
        except Exception as e:
            log(f"Erreur écriture config par défaut: {e}")
        return DEFAULT_CONFIG.copy()

    def save_config(self):
        try:
            to_save = {
                "seuil_critique": self.seuil_critique,
                "seuil_plein": self.seuil_plein,
                "interval_ms": self.interval_ms,
                "history_size": self.history_size,
                "start_minimized": self.start_minimized,
                "enable_notifications": self.enable_notifications,
                "play_sound": self.play_sound,
                "auto_hibernate": self.auto_hibernate,
                "hibernate_threshold": self.hibernate_threshold,
                "enable_power_saver_auto": self.enable_power_saver_auto,
                "power_saver_threshold": self.power_saver_threshold,
                "custom_command": self.custom_command,
                "autorun": self.autorun,
            }
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(to_save, f, indent=2)
            log("Configuration sauvegardée.")
        except Exception as e:
            log(f"Erreur sauvegarde config: {e}")

    # ---------------- Tray ----------------
    def setup_tray_icon(self):
        size = 64
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        # background circle
        d.ellipse((2, 2, size - 2, size - 2), fill=(10, 18, 36, 255))
        # small battery rectangle
        d.rectangle((14, 20, 50, 44), fill=(17, 24, 39, 255))
        d.rectangle((50, 26, 54, 38), fill=(17, 24, 39, 255))
        d.rectangle((16, 22, 48, 42), fill=(144, 205, 244, 255))
        menu = pystray.Menu(
            pystray.MenuItem("Ouvrir", lambda _: self.show_window()),
            pystray.MenuItem("Activer Power Saver", lambda _: self._call_windows_power_saver()),
            pystray.MenuItem("Hiberner maintenant", lambda _: self._call_windows_hibernate()),
            pystray.MenuItem("Quitter", lambda _: self.quit_app()),
        )
        self.tray_icon = pystray.Icon("battery_monitor_pro", img, APP_NAME, menu)
        def _run_tray():
            try:
                self.tray_icon.run()
            except Exception as e:
                log(f"Tray run error: {e}")
        threading.Thread(target=_run_tray, daemon=True).start()
        log("Tray icon démarrée.")

    def show_window(self):
        try:
            self.root.deiconify()
            self.root.attributes("-topmost", True)
            self.root.after(100, lambda: self.root.attributes("-topmost", False))
        except Exception:
            pass

    def minimize_to_tray_or_icon(self):
        if self.tray_icon:
            self.root.withdraw()
        else:
            self.root.iconify()

    # ---------------- Actions ----------------
    def _call_windows_power_saver(self):
        if not IS_WINDOWS:
            messagebox.showinfo("Info", "Action disponible uniquement sous Windows.")
            return
        if messagebox.askyesno("Power Saver", "Activer le mode Economie d'énergie maintenant ?"):
            ok, msg = windows_set_power_saver()
            messagebox.showinfo("Power Saver", msg)

    def _call_windows_hibernate(self):
        if not IS_WINDOWS:
            messagebox.showinfo("Info", "Action disponible uniquement sous Windows.")
            return
        if messagebox.askyesno("Hibernation", "Etes-vous sûr de vouloir hiberner maintenant ?"):
            ok, msg = windows_hibernate()
            messagebox.showinfo("Hibernation", msg)

    # ---------------- Sound ----------------
    def beep(self):
        if not self.play_sound:
            return
        try:
            if IS_WINDOWS:
                import winsound
                winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
            else:
                print("\a", end="", flush=True)
        except Exception as e:
            log(f"Beep erreur: {e}")

    # ---------------- UI drawing ----------------
    def draw_gauge(self, pct, charging):
        self.canvas.delete("all")
        size = 320
        pad = 28
        x0, y0, x1, y1 = pad, pad, size - pad, size - pad
        # color selection
        if charging:
            color = "#60a5fa"
        elif pct > 50:
            color = "#34d399"
        elif pct > 20:
            color = "#fbbf24"
        else:
            color = "#fb7185"
        # background arc
        self.canvas.create_oval(x0, y0, x1, y1, outline="#0b1220", width=28)
        # progress
        angle = int(pct * 360 / 100)
        if angle > 0:
            self.canvas.create_arc(x0, y0, x1, y1, start=90, extent=-angle, style="arc", outline=color, width=28)
        inner = 60
        self.canvas.create_oval(x0 + inner, y0 + inner, x1 - inner, y1 - inner, fill="#07142a", outline="#07142a")
        # center text
        self.canvas.create_text(size//2, size//2 - 12, text=f"{pct}%", font=("Segoe UI", 36, "bold"), fill=color)
        st = "⚡ Charging" if charging else "🔋 On battery"
        self.canvas.create_text(size//2, size//2 + 32, text=st, font=("Segoe UI", 11), fill="#9fb0c8")

    def draw_history(self):
        self.history_canvas.delete("all")
        w = int(self.history_canvas["width"])
        h = int(self.history_canvas["height"])
        data = list(self.history)
        if not data:
            return
        max_v = max(max(data), 100)
        min_v = min(min(data), 0)
        span = max_v - min_v if max_v != min_v else 1
        n = len(data)
        step = w / max(1, self.history.maxlen - 1)
        points = []
        for i, v in enumerate(data):
            x = i * step
            y = h - ((v - min_v) / span) * (h - 8) - 4
            points.append((x, y))
        flat = []
        for p in points:
            flat.extend(p)
        if flat:
            self.history_canvas.create_line(*flat, fill="#60a5fa", width=2, smooth=True)
            for x, y in points:
                self.history_canvas.create_oval(x-2, y-2, x+2, y+2, fill="#60a5fa", outline="")

    # ---------------- Time formatting ----------------
    def format_time(self, secs):
        try:
            if secs is None:
                return "inconnu"
            if secs == psutil.POWER_TIME_UNLIMITED:
                return "illimité"
            if secs == psutil.POWER_TIME_UNKNOWN or secs < 0:
                return "calcul..."
            h = secs // 3600
            m = (secs % 3600) // 60
            return f"{h}h {m}m" if h > 0 else f"{m} min"
        except Exception:
            return "inconnu"

    # ---------------- Update loop ----------------
    def update_interface(self):
        if not self._running:
            return
        try:
            batterie = psutil.sensors_battery()
            if not batterie:
                self.draw_gauge(0, False)
                self.label_state.config(text="Aucune batterie détectée")
                self.label_time.config(text="--")
                self.history.clear()
            else:
                pct = int(round(batterie.percent))
                charging = bool(batterie.power_plugged)
                secsleft = batterie.secsleft
                txt_time = self.format_time(secsleft)
                now = datetime.now().strftime("%H:%M:%S")
                # compute rate from history if possible
                if self.last_percent is not None and self.last_update:
                    dt = (time.time() - self.last_update) / 3600.0  # hours
                    if dt > 0:
                        rate_per_hour = (pct - self.last_percent) / dt
                    else:
                        rate_per_hour = 0.0
                else:
                    rate_per_hour = 0.0
                self.history.append(pct)
                self.draw_gauge(pct, charging)
                self.draw_history()
                self.label_state.config(text="BRANCHÉ" if charging else "SUR BATTERIE")
                self.label_time.config(text=f"Estimation: {txt_time} • Historique: {len(self.history)} points")
                self.label_rate.config(text=f"Taux: {rate_per_hour:+.1f} %/h")
                self.label_last.config(text=f"Dernière: {now}")

                # Alerts:
                # 1) Critical low
                if pct <= self.seuil_critique and not charging:
                    if not self.deja_alerte_critique:
                        log(f"Alerte critique: {pct}%")
                        if self.enable_notifications:
                            notifier.notify("Batterie CRITIQUE", f"Niveau {pct}%. {txt_time} restants.")
                        self.beep()
                        # run custom command safely
                        if self.custom_command:
                            try:
                                subprocess.Popen(self.custom_command, shell=True)
                                log(f"Commande personnalisée lancée: {self.custom_command}")
                            except Exception as e:
                                log(f"Erreur commande personnalisée: {e}")
                        # auto power saver
                        if self.enable_power_saver_auto and IS_WINDOWS:
                            windows_set_power_saver()
                        # auto hibernate if configured and pct <= threshold
                        if self.auto_hibernate and IS_WINDOWS and pct <= self.hibernate_threshold:
                            # warn user and hibernate
                            log(f"Auto-hibernate déclenchée à {pct}%")
                            # we do not automatically hibernate without user's consent at runtime:
                            # ask once per session
                            if messagebox.askyesno("Auto-Hibernate", f"Niveau {pct}%. Hiberner maintenant ?"):
                                windows_hibernate()
                        self.show_big_alert(pct, txt_time, critical=True)
                        self.deja_alerte_critique = True
                else:
                    self.deja_alerte_critique = False

                # 2) Full-charge reminder
                if charging and pct >= self.seuil_plein:
                    if not self.deja_alerte_plein:
                        log(f"Alerte batterie pleine: {pct}%")
                        if self.enable_notifications:
                            notifier.notify("Batterie pleine", f"Niveau {pct}%. Débranchez pour préserver la batterie.")
                        self.beep()
                        self.show_big_alert(pct, txt_time, critical=False, full=True)
                        self.deja_alerte_plein = True
                else:
                    self.deja_alerte_plein = False

                # update trackers
                self.last_percent = pct
                self.last_update = time.time()
        except Exception as e:
            log(f"Erreur update: {e}")

        # schedule
        try:
            self.root.after(self.interval_ms, self.update_interface)
        except Exception as e:
            log(f"Erreur scheduling update: {e}")

    # ---------------- Big alert window ----------------
    def show_big_alert(self, pct, txt_time, critical=False, full=False):
        try:
            popup = tk.Toplevel(self.root)
            popup.title("ALERTE BATTERIE")
            popup.attributes("-topmost", True)
            popup.geometry("800x420")
            popup.configure(bg="#111827")
            popup.transient(self.root)
            frame = tk.Frame(popup, bg="#111827")
            frame.place(relx=0.5, rely=0.5, anchor="center")
            if critical:
                tk.Label(frame, text="⚠️ BATTERIE CRITIQUE ⚠️", font=("Segoe UI", 44, "bold"), bg="#111827", fg="#fecaca").pack()
                tk.Label(frame, text=f"Niveau: {pct}%", font=("Segoe UI", 28), bg="#111827", fg="#fff1f2").pack(pady=6)
                tk.Label(frame, text=f"Temps estimé: {txt_time}", font=("Segoe UI", 18), bg="#111827", fg="#f8fafc").pack(pady=6)
                btn = tk.Button(frame, text="Hiberner maintenant", command=lambda: (windows_hibernate(), popup.destroy()) if IS_WINDOWS else None,
                                bg="#dc2626", fg="white", bd=0, padx=18, pady=10)
                btn.pack(pady=14)
                tk.Button(frame, text="Je comprends, je branche", command=popup.destroy, bg="#0ea5e9", fg="white", bd=0, padx=18, pady=10).pack()
            elif full:
                tk.Label(frame, text="🔌 BATTERIE PLEINE 🔌", font=("Segoe UI", 40, "bold"), bg="#111827", fg="#bbf7d0").pack()
                tk.Label(frame, text=f"Niveau: {pct}% — Débranchez pour préserver la batterie", font=("Segoe UI", 16), bg="#111827", fg="#e6fffa").pack(pady=8)
                tk.Button(frame, text="Je débranche", command=popup.destroy, bg="#34d399", fg="white", bd=0, padx=18, pady=10).pack(pady=12)
            else:
                tk.Label(frame, text="Notification", font=("Segoe UI", 28, "bold"), bg="#111827", fg="#cbd5e1").pack()
                tk.Label(frame, text=f"{pct}% — {txt_time}", font=("Segoe UI", 14), bg="#111827", fg="#e6eef8").pack(pady=8)
                tk.Button(frame, text="Fermer", command=popup.destroy, bg="#94a3b8", fg="white", bd=0, padx=18, pady=10).pack(pady=12)
            # beep once
            self.beep()
        except Exception as e:
            log(f"Erreur big alert: {e}")

    # ---------------- Settings UI ----------------
    def open_settings(self):
        win = tk.Toplevel(self.root)
        win.title("Réglages")
        win.geometry("560x520")
        win.configure(bg="#061428")
        win.transient(self.root)
        win.grab_set()

        # Layout: left labels, right controls
        lframe = tk.Frame(win, bg="#061428")
        lframe.pack(fill="both", expand=True, padx=12, pady=12)

        # Thresholds
        tk.Label(lframe, text="Seuil critique (%)", bg="#061428", fg="#cbd5e1").grid(row=0, column=0, sticky="w")
        crit_var = tk.IntVar(value=self.seuil_critique)
        crit_scale = ttk.Scale(lframe, from_=1, to=30, orient="horizontal", variable=crit_var)
        crit_scale.grid(row=0, column=1, sticky="we", padx=8, pady=6)
        crit_lbl = tk.Label(lframe, text=f"{self.seuil_critique} %", bg="#061428", fg="#9fb0c8")
        crit_lbl.grid(row=0, column=2, sticky="e")
        def update_crit(*_):
            crit_lbl.config(text=f"{crit_var.get()} %")
        crit_var.trace_add("write", update_crit)

        tk.Label(lframe, text="Seuil plein (%)", bg="#061428", fg="#cbd5e1").grid(row=1, column=0, sticky="w")
        full_var = tk.IntVar(value=self.seuil_plein)
        full_scale = ttk.Scale(lframe, from_=80, to=100, orient="horizontal", variable=full_var)
        full_scale.grid(row=1, column=1, sticky="we", padx=8, pady=6)
        full_lbl = tk.Label(lframe, text=f"{self.seuil_plein} %", bg="#061428", fg="#9fb0c8")
        full_lbl.grid(row=1, column=2, sticky="e")
        def update_full(*_):
            full_lbl.config(text=f"{full_var.get()} %")
        full_var.trace_add("write", update_full)

        # Interval
        tk.Label(lframe, text="Intervalle d'actualisation (ms)", bg="#061428", fg="#cbd5e1").grid(row=2, column=0, sticky="w")
        interval_var = tk.IntVar(value=self.interval_ms)
        interval_entry = ttk.Entry(lframe, textvariable=interval_var, width=12)
        interval_entry.grid(row=2, column=1, sticky="w", padx=8, pady=6)

        # Auto hibernate
        auto_h_var = tk.BooleanVar(value=self.auto_hibernate)
        tk.Checkbutton(lframe, text="Auto hibernation (Windows)", variable=auto_h_var, bg="#061428", fg="#cbd5e1", selectcolor="#061428").grid(row=3, column=0, columnspan=2, sticky="w")
        tk.Label(lframe, text="Seuil auto-hibernate (%)", bg="#061428", fg="#cbd5e1").grid(row=4, column=0, sticky="w")
        hiber_var = tk.IntVar(value=self.hibernate_threshold)
        hiber_scale = ttk.Scale(lframe, from_=1, to=15, orient="horizontal", variable=hiber_var)
        hiber_scale.grid(row=4, column=1, sticky="we", padx=8, pady=6)
        hiber_lbl = tk.Label(lframe, text=f"{self.hibernate_threshold} %", bg="#061428", fg="#9fb0c8")
        hiber_lbl.grid(row=4, column=2, sticky="e")
        def update_hiber(*_):
            hiber_lbl.config(text=f"{hiber_var.get()} %")
        hiber_var.trace_add("write", update_hiber)

        # Power saver auto
        ps_var = tk.BooleanVar(value=self.enable_power_saver_auto)
        tk.Checkbutton(lframe, text="Activer Power Saver automatiquement (Windows)", variable=ps_var, bg="#061428", fg="#cbd5e1", selectcolor="#061428").grid(row=5, column=0, columnspan=2, sticky="w")
        tk.Label(lframe, text="Seuil Power Saver (%)", bg="#061428", fg="#cbd5e1").grid(row=6, column=0, sticky="w")
        ps_thresh_var = tk.IntVar(value=self.power_saver_threshold)
        ps_scale = ttk.Scale(lframe, from_=10, to=50, orient="horizontal", variable=ps_thresh_var)
        ps_scale.grid(row=6, column=1, sticky="we", padx=8, pady=6)
        ps_lbl = tk.Label(lframe, text=f"{self.power_saver_threshold} %", bg="#061428", fg="#9fb0c8")
        ps_lbl.grid(row=6, column=2, sticky="e")
        def update_ps(*_):
            ps_lbl.config(text=f"{ps_thresh_var.get()} %")
        ps_thresh_var.trace_add("write", update_ps)

        # Notifications, sounds, autorun
        notif_var = tk.BooleanVar(value=self.enable_notifications)
        sound_var = tk.BooleanVar(value=self.play_sound)
        start_min_var = tk.BooleanVar(value=self.start_minimized)
        autorun_var = tk.BooleanVar(value=self.autorun)
        tk.Checkbutton(lframe, text="Activer notifications", variable=notif_var, bg="#061428", fg="#cbd5e1", selectcolor="#061428").grid(row=7, column=0, columnspan=2, sticky="w")
        tk.Checkbutton(lframe, text="Activer son d'alerte", variable=sound_var, bg="#061428", fg="#cbd5e1", selectcolor="#061428").grid(row=8, column=0, columnspan=2, sticky="w")
        tk.Checkbutton(lframe, text="Démarrer minimisé", variable=start_min_var, bg="#061428", fg="#cbd5e1", selectcolor="#061428").grid(row=9, column=0, columnspan=2, sticky="w")
        tk.Checkbutton(lframe, text="Activer lancement au démarrage (Windows)", variable=autorun_var, bg="#061428", fg="#cbd5e1", selectcolor="#061428").grid(row=10, column=0, columnspan=2, sticky="w")

        # Custom command
        tk.Label(lframe, text="Commande personnalisée (sur critique)", bg="#061428", fg="#cbd5e1").grid(row=11, column=0, sticky="w", pady=(8, 0))
        cmd_var = tk.StringVar(value=self.custom_command)
        cmd_entry = ttk.Entry(lframe, textvariable=cmd_var, width=48)
        cmd_entry.grid(row=11, column=1, columnspan=2, sticky="we", padx=8, pady=(8, 0))

        # Save/Cancel buttons
        def save_and_close():
            try:
                self.seuil_critique = max(1, min(99, int(crit_var.get())))
                self.seuil_plein = max(50, min(100, int(full_var.get())))
                self.interval_ms = max(1000, int(interval_var.get()))
                self.auto_hibernate = bool(auto_h_var.get())
                self.hibernate_threshold = max(1, min(20, int(hiber_var.get())))
                self.enable_power_saver_auto = bool(ps_var.get())
                self.power_saver_threshold = max(5, min(50, int(ps_thresh_var.get())))
                self.enable_notifications = bool(notif_var.get())
                self.play_sound = bool(sound_var.get())
                self.start_minimized = bool(start_min_var.get())
                self.custom_command = str(cmd_var.get())
                # autorun handling
                new_autorun = bool(autorun_var.get())
                if new_autorun != self.autorun and IS_WINDOWS:
                    ok, msg = set_autorun(new_autorun)
                    if not ok:
                        messagebox.showwarning("Autorun", f"Impossible de modifier autorun: {msg}")
                self.autorun = new_autorun

                self.save_config()
                messagebox.showinfo("Réglages", "Paramètres sauvegardés.")
                win.destroy()
            except Exception as e:
                messagebox.showerror("Erreur", f"Impossible de sauvegarder: {e}")

        btn_save = tk.Button(lframe, text="Sauvegarder", command=save_and_close, bg="#0ea5e9", fg="white", bd=0, padx=14, pady=8)
        btn_save.grid(row=12, column=1, sticky="e", pady=12)
        btn_cancel = tk.Button(lframe, text="Annuler", command=win.destroy, bg="#94a3b8", fg="white", bd=0, padx=14, pady=8)
        btn_cancel.grid(row=12, column=2, sticky="w", pady=12)

        # Make grid expand for the middle column
        lframe.columnconfigure(1, weight=1)

    # ---------------- Actions menu (quick) ----------------
    def open_actions_menu(self):
        menu = tk.Toplevel(self.root)
        menu.title("Actions rapides")
        menu.geometry("360x220")
        menu.configure(bg="#07122a")
        menu.transient(self.root)
        tk.Button(menu, text="Activer Power Saver (Windows)", command=lambda: (self._call_windows_power_saver(), menu.destroy()), bg="#0ea5e9", fg="white", bd=0, padx=12, pady=8).pack(pady=8)
        tk.Button(menu, text="Hiberner maintenant (Windows)", command=lambda: (self._call_windows_hibernate(), menu.destroy()), bg="#ef4444", fg="white", bd=0, padx=12, pady=8).pack(pady=8)
        tk.Button(menu, text="Tester notification", command=lambda: (notifier.notify("Test", "Ceci est une notification de test."), menu.destroy()), bg="#94a3b8", fg="white", bd=0, padx=12, pady=8).pack(pady=8)

    # ---------------- Quit ----------------
    def quit_app(self):
        self._running = False
        try:
            if self.tray_icon:
                try:
                    self.tray_icon.stop()
                except Exception:
                    pass
        except Exception:
            pass
        try:
            self.save_config()
        except Exception:
            pass
        try:
            self.root.destroy()
        except Exception:
            pass
        log("Application terminée.")
        # when packaged as .exe, sys.exit helpful
        try:
            sys.exit(0)
        except SystemExit:
            pass

# ---------------- Entrypoint ----------------
def main():
    log("Démarrage Battery Monitor Pro")
    app = BatteryMonitorPro()
    app.root.mainloop()

if __name__ == "__main__":
    main()