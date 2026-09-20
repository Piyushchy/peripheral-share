"""
bridge.py - platform independent core of MouseShare (runs on the Windows PC).

It owns:
  * the settings (Config)
  * the connection to the phone over WiFi or USB (handshake, sender/reader threads,
    heartbeat, auto-reconnect)
  * the "where is the input right now" state machine: PC <-> phone switching by
    screen edge, keyboard shortcut or an assigned mouse button
  * forwarding of keystrokes while the mouse is on the phone

Nothing in here touches the Windows API. winio.py feeds it events and gives it a
`screen` object with virtual_rect(), cursor_pos() and set_cursor().

Wire protocol (UTF-8 text, one command per line, TCP port 5599):
    PC -> phone   HELLO <pin>           first line, must match the PIN on the phone
                  P <fx> <fy>           absolute cursor position, 0..1 of phone screen
                  D <btn> / U <btn>     button down / up   (L R M X1 X2)
                  S <notches>           vertical wheel   (+ = wheel up)
                  H <notches>           horizontal wheel (+ = right)
                  KT <base64 utf8>      type this text into the focused field
                  KE <keycode> <mods>   press+release one key as a real Android key event.
                                        keycode = android.view.KeyEvent KEYCODE_*,
                                        mods = bitmask 1 shift, 2 alt, 4 ctrl
                                        (Ctrl+Backspace = "KE 67 4", Shift+Left = "KE 21 1")
                  KK <KEYNAME>          legacy special key (BACKSPACE ENTER ... ESCAPE)
                  KC <letter>           legacy ctrl+letter (a c v x)
                  NAV <BACK|HOME|RECENTS|ALTTAB|NOTIFICATIONS>
                                        ALTTAB = jump to the previous app
                  ACTIVE 1 / ACTIVE 0   show / hide the phone cursor
                  PING
    phone -> PC   OK / NO               answer to HELLO
                  INFO <w> <h> [name]   phone screen size (and device name)
                  PONG
Discovery (UDP 5598):
    PC broadcasts  MOUSESHARE?
    phone answers  MOUSESHARE <port> <w> <h> <name>
"""
import base64
import json
import os
import queue
import socket
import threading
import time

PORT = 5599
DISCOVERY_PORT = 5598

DEFAULTS = {
    # --- connection ---
    "transport": "wifi",          # wifi | usb
    "phone_ip": "",
    "port": PORT,
    "pin": "",
    "autoconnect": False,         # try to connect when the app starts
    "reconnect": True,            # keep retrying after the link drops
    "adb_path": "",               # blank = look on PATH and in the usual folders
    "usb_serial": "",             # which adb device to use when several are plugged in
    # --- ways to switch between PC and phone (each one is optional) ---
    "edge_crossing": True,
    "edge": "right",
    "edge_delay_ms": 120,
    "edge_corner_margin": 40,
    "hotkey_enabled": True,
    "hotkey": "ctrl+alt+m",
    "switch_button": "off",       # off | M | X1 | X2
    # --- keyboard ---
    "keyboard_share": True,       # typing follows the mouse
    "route_system_keys": True,    # Win/Esc/Alt+Tab act on whichever device has the cursor
    # --- feel ---
    "sensitivity": 1.0,
    "scroll_speed": 1.0,
    "natural_scroll": False,
    # --- windows behaviour ---
    "minimize_to_tray": True,
    "start_with_windows": False,
    "start_minimized": False,
}

RETURN_PUSH = 40   # phone pixels you must push past the phone's edge to jump back to the PC


class BridgeError(Exception):
    pass


def log_path():
    base = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), ".config")
    folder = os.path.join(base, "MouseShare")
    try:
        os.makedirs(folder, exist_ok=True)
    except OSError:
        pass
    return os.path.join(folder, "log.txt")


def log(message):
    """Append a line to %APPDATA%\\MouseShare\\log.txt - the first thing to check on a bug."""
    try:
        with open(log_path(), "a", encoding="utf-8") as f:
            f.write("%s  %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), message))
    except OSError:
        pass


class Config:
    def __init__(self, path=None):
        self.path = path or self.default_path()
        self.data = dict(DEFAULTS)
        self.load()

    @staticmethod
    def default_path():
        base = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), ".config")
        folder = os.path.join(base, "MouseShare")
        os.makedirs(folder, exist_ok=True)
        return os.path.join(folder, "config.json")

    def load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                saved = json.load(f)
            for key in DEFAULTS:
                if key in saved:
                    self.data[key] = saved[key]
        except (OSError, ValueError):
            pass

    def save(self):
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=2)
        except OSError:
            pass

    def __getitem__(self, key):
        return self.data[key]

    def __setitem__(self, key, value):
        self.data[key] = value


class Bridge:
    def __init__(self, cfg, screen):
        self.cfg = cfg
        self.screen = screen
        self.lock = threading.RLock()

        self.sock = None
        self._sendq = None
        self.connected = False
        self.remote = False              # True while the mouse (and keyboard) control the phone

        self.phone_w, self.phone_h = 1080, 2400
        self.phone_name = "phone"
        self.vx = self.vy = 0.0          # virtual cursor position on the phone (pixels)
        self._has_pos = False
        self.park = (0, 0)               # where the real PC cursor is parked while remote
        self.saved_pos = None

        self.armed = True
        self._edge_since = None
        self.local_down = set()
        self.remote_down = set()
        self.keys_down = set()           # vk codes swallowed while remote, so we can release them

        self._last_rx = 0.0
        self._last_ping = 0.0
        self._want_connected = False     # user asked to be connected -> auto-reconnect allowed
        self._retry_at = 0.0
        self._connecting = False
        self.status = "Not connected"
        self.last_sent = "-"             # last non-cursor command, shown in the GUI
        self.on_change = None            # optional GUI callback

    # ------------------------------------------------------------------ status
    def _set_status(self, text):
        self.status = text
        if self.on_change:
            try:
                self.on_change()
            except Exception:
                pass

    # -------------------------------------------------------------- connection
    def target(self):
        """(host, port) to dial, honouring the chosen transport. Raises BridgeError."""
        if self.cfg["transport"] == "usb":
            from discovery import adb_forward     # imported lazily: only needed for USB
            serial = str(self.cfg["usb_serial"]).strip() or None
            local_port = adb_forward(self.cfg["adb_path"], int(self.cfg["port"]), serial)
            return "127.0.0.1", local_port        # USB never needs the phone's IP
        ip = str(self.cfg["phone_ip"]).strip()
        if not ip:
            raise BridgeError("Enter the phone's IP address (or press Scan)")
        return ip, int(self.cfg["port"])

    def connect(self, manual=True):
        """Blocking. Raises BridgeError (status text is already set)."""
        with self.lock:
            if self._connecting:
                return
            self._connecting = True
        try:
            self._connect_inner(manual)
        finally:
            self._connecting = False

    def _connect_inner(self, manual):
        self.disconnect(keep_wanting=True)
        if manual:
            self._want_connected = True
        pin = str(self.cfg["pin"]).strip()
        try:
            host, port = self.target()
        except BridgeError as e:
            self._set_status(str(e))
            self._want_connected = False
            raise
        self._set_status("Connecting to %s:%d ..." % (host, port))

        s = None
        try:
            s = socket.create_connection((host, port), timeout=4)
            s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            s.settimeout(4)
            s.sendall(("HELLO %s\n" % pin).encode())
            f = s.makefile("rb")
            reply = f.readline().decode(errors="ignore").strip()
            if reply != "OK":
                raise BridgeError("Wrong PIN" if reply == "NO" else "Phone did not answer correctly")
            info = f.readline().decode(errors="ignore").split()
            if len(info) >= 3 and info[0] == "INFO":
                self.phone_w, self.phone_h = int(info[1]), int(info[2])
                if len(info) >= 4:
                    self.phone_name = " ".join(info[3:])
            s.settimeout(8)
        except BridgeError as e:
            if s:
                s.close()
            self._set_status(str(e))
            self._retry_at = time.time() + 3
            raise
        except (OSError, ValueError) as e:
            if s:
                s.close()
            self._set_status("Could not connect: %s" % e)
            self._retry_at = time.time() + 3
            raise BridgeError(str(e))

        q = queue.Queue()
        with self.lock:
            self.sock = s
            self._sendq = q
            self.connected = True
            self.remote = False
            self.local_down.clear()
            self.remote_down.clear()
            self.keys_down.clear()
            self.armed = False
            self._edge_since = None
            self._last_rx = self._last_ping = time.time()
            if not self._has_pos:
                self.vx, self.vy = self.phone_w / 2.0, self.phone_h / 2.0
                self._has_pos = True
        threading.Thread(target=self._sender, args=(s, q), daemon=True).start()
        threading.Thread(target=self._reader, args=(s, f), daemon=True).start()
        how = "USB" if self.cfg["transport"] == "usb" else host
        self._set_status("Connected to %s over %s (%dx%d)" % (self.phone_name, how, self.phone_w, self.phone_h))

    def disconnect(self, keep_wanting=False):
        if not keep_wanting:
            self._want_connected = False
        self._drop(self.sock, "Disconnected")

    def _drop(self, s, message):
        with self.lock:
            if s is None or self.sock is not s:
                return
            if self.remote:
                self.exit_remote()
            self.connected = False
            self.sock = None
            q, self._sendq = self._sendq, None
            self._retry_at = time.time() + 2
        if q:
            q.put(None)
        try:
            s.close()
        except OSError:
            pass
        self._set_status(message)

    def _reader(self, s, f):
        try:
            while True:
                line = f.readline()
                if not line:
                    break
                self._last_rx = time.time()
                parts = line.decode(errors="ignore").split()
                if len(parts) >= 3 and parts[0] == "INFO":
                    self.phone_w, self.phone_h = int(parts[1]), int(parts[2])
                    if len(parts) >= 4:
                        self.phone_name = " ".join(parts[3:])
        except (OSError, ValueError):
            pass
        self._drop(s, "Connection to phone lost")

    def _sender(self, s, q):
        try:
            while True:
                first = q.get()
                if first is None:
                    return
                batch, stop = [first], False
                while True:
                    try:
                        nxt = q.get_nowait()
                    except queue.Empty:
                        break
                    if nxt is None:
                        stop = True
                        break
                    batch.append(nxt)
                out = []
                for line in batch:              # only the newest of consecutive P lines matters
                    if out and line.startswith("P ") and out[-1].startswith("P "):
                        out[-1] = line
                    else:
                        out.append(line)
                s.sendall(("\n".join(out) + "\n").encode("utf-8"))
                if stop:
                    return
        except OSError:
            self._drop(s, "Connection to phone lost")

    def send(self, line):
        q = self._sendq
        if q is not None:
            if not line.startswith(("P ", "PING")):
                self.last_sent = line[:70]
            q.put(line)

    def _send_pos(self):
        w, h = self.phone_w, self.phone_h
        x = min(max(self.vx, 0.0), w - 1.0) / w
        y = min(max(self.vy, 0.0), h - 1.0) / h
        self.send("P %.5f %.5f" % (x, y))

    # -------------------------------------------------- switching PC <-> phone
    def toggle(self):
        with self.lock:
            if not self.connected:
                return
            if self.remote:
                self.exit_remote()
            else:
                self.enter_remote()

    def emergency_stop(self):
        with self.lock:
            if self.remote:
                self.exit_remote()

    def enter_remote(self, frac=None):
        """Send mouse (and keyboard) to the phone. frac (0..1) = where along the edge we crossed."""
        with self.lock:
            if not self.connected or self.remote:
                return
            l, t, r, b = self.screen.virtual_rect()
            w, h = self.phone_w, self.phone_h
            self.saved_pos = self.screen.cursor_pos()
            if frac is not None:
                side = self.cfg["edge"]
                if side == "right":
                    self.vx, self.vy = 0.0, frac * (h - 1)
                elif side == "left":
                    self.vx, self.vy = w - 1.0, frac * (h - 1)
                elif side == "top":
                    self.vx, self.vy = frac * (w - 1), h - 1.0
                else:
                    self.vx, self.vy = frac * (w - 1), 0.0
            self.park = ((l + r) // 2, (t + b) // 2)
            self.screen.set_cursor(*self.park)
            self.remote = True
            self._edge_since = None
            self._send_pos()
            self.send("ACTIVE 1")
        self._set_status("Input is on the PHONE")

    def exit_remote(self, frac=None):
        """Bring mouse and keyboard back to the PC."""
        with self.lock:
            if not self.remote:
                return
            self.remote = False
            for name in list(self.remote_down):
                self.send("U " + name)
            self.remote_down.clear()
            self.keys_down.clear()
            self.send("ACTIVE 0")
            l, t, r, b = self.screen.virtual_rect()
            if frac is not None:
                frac = min(max(frac, 0.0), 1.0)
                side = self.cfg["edge"]
                if side == "right":
                    pos = (r - 1, t + frac * (b - t - 1))
                elif side == "left":
                    pos = (l, t + frac * (b - t - 1))
                elif side == "top":
                    pos = (l + frac * (r - l - 1), t)
                else:
                    pos = (l + frac * (r - l - 1), b - 1)
            else:
                pos = self.saved_pos or ((l + r) // 2, (t + b) // 2)
            self.screen.set_cursor(int(pos[0]), int(pos[1]))
            self.armed = False
            self._edge_since = None
        self._set_status("Input is on the PC")

    # ------------------------------------------------------- input from winio
    def on_move(self, dx, dy):
        with self.lock:
            if not self.remote:
                return
            w, h = self.phone_w, self.phone_h
            sens = float(self.cfg["sensitivity"])
            crossing = bool(self.cfg["edge_crossing"])
            side = self.cfg["edge"]
            lo_x, hi_x, lo_y, hi_y = 0.0, w - 1.0, 0.0, h - 1.0
            if crossing:
                if side == "right":
                    lo_x = -RETURN_PUSH
                elif side == "left":
                    hi_x = w - 1.0 + RETURN_PUSH
                elif side == "top":
                    hi_y = h - 1.0 + RETURN_PUSH
                else:
                    lo_y = -RETURN_PUSH
            self.vx = min(max(self.vx + dx * sens, lo_x), hi_x)
            self.vy = min(max(self.vy + dy * sens, lo_y), hi_y)
            if crossing:
                if side == "right" and self.vx <= -RETURN_PUSH:
                    return self.exit_remote(self.vy / (h - 1.0))
                if side == "left" and self.vx >= w - 1.0 + RETURN_PUSH:
                    return self.exit_remote(self.vy / (h - 1.0))
                if side == "top" and self.vy >= h - 1.0 + RETURN_PUSH:
                    return self.exit_remote(self.vx / (w - 1.0))
                if side == "bottom" and self.vy <= -RETURN_PUSH:
                    return self.exit_remote(self.vx / (w - 1.0))
            self._send_pos()

    def mouse_button(self, name, down):
        """name: L R M X1 X2. Returns True if the event must be swallowed on the PC."""
        with self.lock:
            if not self.connected:
                return False
            switch = self.cfg["switch_button"]
            if switch != "off" and name == switch:
                if down:
                    self.toggle()
                return True
            if self.remote:
                if down:
                    self.remote_down.add(name)
                    self.send("D " + name)
                    return True
                if name in self.local_down:
                    self.local_down.discard(name)
                    return False
                self.remote_down.discard(name)
                self.send("U " + name)
                return True
            if down:
                self.local_down.add(name)
            else:
                self.local_down.discard(name)
            return False

    def mouse_wheel(self, notches, horizontal=False):
        with self.lock:
            if not (self.connected and self.remote):
                return False
            amount = notches * float(self.cfg["scroll_speed"])
            if self.cfg["natural_scroll"]:
                amount = -amount
            self.send(("H %.3f" if horizontal else "S %.3f") % amount)
            return True

    # ------------------------------------------------------------- keyboard
    def keyboard_active(self):
        return self.connected and self.remote and bool(self.cfg["keyboard_share"])

    def send_text(self, text):
        if not text:
            return
        self.send("KT " + base64.b64encode(text.encode("utf-8")).decode("ascii"))

    def send_key_event(self, keycode, mods=0):
        """A real key press (Android keycode + shift/alt/ctrl bitmask) for the phone to replay."""
        self.send("KE %d %d" % (int(keycode), int(mods)))

    def send_key(self, name):
        self.send("KK " + name)

    def send_ctrl(self, letter):
        self.send("KC " + letter)

    def send_nav(self, what):
        self.send("NAV " + what)

    # --------------------------------------------------------------- polling
    def _edge_hit(self, x, y):
        l, t, r, b = self.screen.virtual_rect()
        margin = int(self.cfg["edge_corner_margin"])
        side = self.cfg["edge"]
        if side == "right":
            hit, along, lo, hi = x >= r - 1, y, t, b - 1
        elif side == "left":
            hit, along, lo, hi = x <= l, y, t, b - 1
        elif side == "top":
            hit, along, lo, hi = y <= t, x, l, r - 1
        else:
            hit, along, lo, hi = y >= b - 1, x, l, r - 1
        if not hit or along < lo + margin or along > hi - margin:
            return False, 0.0
        return True, (along - lo) / float(max(1, hi - lo))

    def tick(self):
        """Call every ~8 ms: heartbeat, auto-reconnect and edge detection."""
        now = time.time()
        if not self.connected:
            if (self._want_connected and self.cfg["reconnect"] and not self._connecting
                    and now >= self._retry_at):
                self._retry_at = now + 3
                threading.Thread(target=self._retry, daemon=True).start()
            return
        if now - self._last_ping > 2.0:
            self._last_ping = now
            self.send("PING")
        if now - self._last_rx > 8.0:
            self._drop(self.sock, "Phone stopped responding")
            return
        if self.remote:
            return
        x, y = self.screen.cursor_pos()
        hit, frac = self._edge_hit(x, y)
        if not hit:
            self.armed = True
            self._edge_since = None
            return
        if not self.cfg["edge_crossing"] or not self.armed:
            return
        if self._edge_since is None:
            self._edge_since = now
        if (now - self._edge_since) * 1000.0 >= float(self.cfg["edge_delay_ms"]):
            self.enter_remote(frac)

    def _retry(self):
        try:
            self.connect(manual=False)
        except BridgeError:
            pass
