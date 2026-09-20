"""
win_tray.py - a system tray icon using nothing but ctypes.

    tray = Tray(on_show=..., on_connect=..., on_disconnect=..., on_exit=...,
                state=lambda: (connected, remote))
    tray.start()          # runs its own hidden window + message loop in a thread
    tray.set_tip("...")   # hover text
    tray.stop()

The menu is rebuilt each time it opens, so it always shows the right
Connect/Disconnect item. Every callback runs on the tray thread - keep them
short and hand work back to the GUI thread (Tk: root.after).
"""
import ctypes
import ctypes.wintypes as wt
import os
import sys
import threading

user32 = ctypes.WinDLL("user32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)

WM_DESTROY, WM_COMMAND, WM_CLOSE = 0x0002, 0x0111, 0x0010
WM_QUIT = 0x0012
WM_TRAY = 0x0400 + 1          # WM_APP-ish message the icon sends us
WM_LBUTTONUP, WM_LBUTTONDBLCLK, WM_RBUTTONUP = 0x0202, 0x0203, 0x0205
NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP = 0x01, 0x02, 0x04
IDI_APPLICATION = 32512
IMAGE_ICON, LR_LOADFROMFILE, LR_DEFAULTSIZE = 1, 0x0010, 0x0040
MF_STRING, MF_SEPARATOR, MF_GRAYED, MF_DEFAULT = 0x0000, 0x0800, 0x0001, 0x1000
TPM_RIGHTBUTTON, TPM_RETURNCMD = 0x0002, 0x0100
CS_VREDRAW, CS_HREDRAW = 0x0001, 0x0002

ID_SHOW, ID_CONNECT, ID_DISCONNECT, ID_EXIT = 1, 2, 3, 4


class WNDCLASS(ctypes.Structure):
    _fields_ = [("style", wt.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int), ("hInstance", wt.HINSTANCE),
                ("hIcon", wt.HICON), ("hCursor", wt.HANDLE), ("hbrBackground", wt.HBRUSH),
                ("lpszMenuName", wt.LPCWSTR), ("lpszClassName", wt.LPCWSTR)]


class NOTIFYICONDATA(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("hWnd", wt.HWND), ("uID", wt.UINT),
                ("uFlags", wt.UINT), ("uCallbackMessage", wt.UINT), ("hIcon", wt.HICON),
                ("szTip", wt.WCHAR * 128), ("dwState", wt.DWORD), ("dwStateMask", wt.DWORD),
                ("szInfo", wt.WCHAR * 256), ("uVersion", wt.UINT),
                ("szInfoTitle", wt.WCHAR * 64), ("dwInfoFlags", wt.DWORD)]


user32.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.DefWindowProcW.restype = LRESULT
user32.CreateWindowExW.restype = wt.HWND
user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASS)]
shell32.Shell_NotifyIconW.argtypes = [wt.DWORD, ctypes.POINTER(NOTIFYICONDATA)]
shell32.Shell_NotifyIconW.restype = wt.BOOL
user32.TrackPopupMenu.argtypes = [wt.HMENU, wt.UINT, ctypes.c_int, ctypes.c_int,
                                  ctypes.c_int, wt.HWND, ctypes.c_void_p]
user32.TrackPopupMenu.restype = ctypes.c_int
user32.AppendMenuW.argtypes = [wt.HMENU, wt.UINT, ctypes.c_size_t, wt.LPCWSTR]
user32.GetCursorPos.argtypes = [ctypes.POINTER(wt.POINT)]
user32.LoadImageW.restype = wt.HANDLE
user32.LoadIconW.argtypes = [wt.HINSTANCE, ctypes.c_void_p]
user32.LoadIconW.restype = wt.HICON


def icon_path():
    """app.ico next to the script / inside the PyInstaller bundle, if present."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(base, "app.ico")
    return path if os.path.isfile(path) else None


class Tray:
    def __init__(self, on_show=None, on_connect=None, on_disconnect=None, on_exit=None,
                 state=None, tip="MouseShare"):
        self.on_show = on_show
        self.on_connect = on_connect
        self.on_disconnect = on_disconnect
        self.on_exit = on_exit
        self.state = state or (lambda: (False, False))
        self.tip = tip
        self.hwnd = None
        self.error = None
        self._thread = None
        self._proc = WNDPROC(self._wndproc)       # keep a reference alive
        self._nid = None
        self._ready = threading.Event()

    # ------------------------------------------------------------- lifecycle
    def start(self, timeout=3.0):
        self._thread = threading.Thread(target=self._run, daemon=True, name="mouseshare-tray")
        self._thread.start()
        self._ready.wait(timeout)
        return self.error is None

    def stop(self):
        if self.hwnd:
            user32.PostMessageW(self.hwnd, WM_CLOSE, 0, 0)

    def _run(self):
        try:
            hinst = kernel32.GetModuleHandleW(None)
            cls = WNDCLASS()
            cls.style = CS_VREDRAW | CS_HREDRAW
            cls.lpfnWndProc = self._proc
            cls.hInstance = hinst
            cls.lpszClassName = "MouseShareTrayWnd"
            if not user32.RegisterClassW(ctypes.byref(cls)) and ctypes.get_last_error() != 1410:
                raise OSError("RegisterClass failed (%d)" % ctypes.get_last_error())
            self.hwnd = user32.CreateWindowExW(0, "MouseShareTrayWnd", "MouseShare",
                                               0, 0, 0, 0, 0, None, None, hinst, None)
            if not self.hwnd:
                raise OSError("CreateWindow failed (%d)" % ctypes.get_last_error())

            path = icon_path()
            hicon = None
            if path:
                hicon = user32.LoadImageW(None, path, IMAGE_ICON, 0, 0,
                                          LR_LOADFROMFILE | LR_DEFAULTSIZE)
            if not hicon:
                hicon = user32.LoadIconW(None, ctypes.c_void_p(IDI_APPLICATION))

            nid = NOTIFYICONDATA()
            nid.cbSize = ctypes.sizeof(NOTIFYICONDATA)
            nid.hWnd = self.hwnd
            nid.uID = 1
            nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
            nid.uCallbackMessage = WM_TRAY
            nid.hIcon = hicon
            nid.szTip = self.tip[:127]
            if not shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid)):
                raise OSError("Shell_NotifyIcon failed")
            self._nid = nid
        except Exception as e:
            self.error = str(e)
            self._ready.set()
            return

        self._ready.set()
        msg = wt.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    # ------------------------------------------------------------------- api
    def set_tip(self, text):
        nid = self._nid
        if not nid:
            return
        nid.szTip = text[:127]
        nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid))

    # -------------------------------------------------------------- internal
    def _wndproc(self, hwnd, msg, wparam, lparam):
        try:
            if msg == WM_TRAY:
                event = lparam & 0xFFFF
                if event in (WM_LBUTTONUP, WM_LBUTTONDBLCLK):
                    self._call(self.on_show)
                elif event == WM_RBUTTONUP:
                    self._menu()
                return 0
            if msg == WM_COMMAND:
                cmd = wparam & 0xFFFF
                self._call({ID_SHOW: self.on_show, ID_CONNECT: self.on_connect,
                            ID_DISCONNECT: self.on_disconnect, ID_EXIT: self.on_exit}.get(cmd))
                return 0
            if msg == WM_CLOSE:
                self._remove()
                user32.DestroyWindow(hwnd)
                return 0
            if msg == WM_DESTROY:
                user32.PostQuitMessage(0)
                return 0
        except Exception:
            pass
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _call(self, fn):
        if fn:
            try:
                fn()
            except Exception:
                pass

    def _remove(self):
        if self._nid:
            shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(self._nid))
            self._nid = None

    def _menu(self):
        connected, remote = self.state()
        menu = user32.CreatePopupMenu()
        user32.AppendMenuW(menu, MF_STRING | MF_DEFAULT, ID_SHOW, "Open MouseShare")
        user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
        user32.AppendMenuW(menu, MF_STRING | (MF_GRAYED if connected else 0),
                           ID_CONNECT, "Connect")
        user32.AppendMenuW(menu, MF_STRING | (0 if connected else MF_GRAYED),
                           ID_DISCONNECT, "Disconnect")
        where = "on the phone" if remote else "on the PC"
        user32.AppendMenuW(menu, MF_STRING | MF_GRAYED, 0,
                           "Input is %s" % where if connected else "Not connected")
        user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
        user32.AppendMenuW(menu, MF_STRING, ID_EXIT, "Exit")

        pt = wt.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        user32.SetForegroundWindow(self.hwnd)          # so the menu closes on click-away
        cmd = user32.TrackPopupMenu(menu, TPM_RIGHTBUTTON | TPM_RETURNCMD,
                                    pt.x, pt.y, 0, self.hwnd, None)
        user32.DestroyMenu(menu)
        if cmd:
            user32.PostMessageW(self.hwnd, WM_COMMAND, cmd, 0)
