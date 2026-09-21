"""
keys.py - virtual-key constants, hotkey parsing and the KeyRouter.

No Windows API in here, so all of the key-routing decisions can be unit-tested on
any OS. winio.py feeds raw key events into KeyRouter.handle() and swallows the key
on the PC when it returns True.
"""

VK_BACK, VK_TAB, VK_RETURN, VK_ESCAPE, VK_SPACE = 0x08, 0x09, 0x0D, 0x1B, 0x20
VK_SHIFT, VK_CONTROL, VK_MENU = 0x10, 0x11, 0x12
VK_LSHIFT, VK_RSHIFT, VK_LCONTROL, VK_RCONTROL = 0xA0, 0xA1, 0xA2, 0xA3
VK_LMENU, VK_RMENU, VK_LWIN, VK_RWIN = 0xA4, 0xA5, 0x5B, 0x5C
VK_CAPITAL, VK_F12 = 0x14, 0x7B

CTRL_VKS = {VK_CONTROL, VK_LCONTROL, VK_RCONTROL}
ALT_VKS = {VK_MENU, VK_LMENU, VK_RMENU}
SHIFT_VKS = {VK_SHIFT, VK_LSHIFT, VK_RSHIFT}
WIN_VKS = {VK_LWIN, VK_RWIN}
MOD_VKS = CTRL_VKS | ALT_VKS | SHIFT_VKS | WIN_VKS

# Windows virtual-key -> Android KeyEvent keycode. These are sent to the phone as *real key
# events* ("KE <keycode> <mods>") so Android applies its own shortcut handling (Ctrl+Backspace
# deletes a word, Shift+arrows select, Ctrl+Z undoes...) instead of us re-implementing it.
ANDROID_KEYS = {
    VK_BACK: 67, VK_TAB: 61, VK_RETURN: 66, VK_ESCAPE: 111, VK_SPACE: 62,
    0x2E: 112,                                   # Delete       -> KEYCODE_FORWARD_DEL
    0x2D: 124,                                   # Insert
    0x25: 21, 0x26: 19, 0x27: 22, 0x28: 20,      # left up right down -> DPAD_*
    0x24: 122, 0x23: 123, 0x21: 92, 0x22: 93,    # home end pageup pagedown
    0xBA: 74, 0xBB: 70, 0xBC: 55, 0xBD: 69, 0xBE: 56, 0xBF: 76,   # ; = , - . /
    0xC0: 68, 0xDB: 71, 0xDC: 73, 0xDD: 72, 0xDE: 75,             # ` [ \ ] '
}
ANDROID_KEYS.update({0x41 + i: 29 + i for i in range(26)})        # A..Z
ANDROID_KEYS.update({0x30 + i: 7 + i for i in range(10)})         # 0..9
ANDROID_KEYS.update({0x70 + i: 131 + i for i in range(12)})       # F1..F12

# Keys that are always sent as key events (with whatever modifiers are held).
# Everything else is typed as text unless Ctrl or Alt is held (then it is a shortcut).
SPECIAL_VKS = {VK_BACK, VK_TAB, VK_RETURN, 0x2E, 0x2D, 0x25, 0x26, 0x27, 0x28,
               0x24, 0x23, 0x21, 0x22} | {0x70 + i for i in range(12)}

# Modifier keys as Android keycodes, for real key events (KD/KU). Sending Alt itself - held while
# Tab is tapped again and again - is what makes Android's own Alt+Tab switcher work.
RAW_MOD_KEYS = {VK_LSHIFT: 59, VK_RSHIFT: 60, VK_LCONTROL: 113, VK_RCONTROL: 114,
                VK_LMENU: 57, VK_RMENU: 58, VK_SHIFT: 59, VK_CONTROL: 113, VK_MENU: 57}

# modifier bitmask sent with a key event
MOD_SHIFT, MOD_ALT, MOD_CTRL = 1, 2, 4

# Win+<key> while the pointer is on the phone (system-key routing on)
WIN_COMBOS = {
    0x44: "HOME",            # Win+D  show desktop      -> phone home screen
    0x4D: "HOME",            # Win+M  minimise all      -> phone home screen
    0x09: "RECENTS",         # Win+Tab task view        -> recent apps
    0x41: "NOTIFICATIONS",   # Win+A  action centre     -> notification shade
    0x4E: "NOTIFICATIONS",   # Win+N  notifications     -> notification shade
}

_NAMED_KEYS = {
    "space": 0x20, "esc": 0x1B, "escape": 0x1B, "tab": 0x09, "enter": 0x0D, "return": 0x0D,
    "pause": 0x13, "scrolllock": 0x91, "insert": 0x2D, "delete": 0x2E, "home": 0x24,
    "end": 0x23, "pageup": 0x21, "pagedown": 0x22, "left": 0x25, "up": 0x26,
    "right": 0x27, "down": 0x28, "`": 0xC0, "-": 0xBD, "=": 0xBB, "[": 0xDB, "]": 0xDD,
    ";": 0xBA, "'": 0xDE, ",": 0xBC, ".": 0xBE, "/": 0xBF, "\\": 0xDC,
}


def parse_hotkey(text):
    """'ctrl+alt+m' -> (frozenset({'ctrl','alt'}), vk). Raises ValueError if invalid."""
    mods, vk = set(), None
    for part in text.lower().replace(" ", "").split("+"):
        if part in ("ctrl", "control"):
            mods.add("ctrl")
        elif part == "alt":
            mods.add("alt")
        elif part == "shift":
            mods.add("shift")
        elif part in ("win", "windows"):
            mods.add("win")
        elif len(part) == 1 and part.isalnum():
            vk = ord(part.upper())
        elif part.startswith("f") and part[1:].isdigit() and 1 <= int(part[1:]) <= 24:
            vk = 0x6F + int(part[1:])
        elif part in _NAMED_KEYS:
            vk = _NAMED_KEYS[part]
        else:
            raise ValueError("unknown key: %r" % part)
    if vk is None:
        raise ValueError("hotkey needs a normal key")
    return frozenset(mods), vk


class KeyRouter:
    """
    Decides, for every physical key event, whether the PC keeps it or the phone gets it.

    handle(is_down, vk, scan) -> True means "swallow it on the PC".

    Rules
      * Ctrl+Alt+Shift+F12 always returns input to the PC.
      * The switch hotkey works on both sides.
      * While input is on the phone and keyboard sharing is on, keys go to the phone.
      * System-key routing ON : Win tapped alone = Home, Win+D/M = Home, Win+Tab = Recents,
                                Win+A/N = Notifications, Esc = Back.
                                Alt+Tab is a real switcher session: the first Tab opens the
                                phone's Recents, every further Tab (Shift+Tab = backwards) slides
                                one card, and releasing Alt opens the highlighted app.
      * REAL KEY MODE (the phone's shell helper is running - see keyhelper.py): modifiers, special
        keys and shortcuts are sent as genuine key-down / key-up events (KD/KU), so Alt+Tab is
        simply Alt held and Tab tapped, handled by Android itself. Alt+Tab is then NOT emulated.
      * Otherwise shortcuts are sent as one key event carrying the held modifiers (KE).
      * Plain typing is always sent as text (KT) so the PC keyboard layout is respected.
      * System-key routing OFF: modifiers, Win+anything and Alt+Tab pass through to the PC.
      * A key whose press we swallowed has its release swallowed too (and only those), so
        the PC never sees a lone key-up and never gets a stuck modifier.
    """

    def __init__(self, bridge, translate=None):
        self.bridge = bridge
        self.translate = translate or (lambda vk, scan, mods: "")
        self.held = set()            # keys physically down right now
        self.swallowed = set()       # keys whose press we swallowed
        self.win_pending = False     # Win pressed and nothing else yet -> Home on release
        self.alt_tab = False         # an Alt+Tab switcher session is open on the phone
        self.raw_down = {}           # vk -> Android keycode we sent a real key-down for
        self._hk_cache = (None, None)

    # ------------------------------------------------------------ helpers
    def mods(self):
        m = set()
        if self.held & CTRL_VKS:
            m.add("ctrl")
        if self.held & ALT_VKS:
            m.add("alt")
        if self.held & SHIFT_VKS:
            m.add("shift")
        if self.held & WIN_VKS:
            m.add("win")
        return frozenset(m)

    def mods_mask(self, mods):
        return ((MOD_SHIFT if "shift" in mods else 0) |
                (MOD_ALT if "alt" in mods else 0) |
                (MOD_CTRL if "ctrl" in mods else 0))

    def hotkey(self):
        text = self.bridge.cfg["hotkey"]
        if self._hk_cache[0] != text:
            try:
                self._hk_cache = (text, parse_hotkey(text))
            except ValueError:
                self._hk_cache = (text, None)
        return self._hk_cache[1]

    # -------------------------------------------------------------- entry
    def handle(self, is_down, vk, scan=0):
        b = self.bridge
        repeat = is_down and vk in self.held           # key auto-repeat (still physically down)
        if is_down:
            self.held.add(vk)
        else:
            self.held.discard(vk)

        if not is_down:
            code = self.raw_down.pop(vk, None)
            if code is not None and b.connected:
                b.send_key_up(code)                       # real key-up for every real key-down
            if vk in ALT_VKS and self.alt_tab and not (self.held & ALT_VKS):
                self.alt_tab = False                      # Alt released -> open the highlighted app
                b.send_nav("ALTTABEND")
            if vk in self.swallowed:
                self.swallowed.discard(vk)
                if vk in WIN_VKS and self.win_pending and not (self.held & WIN_VKS):
                    self.win_pending = False              # Win was tapped on its own
                    if b.keyboard_active():
                        b.send_nav("HOME")
                return True
            if vk in WIN_VKS:
                self.win_pending = False
            return False

        swallow = self._down(vk, scan, repeat)
        if swallow:
            self.swallowed.add(vk)
        return swallow

    # --------------------------------------------------------------- down
    def _down(self, vk, scan, repeat=False):
        b = self.bridge
        mods = self.mods()

        if vk == VK_F12 and mods == frozenset(("ctrl", "alt", "shift")):
            b.emergency_stop()                             # built-in escape hatch, always on
            return True

        if b.cfg["hotkey_enabled"]:
            hk = self.hotkey()
            if hk and vk == hk[1] and mods == hk[0]:
                if vk not in self.swallowed:               # ignore key auto-repeat
                    b.toggle()
                return True

        if not b.keyboard_active():
            return False
        return self._forward(vk, scan, mods, repeat)

    def _forward(self, vk, scan, mods, repeat=False):
        b = self.bridge
        route = bool(b.cfg["route_system_keys"])
        raw = b.raw_active()

        if route:
            if vk in WIN_VKS:
                self.win_pending = True                    # decided on release
                return True
            if "win" in mods:
                self.win_pending = False                   # it is a combo, not a tap
                action = WIN_COMBOS.get(vk)
                if action:
                    b.send_nav(action)
                return True                                # never let Win+X reach the PC
            if vk == VK_TAB and "alt" in mods and not raw:
                if not repeat:                             # holding Tab must not spin the switcher
                    self.alt_tab = True
                    b.send_nav("ALTTABBACK" if "shift" in mods else "ALTTAB")
                return True
            if vk == VK_ESCAPE:
                b.send_nav("BACK")
                return True
        else:
            # Win, Win+X and Alt+Tab keep working on the PC while the pointer is on the phone
            if vk in MOD_VKS or "win" in mods or (vk == VK_TAB and "alt" in mods):
                if raw and vk in RAW_MOD_KEYS:
                    self._raw_down(vk, repeat)             # phone sees the modifier, PC keeps it too
                return False
            if vk == VK_ESCAPE:
                b.send_key("ESCAPE")
                return True

        if vk in MOD_VKS:
            if raw and vk in RAW_MOD_KEYS:
                self._raw_down(vk, repeat)                 # real Alt/Ctrl/Shift held on the phone
            return True                                    # (otherwise they only shape other keys)

        code = ANDROID_KEYS.get(vk)
        mask = self.mods_mask(mods)
        altgr = "ctrl" in mods and "alt" in mods           # AltGr layouts report Ctrl+Alt
        if code is not None and not altgr and (vk in SPECIAL_VKS or "ctrl" in mods or "alt" in mods):
            if raw:
                self._raw_down(vk, repeat, code)           # Alt+Tab, Ctrl+Backspace, Shift+Left ...
            else:
                b.send_key_event(code, mask)
            return True
        text = self.translate(vk, scan, mods)
        if text:
            b.send_text(text)
        elif code is not None and altgr:
            if raw:
                self._raw_down(vk, repeat, code)
            else:
                b.send_key_event(code, mask)
        return True

    def _raw_down(self, vk, repeat, code=None):
        """Send a real key-down. The matching real key-up is sent when the key is released."""
        if code is None:
            code = RAW_MOD_KEYS[vk]
            if repeat:
                return                                     # keyboards do not auto-repeat modifiers
        self.raw_down[vk] = code
        self.bridge.send_key_down(code, repeat)
