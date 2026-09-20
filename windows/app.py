"""
MouseShare - Windows side.  Run:  python app.py   (or run.bat / MouseShare.exe)

Options: --tray  start hidden in the notification area.
"""
import ctypes
import sys
import threading
import time
import tkinter as tk
import traceback
from tkinter import ttk

import winio                       # sets DPI awareness on import - keep before creating Tk
import discovery
from bridge import Bridge, BridgeError, Config

try:
    import win_tray
except Exception:
    win_tray = None

EDGES = {"Right": "right", "Left": "left", "Top": "top", "Bottom": "bottom"}
BUTTONS = {
    "Off": "off",
    "Middle button (wheel click)": "M",
    "Side button 1 (back)": "X1",
    "Side button 2 (forward)": "X2",
}
_KEYSYM_NAMES = {
    "Escape": "esc", "Return": "enter", "Prior": "pageup", "Next": "pagedown",
    "Scroll_Lock": "scrolllock", "space": "space", "Tab": "tab", "Pause": "pause",
    "Insert": "insert", "Delete": "delete", "Home": "home", "End": "end",
    "Left": "left", "Right": "right", "Up": "up", "Down": "down",
}


def _reverse(mapping, value):
    for label, val in mapping.items():
        if val == value:
            return label
    return next(iter(mapping))


def already_running():
    """Named mutex: a second copy would install a second set of input hooks."""
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW(None, False, "Global\\MouseShareSingleInstance")
        return ctypes.get_last_error() == 183          # ERROR_ALREADY_EXISTS
    except Exception:
        return False


class App:
    def __init__(self, start_hidden=False):
        self.cfg = Config()
        self.bridge = Bridge(self.cfg, winio.WinScreen())
        self.hooks = winio.Hooks(self.bridge)
        self.devices = []
        self.running = True
        self.tray = None

        self.root = tk.Tk()
        self.root.title("MouseShare")
        self.root.resizable(False, False)
        self._build()

        self.hooks.start()
        threading.Thread(target=self._ticker, daemon=True).start()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self._start_tray()
        self._refresh()

        if start_hidden or self.cfg["start_minimized"]:
            self.hide()
        if self.cfg["autoconnect"]:
            self.root.after(400, lambda: self._connect_async(manual=True))
        self.root.after(600, self.scan)

    # -------------------------------------------------------------------- UI
    def _bind(self, var, key, convert=lambda v: v, after=None):
        def changed(*_):
            try:
                self.cfg[key] = convert(var.get())
            except (ValueError, tk.TclError):
                return
            self.cfg.save()
            if after:
                after()
        var.trace_add("write", changed)

    def _build(self):
        pad = {"padx": 10, "pady": 5}
        cfg = self.cfg
        nb = ttk.Notebook(self.root)
        nb.grid(row=0, column=0, sticky="nsew", **pad)
        tab_conn = ttk.Frame(nb)
        tab_switch = ttk.Frame(nb)
        tab_opts = ttk.Frame(nb)
        nb.add(tab_conn, text="Connection")
        nb.add(tab_switch, text="Switching")
        nb.add(tab_opts, text="Options")

        # ---------------------------------------------------------- connection
        box = ttk.LabelFrame(tab_conn, text="Phones found (double-click to connect)")
        box.grid(row=0, column=0, sticky="ew", **pad)
        box.columnconfigure(0, weight=1)

        self.tree = ttk.Treeview(box, columns=("how", "name", "addr"), show="headings", height=5)
        for col, title, width in (("how", "Link", 60), ("name", "Device", 190), ("addr", "Address", 170)):
            self.tree.heading(col, text=title)
            self.tree.column(col, width=width, anchor="w")
        self.tree.grid(row=0, column=0, columnspan=3, sticky="ew", padx=6, pady=4)
        self.tree.bind("<<TreeviewSelect>>", self._pick_device)
        self.tree.bind("<Double-1>", self._connect_selected)

        self.btn_scan = ttk.Button(box, text="Scan again", command=self.scan)
        self.btn_scan.grid(row=1, column=0, padx=6, pady=(0, 6), sticky="w")
        self.btn_connect = ttk.Button(box, text="Connect", command=self.toggle_connect)
        self.btn_connect.grid(row=1, column=1, padx=6, pady=(0, 6), sticky="e")
        self.lbl_found = ttk.Label(box, text="")
        self.lbl_found.grid(row=1, column=2, padx=6, sticky="w")

        box = ttk.LabelFrame(tab_conn, text="Connection details")
        box.grid(row=1, column=0, sticky="ew", **pad)
        self.v_transport = tk.StringVar(value=cfg["transport"])
        ttk.Radiobutton(box, text="WiFi", variable=self.v_transport, value="wifi",
                        command=self._transport_changed).grid(row=0, column=0, sticky="w", padx=6)
        ttk.Radiobutton(box, text="USB (adb - no IP needed)", variable=self.v_transport,
                        value="usb", command=self._transport_changed).grid(row=0, column=1,
                                                                           sticky="w")
        self._bind(self.v_transport, "transport")

        self.v_ip = tk.StringVar(value=cfg["phone_ip"])
        self.v_port = tk.IntVar(value=cfg["port"])
        self.v_pin = tk.StringVar(value=cfg["pin"])
        self.v_serial = tk.StringVar(value=cfg["usb_serial"])
        self.lbl_ip = ttk.Label(box, text="Phone IP")
        self.lbl_ip.grid(row=1, column=0, sticky="w", padx=6, pady=3)
        self.ent_ip = ttk.Entry(box, textvariable=self.v_ip, width=18)
        self.ent_ip.grid(row=1, column=1, sticky="w")
        self.lbl_serial = ttk.Label(box, text="USB device")
        self.lbl_serial.grid(row=2, column=0, sticky="w", padx=6)
        self.ent_serial = ttk.Entry(box, textvariable=self.v_serial, width=24, state="readonly")
        self.ent_serial.grid(row=2, column=1, sticky="w")
        ttk.Label(box, text="Port").grid(row=3, column=0, sticky="w", padx=6)
        ttk.Entry(box, textvariable=self.v_port, width=8).grid(row=3, column=1, sticky="w")
        ttk.Label(box, text="PIN").grid(row=4, column=0, sticky="w", padx=6, pady=3)
        ttk.Entry(box, textvariable=self.v_pin, width=18).grid(row=4, column=1, sticky="w")
        self._bind(self.v_ip, "phone_ip", lambda v: v.strip())
        self._bind(self.v_port, "port", int)
        self._bind(self.v_pin, "pin", lambda v: v.strip())
        self._bind(self.v_serial, "usb_serial", lambda v: v.strip())

        self.v_auto = tk.BooleanVar(value=cfg["autoconnect"])
        self.v_recon = tk.BooleanVar(value=cfg["reconnect"])
        ttk.Checkbutton(box, text="Connect automatically on start", variable=self.v_auto).grid(
            row=5, column=0, columnspan=2, sticky="w", padx=6)
        ttk.Checkbutton(box, text="Reconnect if the link drops", variable=self.v_recon).grid(
            row=6, column=0, columnspan=2, sticky="w", padx=6, pady=(0, 4))
        self._bind(self.v_auto, "autoconnect", bool)
        self._bind(self.v_recon, "reconnect", bool)

        ttk.Label(tab_conn, text="USB needs Android platform-tools (adb) and USB debugging on "
                                 "the phone. It is faster than WiFi and needs no network.",
                  wraplength=450, foreground="#555").grid(row=2, column=0, sticky="w", padx=14)
        self._transport_changed()

        # ----------------------------------------------------------- switching
        box = ttk.LabelFrame(tab_switch, text="How to switch between PC and phone (each optional)")
        box.grid(row=0, column=0, sticky="ew", **pad)

        self.v_edge_on = tk.BooleanVar(value=cfg["edge_crossing"])
        ttk.Checkbutton(box, text="Screen-edge crossing (like a second monitor)",
                        variable=self.v_edge_on).grid(row=0, column=0, columnspan=3,
                                                      sticky="w", padx=6, pady=3)
        self._bind(self.v_edge_on, "edge_crossing", bool)

        self.v_edge = tk.StringVar(value=_reverse(EDGES, cfg["edge"]))
        ttk.Label(box, text="Phone sits on the ... of my screen").grid(row=1, column=0,
                                                                       sticky="w", padx=22)
        ttk.Combobox(box, textvariable=self.v_edge, values=list(EDGES), state="readonly",
                     width=8).grid(row=1, column=1, sticky="w")
        self._bind(self.v_edge, "edge", lambda v: EDGES[v])

        self.v_delay = tk.IntVar(value=cfg["edge_delay_ms"])
        ttk.Label(box, text="Rest on edge for (ms, 0 = instant)").grid(row=2, column=0,
                                                                       sticky="w", padx=22)
        ttk.Spinbox(box, from_=0, to=1000, increment=20, textvariable=self.v_delay,
                    width=6).grid(row=2, column=1, sticky="w")
        self._bind(self.v_delay, "edge_delay_ms", int)

        ttk.Separator(box).grid(row=3, column=0, columnspan=3, sticky="ew", pady=6)

        self.v_hk_on = tk.BooleanVar(value=cfg["hotkey_enabled"])
        ttk.Checkbutton(box, text="Keyboard shortcut", variable=self.v_hk_on).grid(
            row=4, column=0, sticky="w", padx=6, pady=3)
        self._bind(self.v_hk_on, "hotkey_enabled", bool)
        self.v_hk = tk.StringVar(value=cfg["hotkey"])
        self.hk_entry = ttk.Entry(box, textvariable=self.v_hk, width=18, state="readonly")
        self.hk_entry.grid(row=4, column=1, sticky="w")
        ttk.Label(box, text="<- click, then press the combo").grid(row=4, column=2,
                                                                   sticky="w", padx=6)
        self.hk_entry.bind("<KeyPress>", self._on_hotkey_key)
        self._bind(self.v_hk, "hotkey")

        self.v_btn = tk.StringVar(value=_reverse(BUTTONS, cfg["switch_button"]))
        ttk.Label(box, text="Mouse button that switches").grid(row=5, column=0,
                                                               sticky="w", padx=6, pady=3)
        ttk.Combobox(box, textvariable=self.v_btn, values=list(BUTTONS), state="readonly",
                     width=26).grid(row=5, column=1, columnspan=2, sticky="w")
        self._bind(self.v_btn, "switch_button", lambda v: BUTTONS[v])

        ttk.Label(box, text="Emergency exit (always on): Ctrl+Alt+Shift+F12",
                  foreground="#666").grid(row=6, column=0, columnspan=3,
                                          sticky="w", padx=6, pady=(6, 4))

        box = ttk.LabelFrame(tab_switch, text="Keyboard")
        box.grid(row=1, column=0, sticky="ew", **pad)
        self.v_kb = tk.BooleanVar(value=cfg["keyboard_share"])
        ttk.Checkbutton(box, text="Typing follows the mouse (type into phone text boxes)",
                        variable=self.v_kb).grid(row=0, column=0, sticky="w", padx=6, pady=3)
        self._bind(self.v_kb, "keyboard_share", bool)
        self.v_sys = tk.BooleanVar(value=cfg["route_system_keys"])
        ttk.Checkbutton(box, text="System keys act on the device the cursor is on "
                                  "(Win / Win+D = Home, Win+Tab = Recents, Alt+Tab = previous app, Win+A = Notifications, Esc = Back)",
                        variable=self.v_sys).grid(row=1, column=0, sticky="w", padx=6, pady=3)
        self._bind(self.v_sys, "route_system_keys", bool)
        ttk.Label(box, text="With this off, Win and Alt+Tab keep working on the PC even while "
                            "the cursor is on the phone.", wraplength=430,
                  foreground="#555").grid(row=2, column=0, sticky="w", padx=6, pady=(0, 4))

        # ------------------------------------------------------------- options
        box = ttk.LabelFrame(tab_opts, text="Feel")
        box.grid(row=0, column=0, sticky="ew", **pad)
        self.v_sens = tk.DoubleVar(value=cfg["sensitivity"])
        self.v_scroll = tk.DoubleVar(value=cfg["scroll_speed"])
        self.v_nat = tk.BooleanVar(value=cfg["natural_scroll"])
        ttk.Label(box, text="Pointer speed").grid(row=0, column=0, sticky="w", padx=6)
        tk.Scale(box, from_=0.3, to=3.0, resolution=0.1, orient="horizontal", length=220,
                 variable=self.v_sens).grid(row=0, column=1, padx=6)
        ttk.Label(box, text="Scroll speed").grid(row=1, column=0, sticky="w", padx=6)
        tk.Scale(box, from_=0.3, to=3.0, resolution=0.1, orient="horizontal", length=220,
                 variable=self.v_scroll).grid(row=1, column=1, padx=6)
        ttk.Checkbutton(box, text="Natural (reversed) scrolling", variable=self.v_nat).grid(
            row=2, column=0, columnspan=2, sticky="w", padx=6, pady=3)
        self._bind(self.v_sens, "sensitivity", float)
        self._bind(self.v_scroll, "scroll_speed", float)
        self._bind(self.v_nat, "natural_scroll", bool)

        box = ttk.LabelFrame(tab_opts, text="Windows")
        box.grid(row=1, column=0, sticky="ew", **pad)
        self.v_tray = tk.BooleanVar(value=cfg["minimize_to_tray"])
        ttk.Checkbutton(box, text="Closing the window hides it in the tray (keeps running)",
                        variable=self.v_tray).grid(row=0, column=0, sticky="w", padx=6, pady=3)
        self._bind(self.v_tray, "minimize_to_tray", bool)
        self.v_startup = tk.BooleanVar(value=winio.autostart_enabled())
        ttk.Checkbutton(box, text="Start MouseShare when Windows starts", variable=self.v_startup,
                        command=self._apply_autostart).grid(row=1, column=0, sticky="w",
                                                            padx=6, pady=3)
        self.v_startmin = tk.BooleanVar(value=cfg["start_minimized"])
        ttk.Checkbutton(box, text="Start hidden in the tray", variable=self.v_startmin).grid(
            row=2, column=0, sticky="w", padx=6, pady=3)
        self._bind(self.v_startmin, "start_minimized", bool)

        # --------------------------------------------------------------- status
        self.lbl_status = ttk.Label(self.root, text="", wraplength=470,
                                    font=("Segoe UI", 10, "bold"))
        self.lbl_status.grid(row=1, column=0, sticky="w", padx=14, pady=(2, 0))
        self.lbl_debug = ttk.Label(self.root, text="", wraplength=470, foreground="#555")
        self.lbl_debug.grid(row=2, column=0, sticky="w", padx=14, pady=(0, 10))

    def _on_hotkey_key(self, event):
        key = event.keysym
        if key in ("Control_L", "Control_R", "Shift_L", "Shift_R", "Alt_L", "Alt_R",
                   "Meta_L", "Meta_R", "Super_L", "Super_R", "Caps_Lock", "Num_Lock"):
            return "break"
        name = _KEYSYM_NAMES.get(key, key.lower())
        mods = []
        if event.state & 0x0004:
            mods.append("ctrl")
        if event.state & 0x20000:
            mods.append("alt")
        if event.state & 0x0001:
            mods.append("shift")
        combo = "+".join(mods + [name])
        try:
            winio.parse_hotkey(combo)
        except ValueError:
            return "break"
        self.v_hk.set(combo)
        return "break"

    def _apply_autostart(self):
        want = self.v_startup.get()
        if not winio.set_autostart(want):
            self.v_startup.set(winio.autostart_enabled())
            self.bridge._set_status("Could not change the Windows startup setting")
        self.cfg["start_with_windows"] = self.v_startup.get()
        self.cfg.save()

    # ---------------------------------------------------------------- devices
    def _transport_changed(self):
        usb = self.v_transport.get() == "usb"
        for widget in (self.lbl_ip, self.ent_ip):
            widget.grid_remove() if usb else widget.grid()
        for widget in (self.lbl_serial, self.ent_serial):
            widget.grid() if usb else widget.grid_remove()

    def scan(self):
        self.btn_scan.config(state="disabled", text="Scanning...")
        self.lbl_found.config(text="looking over USB and WiFi...")

        def work():
            try:
                found = discovery.scan_all(self.cfg["adb_path"])
            except Exception:
                found = []
            self.root.after(0, lambda: self._scan_done(found))
        threading.Thread(target=work, daemon=True).start()

    def _scan_done(self, found):
        self.devices = found
        self.tree.delete(*self.tree.get_children())
        for i, d in enumerate(found):
            if d["transport"] == "usb":
                row = ("USB", d.get("name", "phone"), d.get("serial", ""))
            else:
                row = ("WiFi", d.get("name", "phone"), "%s:%s" % (d.get("ip"), d.get("port")))
            self.tree.insert("", "end", iid=str(i), values=row)
        self.btn_scan.config(state="normal", text="Scan again")
        if found:
            self.lbl_found.config(text="%d found" % len(found))
            if not self.tree.selection():
                self.tree.selection_set("0")
        else:
            self.lbl_found.config(text="none found - enter the details below")

    def _selected_device(self):
        sel = self.tree.selection()
        if not sel:
            return None
        try:
            return self.devices[int(sel[0])]
        except (ValueError, IndexError):
            return None

    def _pick_device(self, _event=None):
        d = self._selected_device()
        if not d:
            return
        self.v_transport.set(d["transport"])
        if d["transport"] == "usb":
            self.v_serial.set(d.get("serial", ""))
        else:
            self.v_ip.set(d.get("ip", ""))
            self.v_port.set(d.get("port", 5599))
        self._transport_changed()

    def _connect_selected(self, _event=None):
        self._pick_device()
        if self.bridge.connected:
            self.bridge.disconnect()
        self._connect_async(manual=True)

    # ---------------------------------------------------------------- actions
    def toggle_connect(self):
        if self.bridge.connected:
            self.bridge.disconnect()
        else:
            self._connect_async(manual=True)

    def _connect_async(self, manual=True):
        def work():
            try:
                self.bridge.connect(manual=manual)
            except BridgeError:
                pass                       # status text already explains what went wrong
        threading.Thread(target=work, daemon=True).start()

    def _ticker(self):
        while self.running:
            time.sleep(0.008)
            try:
                self.bridge.tick()
            except Exception:
                traceback.print_exc()
                time.sleep(1)

    def _refresh(self):
        b = self.bridge
        text = b.status
        if b.connected:
            text += "   [input is on the PHONE]" if b.remote else "   [input is on the PC]"
        self.lbl_status.config(text=text)
        self.btn_connect.config(text="Disconnect" if b.connected else "Connect")
        if b.connected:
            self.lbl_debug.config(text="Last command sent to phone: %s" % b.last_sent)
        else:
            self.lbl_debug.config(text="")
        if self.tray:
            self.tray.set_tip("MouseShare - " + text[:110])
        self.root.after(250, self._refresh)

    # ------------------------------------------------------------------- tray
    def _start_tray(self):
        if win_tray is None:
            return
        self.tray = win_tray.Tray(
            on_show=lambda: self.root.after(0, self.show),
            on_connect=lambda: self.root.after(0, lambda: self._connect_async(True)),
            on_disconnect=lambda: self.root.after(0, self.bridge.disconnect),
            on_exit=lambda: self.root.after(0, self.quit),
            state=lambda: (self.bridge.connected, self.bridge.remote),
        )
        if not self.tray.start():
            self.tray = None

    def show(self):
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def hide(self):
        if self.tray:
            self.root.withdraw()
        else:
            self.root.iconify()

    def on_close(self):
        if self.cfg["minimize_to_tray"] and self.tray:
            self.hide()
        else:
            self.quit()

    def quit(self):
        self.running = False
        try:
            self.bridge.disconnect()
        finally:
            self.hooks.stop()
            if self.tray:
                self.tray.stop()
            self.root.destroy()

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    if already_running():
        ctypes.windll.user32.MessageBoxW(
            None, "MouseShare is already running - look in the notification area.",
            "MouseShare", 0x40)
        sys.exit(0)
    App(start_hidden="--tray" in sys.argv).run()
