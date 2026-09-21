"""
winio.py - the only Windows-specific input file (pure ctypes, no pip packages).

  * WinScreen  - virtual desktop size, cursor get/set
  * Hooks      - global low-level mouse + keyboard hooks that feed the Bridge.
                 While the input is "on the phone" they swallow PC mouse and key
                 events and forward them over the bridge instead.
  * parse_hotkey, autostart helpers
"""
import ctypes
import ctypes.wintypes as wt
import os
import sys
import threading
import time
import traceback

from keys import (KeyRouter, parse_hotkey, VK_CAPITAL, VK_CONTROL, VK_MENU, VK_SHIFT,  # noqa: F401
                  VK_SPACE)

# Make coordinates real pixels on every monitor (must happen before any window exists).
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

LRESULT = ctypes.c_ssize_t
HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wt.WPARAM, wt.LPARAM)


class POINT(ctypes.Structure):
    _fields_ = [("x", wt.LONG), ("y", wt.LONG)]


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("pt", POINT), ("mouseData", wt.DWORD), ("flags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", wt.DWORD), ("scanCode", wt.DWORD), ("flags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wt.HINSTANCE, wt.DWORD]
user32.SetWindowsHookExW.restype = wt.HHOOK
user32.UnhookWindowsHookEx.argtypes = [wt.HHOOK]
user32.UnhookWindowsHookEx.restype = wt.BOOL
user32.CallNextHookEx.argtypes = [wt.HHOOK, ctypes.c_int, wt.WPARAM, wt.LPARAM]
user32.CallNextHookEx.restype = LRESULT
user32.GetMessageW.argtypes = [ctypes.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT]
user32.GetMessageW.restype = ctypes.c_int
user32.PostThreadMessageW.argtypes = [wt.DWORD, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.PostThreadMessageW.restype = wt.BOOL
user32.GetKeyState.argtypes = [ctypes.c_int]
user32.GetKeyState.restype = ctypes.c_short
user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
user32.SetCursorPos.restype = wt.BOOL
user32.GetCursorPos.argtypes = [ctypes.POINTER(POINT)]
user32.GetCursorPos.restype = wt.BOOL
user32.GetForegroundWindow.restype = wt.HWND
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
user32.GetWindowThreadProcessId.restype = wt.DWORD
user32.GetKeyboardLayout.argtypes = [wt.DWORD]
user32.GetKeyboardLayout.restype = wt.HKL
user32.ToUnicodeEx.argtypes = [wt.UINT, wt.UINT, ctypes.c_char * 256,
                               ctypes.c_wchar_p, ctypes.c_int, wt.UINT, wt.HKL]
user32.ToUnicodeEx.restype = ctypes.c_int
kernel32.GetModuleHandleW.argtypes = [wt.LPCWSTR]
kernel32.GetModuleHandleW.restype = wt.HMODULE
kernel32.GetCurrentThreadId.restype = wt.DWORD

WH_KEYBOARD_LL, WH_MOUSE_LL = 13, 14
WM_QUIT = 0x0012
WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN, WM_LBUTTONUP = 0x0201, 0x0202
WM_RBUTTONDOWN, WM_RBUTTONUP = 0x0204, 0x0205
WM_MBUTTONDOWN, WM_MBUTTONUP = 0x0207, 0x0208
WM_MOUSEWHEEL, WM_XBUTTONDOWN, WM_XBUTTONUP, WM_MOUSEHWHEEL = 0x020A, 0x020B, 0x020C, 0x020E
WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP = 0x0100, 0x0101, 0x0104, 0x0105
LLMHF_INJECTED = 0x01
LLKHF_INJECTED = 0x10
# --------------------------------------------------------------------- screen
class WinScreen:
    def virtual_rect(self):
        gm = user32.GetSystemMetrics
        left, top, w, h = gm(76), gm(77), gm(78), gm(79)   # SM_(X|Y|CX|CY)VIRTUALSCREEN
        return left, top, left + w, top + h

    def cursor_pos(self):
        p = POINT()
        user32.GetCursorPos(ctypes.byref(p))
        return p.x, p.y

    def set_cursor(self, x, y):
        user32.SetCursorPos(int(x), int(y))


# ------------------------------------------------------------------ autostart
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_NAME = "MouseShare"


def launch_command():
    """How Windows should start us: the frozen exe, or pythonw + app.py."""
    if getattr(sys, "frozen", False):
        return '"%s" --tray' % sys.executable
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app.py")
    pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    exe = pyw if os.path.isfile(pyw) else sys.executable
    return '"%s" "%s" --tray' % (exe, script)


def autostart_enabled():
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, RUN_NAME)
        return True
    except Exception:
        return False


def set_autostart(enabled):
    """Returns True on success."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            if enabled:
                winreg.SetValueEx(key, RUN_NAME, 0, winreg.REG_SZ, launch_command())
            else:
                try:
                    winreg.DeleteValue(key, RUN_NAME)
                except FileNotFoundError:
                    pass
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------- hooks
class Hooks:
    def __init__(self, bridge):
        self.bridge = bridge
        self.error = None
        self._tid = None
        self._mouse_proc = HOOKPROC(self._on_mouse)      # keep references alive!
        self._kbd_proc = HOOKPROC(self._on_key)
        self.router = KeyRouter(bridge, translate=self._translate)
        self._last_err = 0.0

    def start(self):
        threading.Thread(target=self._run, daemon=True, name="mouseshare-hooks").start()

    def stop(self):
        if self._tid:
            user32.PostThreadMessageW(self._tid, WM_QUIT, 0, 0)

    def _run(self):
        self._tid = kernel32.GetCurrentThreadId()
        hmod = kernel32.GetModuleHandleW(None)
        mh = user32.SetWindowsHookExW(WH_MOUSE_LL, self._mouse_proc, hmod, 0)
        kh = user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._kbd_proc, hmod, 0)
        if not mh or not kh:
            self.error = "Could not install input hooks (error %d)" % ctypes.get_last_error()
            self.bridge._set_status(self.error)
            return
        msg = wt.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        user32.UnhookWindowsHookEx(mh)
        user32.UnhookWindowsHookEx(kh)

    def _log_error(self):
        now = time.time()
        if now - self._last_err > 5:
            self._last_err = now
            traceback.print_exc()
            try:
                from bridge import log
                log("hook error:\n" + traceback.format_exc())
            except Exception:
                pass

    # ---- mouse
    def _on_mouse(self, code, wparam, lparam):
        if code == 0:
            try:
                info = ctypes.cast(lparam, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
                if not (info.flags & LLMHF_INJECTED) and self._handle_mouse(wparam, info):
                    return 1
            except Exception:
                self._log_error()
        return user32.CallNextHookEx(None, code, wparam, lparam)

    def _handle_mouse(self, msg, info):
        b = self.bridge
        if msg == WM_MOUSEMOVE:
            if not b.remote:
                return False
            px, py = b.park
            dx, dy = info.pt.x - px, info.pt.y - py
            if (dx or dy) and abs(dx) < 4000 and abs(dy) < 4000:
                b.on_move(dx, dy)
            return True                                    # cursor stays parked

        name = down = None
        if msg in (WM_LBUTTONDOWN, WM_LBUTTONUP):
            name, down = "L", msg == WM_LBUTTONDOWN
        elif msg in (WM_RBUTTONDOWN, WM_RBUTTONUP):
            name, down = "R", msg == WM_RBUTTONDOWN
        elif msg in (WM_MBUTTONDOWN, WM_MBUTTONUP):
            name, down = "M", msg == WM_MBUTTONDOWN
        elif msg in (WM_XBUTTONDOWN, WM_XBUTTONUP):
            name = "X1" if ((info.mouseData >> 16) & 0xFFFF) == 1 else "X2"
            down = msg == WM_XBUTTONDOWN
        if name:
            return b.mouse_button(name, down)

        if msg in (WM_MOUSEWHEEL, WM_MOUSEHWHEEL):
            delta = ctypes.c_short((info.mouseData >> 16) & 0xFFFF).value
            return b.mouse_wheel(delta / 120.0, horizontal=(msg == WM_MOUSEHWHEEL))
        return False

    # ---- keyboard
    def _on_key(self, code, wparam, lparam):
        if code == 0:
            try:
                info = ctypes.cast(lparam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                if not (info.flags & LLKHF_INJECTED) and self._handle_key(wparam, info):
                    return 1
            except Exception:
                self._log_error()
        return user32.CallNextHookEx(None, code, wparam, lparam)

    def _translate(self, vk, scan, mods):
        """vk+scancode -> the character this key would type, or '' (dead keys, F-keys...)."""
        state = (ctypes.c_char * 256)()
        if "shift" in mods:
            state[VK_SHIFT] = b"\x80"
        if user32.GetKeyState(VK_CAPITAL) & 1:
            state[VK_CAPITAL] = b"\x01"
        if "ctrl" in mods and "alt" in mods:                  # AltGr
            state[VK_CONTROL] = b"\x80"
            state[VK_MENU] = b"\x80"
        try:
            hwnd = user32.GetForegroundWindow()
            tid = user32.GetWindowThreadProcessId(hwnd, None) if hwnd else 0
            hkl = user32.GetKeyboardLayout(tid)
            buf = ctypes.create_unicode_buffer(8)
            n = user32.ToUnicodeEx(vk, scan, state, buf, len(buf), 0, hkl)
            text = buf[:n] if n > 0 else ""
        except Exception:
            self._log_error()
            text = ""
        if not text:
            text = self._fallback_char(vk, mods)
        return "".join(ch for ch in text if ch >= " ")

    def _fallback_char(self, vk, mods):
        """If the layout lookup fails, at least get US letters, digits and space through."""
        shifted = "shift" in mods
        if vk == VK_SPACE:
            return " "
        if 0x41 <= vk <= 0x5A:
            caps = bool(user32.GetKeyState(VK_CAPITAL) & 1)
            return chr(vk) if shifted != caps else chr(vk + 32)
        if 0x30 <= vk <= 0x39 and not shifted:
            return chr(vk)
        if 0x60 <= vk <= 0x69:                                 # numpad digits
            return chr(vk - 0x60 + 0x30)
        return ""

    def _handle_key(self, msg, info):
        if msg in (WM_KEYDOWN, WM_SYSKEYDOWN):
            return self.router.handle(True, info.vkCode, info.scanCode)
        if msg in (WM_KEYUP, WM_SYSKEYUP):
            return self.router.handle(False, info.vkCode, info.scanCode)
        return False
