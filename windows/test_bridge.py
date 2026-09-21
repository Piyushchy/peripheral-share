"""
Headless self-test: fake phone + fake screen, no Windows needed.
Run:  python test_bridge.py
"""
import base64
import os
import socket
import tempfile
import threading
import time

from bridge import Bridge, BridgeError, Config


class FakeScreen:
    def __init__(self):
        self.rect = (0, 0, 1920, 1080)
        self.pos = (960, 540)

    def virtual_rect(self):
        return self.rect

    def cursor_pos(self):
        return self.pos

    def set_cursor(self, x, y):
        self.pos = (x, y)


class FakePhone(threading.Thread):
    def __init__(self, pin="123456", w=1080, h=2400):
        super().__init__(daemon=True)
        self.pin, self.w, self.h = pin, w, h
        self.srv = socket.socket()
        self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(2)
        self.port = self.srv.getsockname()[1]
        self.lines = []
        self.start()

    def run(self):
        while True:
            try:
                c, _ = self.srv.accept()
            except OSError:
                return
            threading.Thread(target=self.handle, args=(c,), daemon=True).start()

    def handle(self, c):
        f = c.makefile("rb")
        first = f.readline().decode().split()
        if len(first) < 2 or first[1] != self.pin:
            c.sendall(b"NO\n")
            c.close()
            return
        self.conn = c
        c.sendall(("OK\nINFO %d %d\n" % (self.w, self.h)).encode())
        for raw in f:
            line = raw.decode().strip()
            if line == "PING":
                c.sendall(b"PONG\n")
            else:
                self.lines.append(line)

    def push(self, line):
        """Send a line from the 'phone' to the PC (e.g. RAWKEYS 1 ...)."""
        self.conn.sendall((line + "\n").encode())

    def wait_for(self, prefix, timeout=2.0):
        end = time.time() + timeout
        while time.time() < end:
            for line in self.lines:
                if line.startswith(prefix):
                    return line
            time.sleep(0.01)
        raise AssertionError("phone never received %r; got %r" % (prefix, self.lines))


def make(phone, **overrides):
    cfg = Config(os.path.join(tempfile.mkdtemp(), "c.json"))
    cfg["phone_ip"], cfg["port"], cfg["pin"] = "127.0.0.1", phone.port, "123456"
    for k, v in overrides.items():
        cfg[k] = v
    screen = FakeScreen()
    return Bridge(cfg, screen), screen


def test_pin():
    phone = FakePhone()
    b, _ = make(phone)
    b.cfg["pin"] = "000000"
    try:
        b.connect()
        raise AssertionError("wrong PIN accepted")
    except BridgeError:
        assert b.status == "Wrong PIN", b.status
    b.cfg["pin"] = "123456"
    b.connect()
    assert b.connected and (b.phone_w, b.phone_h) == (1080, 2400)
    b.disconnect()
    print("ok pin")


def test_edge_crossing_and_return():
    phone = FakePhone()
    b, screen = make(phone, edge_delay_ms=0)
    b.connect()
    b.armed = True
    screen.pos = (1919, 540)                     # touch the right edge, middle
    b.tick()
    assert b.remote, "edge should switch to phone"
    line = phone.wait_for("P ")
    fx, fy = map(float, line.split()[1:])
    assert fx == 0.0 and abs(fy - 0.5) < 0.01, line          # enters at phone's left edge, same height
    phone.wait_for("ACTIVE 1")
    assert screen.pos == (960, 540)                            # PC cursor parked at centre

    b.on_move(300, 0)                                           # move into the phone
    assert b.remote and abs(b.vx - 300) < 1e-6
    assert b.mouse_button("L", True) is True                    # click goes to phone
    assert b.mouse_button("L", False) is True
    phone.wait_for("D L")
    phone.wait_for("U L")
    assert b.mouse_wheel(1.0) is True
    phone.wait_for("S 1.000")

    b.on_move(-300, 0)                                          # back at the left edge, not yet out
    assert b.remote
    b.on_move(-50, 0)                                           # push past the edge -> back to PC
    assert not b.remote, "pushing past the edge should return to the PC"
    assert screen.pos[0] == 1919, screen.pos                    # lands on PC's right edge
    phone.wait_for("ACTIVE 0")
    b.tick()
    assert not b.remote, "must not bounce straight back (edge is disarmed)"
    b.disconnect()
    print("ok edge crossing + return")


def test_edge_disabled_hotkey_only():
    phone = FakePhone()
    b, screen = make(phone, edge_crossing=False, edge_delay_ms=0)
    b.connect()
    b.armed = True
    screen.pos = (1919, 540)
    b.tick()
    assert not b.remote, "edge crossing is disabled"
    screen.pos = (500, 300)
    b.toggle()                                                  # what the hotkey does
    assert b.remote
    b.on_move(-5000, 0)                                         # slam into the phone's left edge
    assert b.remote and b.vx == 0.0, "no edge return when edge crossing is off"
    b.toggle()
    assert not b.remote and screen.pos == (500, 300), screen.pos   # cursor restored where it was
    b.disconnect()
    print("ok hotkey-only mode")


def test_switch_button():
    phone = FakePhone()
    b, _ = make(phone, edge_crossing=False, hotkey_enabled=False, switch_button="X1")
    assert b.mouse_button("X1", True) is False                  # not connected: button works normally
    b.connect()
    assert b.mouse_button("X1", True) is True and b.remote
    assert b.mouse_button("X1", False) is True                  # release swallowed
    assert b.mouse_button("X1", True) is True and not b.remote
    b.mouse_button("X1", False)
    assert b.mouse_button("M", True) is False                   # other buttons untouched on the PC
    b.disconnect()
    print("ok mouse-button switch")


def test_drag_across_edge():
    phone = FakePhone()
    b, screen = make(phone, edge_delay_ms=0)
    b.connect()
    b.armed = True
    assert b.mouse_button("L", True) is False                   # pressed on PC (dragging a window)
    screen.pos = (1919, 300)
    b.tick()
    assert b.remote
    assert b.mouse_button("L", False) is False, "PC must still see the release"
    b.disconnect()
    print("ok drag across edge")


def test_disconnect_returns_mouse():
    phone = FakePhone()
    b, screen = make(phone)
    b.connect()
    b.toggle()
    assert b.remote
    b._drop(b.sock, "test")
    assert not b.remote and not b.connected
    print("ok disconnect returns mouse")


def test_sensitivity_and_natural_scroll():
    phone = FakePhone()
    b, _ = make(phone, sensitivity=2.0, natural_scroll=True, scroll_speed=1.5)
    b.connect()
    b.toggle()
    b.vx = b.vy = 100.0
    b.on_move(10, -10)
    assert (b.vx, b.vy) == (120.0, 80.0)
    b.mouse_wheel(2.0)
    phone.wait_for("S -3.000")
    b.disconnect()
    print("ok sensitivity + natural scroll")




def test_keyboard_forwarding():
    phone = FakePhone()
    b, _ = make(phone)
    b.connect()
    assert not b.keyboard_active(), "keyboard stays on the PC until the mouse moves over"
    b.toggle()
    assert b.keyboard_active()
    b.send_text("Hi Piyush")
    line = phone.wait_for("KT ")
    assert base64.b64decode(line.split()[1]).decode() == "Hi Piyush"
    b.send_key("BACKSPACE")
    phone.wait_for("KK BACKSPACE")
    b.send_ctrl("v")
    phone.wait_for("KC v")
    b.send_key_event(67, 4)
    _expect(phone, "KE 67 4")
    b.send_nav("HOME")
    phone.wait_for("NAV HOME")
    b.cfg["keyboard_share"] = False
    assert not b.keyboard_active(), "typing must stay on the PC when sharing is off"
    b.disconnect()
    print("ok keyboard forwarding")


def test_discovery_parsing():
    import discovery
    info = discovery.parse_reply("MOUSESHARE 5599 1080 2400 Pixel 7a")
    assert info == {"port": 5599, "w": 1080, "h": 2400, "name": "Pixel 7a"}, info
    assert discovery.parse_reply("hello") is None
    assert discovery.parse_reply("MOUSESHARE x y z") is None
    devices = discovery.parse_devices(
        "List of devices attached\n"
        "R58M12ABCDE            device usb:1-3 product:a52q model:SM_A525F\n"
        "emulator-5554          offline\n"
        "9A271FFAZ004TH         device product:redfin model:Pixel_5\n")
    assert [d["name"] for d in devices] == ["SM A525F", "Pixel 5"], devices
    assert devices[0]["serial"] == "R58M12ABCDE"
    print("ok discovery parsing")


def test_reconnect_after_drop():
    phone = FakePhone()
    b, _ = make(phone)
    b.connect()
    b._drop(b.sock, "lost")
    assert not b.connected and b._want_connected, "should still want to be connected"
    b._retry_at = 0
    b.tick()
    end = time.time() + 3
    while time.time() < end and not b.connected:
        time.sleep(0.05)
    assert b.connected, "auto-reconnect did not come back"
    b.disconnect()
    assert not b._want_connected, "a manual disconnect must stop reconnecting"
    b.tick()
    assert not b.connected
    print("ok auto-reconnect")


def test_hotkey_parsing():
    import keys as w
    for combo, expect in [("ctrl+alt+m", (frozenset({"ctrl", "alt"}), 0x4D)),
                          ("f9", (frozenset(), 0x78)),
                          ("ctrl+shift+space", (frozenset({"ctrl", "shift"}), 0x20))]:
        assert w.parse_hotkey(combo) == expect, combo
    for bad in ("ctrl+alt", "nope", ""):
        try:
            w.parse_hotkey(bad)
            raise AssertionError("accepted %r" % bad)
        except ValueError:
            pass
    print("ok hotkey parsing")


# ---------------------------------------------------------------- key routing
def _expect(phone, line, timeout=2.0):
    """Wait for exactly this line (wait_for only matches prefixes)."""
    end = time.time() + timeout
    while time.time() < end:
        if line in phone.lines:
            return
        time.sleep(0.01)
    raise AssertionError("phone never received %r; got %r" % (line, phone.lines))


def _router(phone, **overrides):
    from keys import KeyRouter
    b, screen = make(phone, **overrides)
    b.connect()
    b.toggle()                                       # mouse is now on the phone
    typed = lambda vk, scan, mods: chr(vk + 32) if 0x41 <= vk <= 0x5A else ""
    return b, KeyRouter(b, translate=typed)


def _press(r, *vks):
    """Press then release keys in order (release in reverse). Returns swallow flag per press."""
    result = [r.handle(True, vk) for vk in vks]
    for vk in reversed(vks):
        r.handle(False, vk)
    return result


VK_LWIN, VK_D, VK_TAB, VK_LALT, VK_LCTRL, VK_ESC = 0x5B, 0x44, 0x09, 0xA4, 0xA2, 0x1B
VK_BACK = 0x08


def test_win_d_goes_home_on_phone():
    phone = FakePhone()
    b, r = _router(phone)
    swallowed = _press(r, VK_LWIN, VK_D)
    assert swallowed == [True, True], "PC must not see Win or D"
    phone.wait_for("NAV HOME")
    assert phone.lines.count("NAV HOME") == 1, "Win+D must send Home once, not also on Win release"
    b.disconnect()
    print("ok Win+D -> phone home")


def test_win_tap_and_other_combos():
    phone = FakePhone()
    b, r = _router(phone)
    _press(r, VK_LWIN)                               # Win tapped on its own
    phone.wait_for("NAV HOME")
    phone.lines.clear()
    _press(r, VK_LWIN, VK_TAB)
    phone.wait_for("NAV RECENTS")
    _press(r, VK_LWIN, 0x41)                         # Win+A
    phone.wait_for("NAV NOTIFICATIONS")
    phone.lines.clear()
    _press(r, VK_LALT, VK_TAB)                       # Alt+Tab = app switcher session
    _expect(phone, "NAV ALTTAB")
    _expect(phone, "NAV ALTTABEND")                  # Alt released -> open the highlighted app
    assert "NAV RECENTS" not in phone.lines, "Alt+Tab is not the same as Win+Tab"
    phone.lines.clear()
    assert _press(r, VK_ESC) == [True]
    phone.wait_for("NAV BACK")
    assert not r.swallowed and not r.win_pending, "no key may be left 'stuck'"
    b.disconnect()
    print("ok Win tap / Win+Tab / Win+A / Alt+Tab / Esc")


def test_unknown_win_combo_still_stays_off_the_pc():
    phone = FakePhone()
    b, r = _router(phone)
    assert _press(r, VK_LWIN, 0x45) == [True, True]  # Win+E would open Explorer on the PC
    time.sleep(0.1)
    assert not any(l.startswith("NAV") for l in phone.lines)
    b.disconnect()
    print("ok unmapped Win combo swallowed silently")


def test_system_keys_off_leaves_win_to_the_pc():
    phone = FakePhone()
    b, r = _router(phone, route_system_keys=False)
    assert _press(r, VK_LWIN, VK_D) == [False, False], "Win+D must reach the PC"
    assert _press(r, VK_LALT, VK_TAB) == [False, False], "Alt+Tab must reach the PC"
    assert _press(r, VK_LCTRL) == [False], "modifiers pass through so combos are complete"
    time.sleep(0.1)
    assert not any(l.startswith("NAV") for l in phone.lines)
    assert _press(r, 0x41) == [True], "ordinary typing still goes to the phone"
    phone.wait_for("KT ")
    b.disconnect()
    print("ok system keys off -> Win stays on PC")


def test_typing_and_no_stuck_modifiers():
    phone = FakePhone()
    b, r = _router(phone)
    r.handle(True, VK_LCTRL)
    r.handle(True, 0x56)                              # Ctrl+V
    _expect(phone, "KE 50 4")                         # V = keycode 50, ctrl = 4
    b.exit_remote()                                   # pointer leaves while Ctrl is still held
    assert r.handle(False, 0x56) is True, "V press was swallowed, so its release must be too"
    assert r.handle(False, VK_LCTRL) is True, "Ctrl press was swallowed, so its release must be too"
    assert not r.swallowed
    b.disconnect()
    print("ok no stuck modifiers")


def test_keys_stay_on_pc_when_pointer_is_on_pc():
    phone = FakePhone()
    from keys import KeyRouter
    b, _ = make(phone)
    b.connect()                                       # connected, pointer still on the PC
    r = KeyRouter(b)
    assert _press(r, VK_LWIN, VK_D) == [False, False]
    assert _press(r, 0x41) == [False]
    b.disconnect()
    print("ok keys untouched while on PC")


def test_hotkey_and_emergency_exit_through_router():
    phone = FakePhone()
    b, r = _router(phone)
    assert b.remote
    r.handle(True, VK_LCTRL); r.handle(True, VK_LALT)
    assert r.handle(True, 0x4D) is True               # Ctrl+Alt+M
    assert not b.remote, "hotkey must toggle back to the PC while typing on the phone"
    assert r.handle(True, 0x4D) is True and not b.remote, "auto-repeat must not re-toggle"
    r.handle(False, 0x4D); r.handle(False, VK_LALT); r.handle(False, VK_LCTRL)
    b.toggle()
    assert b.remote
    for vk in (VK_LCTRL, VK_LALT, 0xA0):
        r.handle(True, vk)
    assert r.handle(True, 0x7B) is True and not b.remote, "Ctrl+Alt+Shift+F12 escape hatch"
    b.disconnect()
    print("ok hotkey + emergency exit")



def test_ctrl_backspace_and_friends_carry_their_modifiers():
    phone = FakePhone()
    b, r = _router(phone)
    assert _press(r, VK_BACK) == [True]
    _expect(phone, "KE 67 0")                         # plain Backspace is a real key event too
    _press(r, VK_LCTRL, VK_BACK)
    _expect(phone, "KE 67 4")                         # Ctrl+Backspace = delete word (Android does it)
    _press(r, VK_LCTRL, 0x2E)
    _expect(phone, "KE 112 4")                        # Ctrl+Delete
    _press(r, VK_LCTRL, 0x25)
    _expect(phone, "KE 21 4")                         # Ctrl+Left = word jump
    _press(r, 0xA0, 0x27)
    _expect(phone, "KE 22 1")                         # Shift+Right = extend selection
    _press(r, VK_LCTRL, 0xA0, 0x25)
    _expect(phone, "KE 21 5")                         # Ctrl+Shift+Left
    _press(r, 0xA0, VK_TAB)
    _expect(phone, "KE 61 1")                         # Shift+Tab
    assert not r.swallowed and not r.held
    b.disconnect()
    print("ok Ctrl+Backspace / Ctrl+Del / Ctrl+arrows / Shift+arrows")


def test_ctrl_shortcuts_beyond_acxv():
    phone = FakePhone()
    b, r = _router(phone)
    for vk, expect in ((0x5A, "KE 54 4"),             # Ctrl+Z undo
                       (0x59, "KE 53 4"),             # Ctrl+Y redo
                       (0x46, "KE 34 4"),             # Ctrl+F
                       (0x31, "KE 8 4"),              # Ctrl+1
                       (0xBB, "KE 70 4"),             # Ctrl+=  (zoom in)
                       (0xBD, "KE 69 4"),             # Ctrl+-  (zoom out)
                       (0x20, "KE 62 4")):            # Ctrl+Space
        _press(r, VK_LCTRL, vk)
        _expect(phone, expect)
    _press(r, VK_LALT, 0x25)
    _expect(phone, "KE 21 2")                         # Alt+Left (browser back)
    _press(r, 0x70)
    _expect(phone, "KE 131 0")                        # F1
    b.disconnect()
    print("ok Ctrl/Alt shortcuts and F-keys")


def test_plain_and_shifted_typing_stays_text():
    phone = FakePhone()
    b, r = _router(phone)
    _press(r, 0xA0, 0x41)                             # Shift+A -> text, layout handled by the PC
    phone.wait_for("KT ")
    assert not any(l.startswith("KE") for l in phone.lines)
    b.disconnect()
    print("ok typing stays text")


def test_altgr_text_is_not_a_shortcut():
    from keys import KeyRouter
    phone = FakePhone()
    b, screen = make(phone)
    b.connect()
    b.toggle()
    r = KeyRouter(b, translate=lambda vk, scan, mods: "@" if vk == 0x51 else "")
    r.handle(True, VK_LCTRL); r.handle(True, 0xA5)    # AltGr = Ctrl + right Alt
    r.handle(True, 0x51)
    line = phone.wait_for("KT ")
    assert base64.b64decode(line.split()[1]).decode() == "@"
    assert not any(l.startswith("KE") for l in phone.lines)
    b.disconnect()
    print("ok AltGr characters")


def test_alt_tab_ignores_key_repeat():
    phone = FakePhone()
    b, r = _router(phone)
    r.handle(True, VK_LALT)
    for _ in range(5):                                # Tab held down -> repeated key-downs
        assert r.handle(True, VK_TAB) is True
    r.handle(False, VK_TAB); r.handle(False, VK_LALT)
    time.sleep(0.1)
    assert phone.lines.count("NAV ALTTAB") == 1, phone.lines
    b.disconnect()
    print("ok Alt+Tab once per press")


def test_alt_tab_session_cycles_until_alt_is_released():
    phone = FakePhone()
    b, r = _router(phone)
    r.handle(True, VK_LALT)
    r.handle(True, VK_TAB); r.handle(False, VK_TAB)  # 1st Tab: open switcher
    r.handle(True, VK_TAB); r.handle(False, VK_TAB)  # 2nd Tab: next app
    r.handle(True, 0xA0)                              # hold Shift
    r.handle(True, VK_TAB); r.handle(False, VK_TAB)  # Shift+Alt+Tab: back one
    r.handle(False, 0xA0)
    time.sleep(0.15)
    assert "NAV ALTTABEND" not in phone.lines, "the app must not open while Alt is still held"
    r.handle(False, VK_LALT)
    _expect(phone, "NAV ALTTABEND")
    seq = [l for l in phone.lines if l.startswith("NAV ALTTAB")]
    assert seq == ["NAV ALTTAB", "NAV ALTTAB", "NAV ALTTABBACK", "NAV ALTTABEND"], seq
    assert not r.alt_tab and not r.swallowed and not r.held
    # a plain Alt tap afterwards must not send another END
    n = len(phone.lines)
    r.handle(True, VK_LALT); r.handle(False, VK_LALT)
    time.sleep(0.1)
    assert len(phone.lines) == n
    b.disconnect()
    print("ok Alt+Tab session: open, cycle, Shift reverses, release opens")


# ------------------------------------------------------------------ real keys (KD/KU)
def _raw_router(phone, **overrides):
    """A router whose phone has announced that its real-key helper is running."""
    b, r = _router(phone, **overrides)
    phone.push("RAWKEYS 1 FakeSink")
    end = time.time() + 2
    while not b.raw_keys and time.time() < end:
        time.sleep(0.01)
    assert b.raw_keys, "RAWKEYS 1 was not picked up"
    phone.wait_for("ACTIVE 1")
    phone.lines.clear()
    return b, r


def _settle(phone, n, timeout=2.0):
    end = time.time() + timeout
    while len(phone.lines) < n and time.time() < end:
        time.sleep(0.01)
    time.sleep(0.05)
    return list(phone.lines)


def test_rawkeys_announcement():
    phone = FakePhone()
    b, r = _router(phone)
    assert not b.raw_keys and not b.raw_active()
    phone.push("RAWKEYS 1 InputManagerGlobal")
    end = time.time() + 2
    while not b.raw_keys and time.time() < end:
        time.sleep(0.01)
    assert b.raw_keys and b.raw_info == "InputManagerGlobal" and b.raw_active()
    b.cfg["real_keys"] = False
    assert not b.raw_active(), "the setting must be able to turn real keys off"
    b.cfg["real_keys"] = True
    phone.push("RAWKEYS 0 helper stopped")
    end = time.time() + 2
    while b.raw_keys and time.time() < end:
        time.sleep(0.01)
    assert not b.raw_keys and b.raw_info == "helper stopped"
    b.disconnect()
    print("ok RAWKEYS announcement")


def test_real_alt_tab_is_alt_held_and_tab_tapped():
    phone = FakePhone()
    b, r = _raw_router(phone)
    assert r.handle(True, VK_LALT) is True            # Alt swallowed on the PC...
    for _ in range(3):
        assert r.handle(True, VK_TAB) is True
        r.handle(False, VK_TAB)
    r.handle(False, VK_LALT)
    lines = _settle(phone, 8)
    assert lines == ["KD 57 0", "KD 61 0", "KU 61", "KD 61 0", "KU 61", "KD 61 0", "KU 61", "KU 57"], lines
    assert not any(l.startswith("NAV") for l in lines), "real Alt+Tab must not be emulated"
    assert not r.raw_down and not r.swallowed and not r.held
    b.disconnect()
    print("ok real Alt+Tab: Alt held, Tab tapped repeatedly, Alt released")


def test_real_keys_ctrl_backspace_shift_tab_and_repeat():
    phone = FakePhone()
    b, r = _raw_router(phone)
    _press(r, VK_LCTRL, VK_BACK)
    _press(r, 0xA0, VK_TAB)                            # Shift+Tab
    r.handle(True, VK_BACK); r.handle(True, VK_BACK); r.handle(True, VK_BACK); r.handle(False, VK_BACK)
    r.handle(True, VK_LALT); r.handle(True, VK_LALT); r.handle(True, VK_LALT); r.handle(False, VK_LALT)
    lines = _settle(phone, 15)
    assert lines == [
        "KD 113 0", "KD 67 0", "KU 67", "KU 113",      # Ctrl+Backspace
        "KD 59 0", "KD 61 0", "KU 61", "KU 59",        # Shift+Tab
        "KD 67 0", "KD 67 1", "KD 67 1", "KU 67",      # held Backspace auto-repeats
        "KD 57 0", "KU 57",                            # a modifier does not auto-repeat
    ], lines
    assert not r.raw_down
    b.disconnect()
    print("ok real Ctrl+Backspace / Shift+Tab / auto-repeat")


def test_real_keys_typing_stays_text_and_win_keys_stay_navigation():
    phone = FakePhone()
    b, r = _raw_router(phone)
    _press(r, 0xA0, 0x41)                              # Shift+A: Shift is real, the letter is text
    _press(r, VK_LWIN, VK_D)                           # Win+D still = phone Home
    r.handle(True, VK_ESC); r.handle(False, VK_ESC)    # Esc still = Back
    lines = _settle(phone, 6)
    assert lines[0] == "KD 59 0" and lines[1].startswith("KT ") and lines[2] == "KU 59", lines
    assert "NAV HOME" in lines and "NAV BACK" in lines, lines
    assert not any(l.startswith("KD 61") for l in lines)
    b.disconnect()
    print("ok typing stays text, Win/Esc stay navigation")


def test_real_keys_off_in_settings_uses_old_path():
    phone = FakePhone()
    b, r = _raw_router(phone, real_keys=False)
    _press(r, VK_LCTRL, VK_BACK)
    _press(r, VK_LALT, VK_TAB)
    lines = _settle(phone, 3)
    assert "KE 67 4" in lines and "NAV ALTTAB" in lines, lines
    assert not any(l.startswith(("KD", "KU")) for l in lines), lines
    b.disconnect()
    print("ok real_keys=false keeps the old behaviour")


def test_real_keys_with_system_routing_off():
    phone = FakePhone()
    b, r = _raw_router(phone, route_system_keys=False)
    assert r.handle(True, VK_LALT) is False            # PC keeps Alt (and Alt+Tab) ...
    assert r.handle(True, VK_TAB) is False             # ... so Windows switches windows
    r.handle(False, VK_TAB); r.handle(False, VK_LALT)
    lines = _settle(phone, 2)
    assert lines == ["KD 57 0", "KU 57"], lines        # the phone only ever sees a lone Alt
    b.disconnect()
    print("ok system keys off: Alt+Tab stays on the PC")


def test_pointer_returning_to_pc_releases_real_keys():
    phone = FakePhone()
    b, r = _raw_router(phone)
    r.handle(True, VK_LALT)                            # Alt held...
    r.handle(True, VK_TAB)
    b.exit_remote()                                    # ...pointer goes back to the PC
    lines = _settle(phone, 4)
    assert "RAWRESET" in lines and lines.index("RAWRESET") < lines.index("ACTIVE 0"), lines
    phone.lines.clear()
    r.handle(False, VK_TAB); r.handle(False, VK_LALT)  # late key-ups must not break anything
    assert not r.raw_down and not r.held
    b.disconnect()
    print("ok pointer back to PC releases real keys")


def test_raw_state_forgotten_when_link_drops():
    phone = FakePhone()
    b, r = _raw_router(phone)
    b.disconnect()
    assert not b.raw_keys and not b.raw_active()
    print("ok real-key state cleared on disconnect")


# ------------------------------------------------------------- keyhelper (adb launcher)
class _Res:
    def __init__(self, out="", err="", rc=0):
        self.stdout, self.stderr, self.returncode = out, err, rc


DEV1 = "List of devices attached\nABC123 device product:x model:Pixel_7 device:y transport_id:1\n\n"
DEV2 = DEV1 + "XYZ789 device product:x model:Galaxy device:y transport_id:2\n"
APK = "package:/data/app/~~k3y==/com.piyush.mouseshare-abc==/base.apk\n"


def _fake_adb(devices=DEV1, pm=APK, start_rc=0):
    calls = []

    def run(adb, args, timeout=15):
        calls.append(args)
        if args[:2] == ["devices", "-l"]:
            return _Res(devices)
        if "path" in args:
            return _Res(pm, rc=0 if pm else 1)
        if any("app_process" in a for a in args):
            return _Res("", "boom: no space" if start_rc else "", start_rc)
        return _Res()
    return run, calls


def test_keyhelper_starts_the_helper_with_the_right_commands():
    import keyhelper
    run, calls = _fake_adb()
    token = keyhelper.start_helper(adb_path=__file__, token="tok123", run=run)
    assert token == "tok123"
    assert [c[0] if c[0] != "shell" else c[1] for c in calls] == ["devices", "pm", "pkill -f '[c]om.piyush.mouseshare.KeyServer'", calls[-1][1]]
    start = calls[-1][1]
    assert start == ("CLASSPATH='/data/app/~~k3y==/com.piyush.mouseshare-abc==/base.apk' setsid nohup "
                     "app_process / com.piyush.mouseshare.KeyServer tok123 </dev/null >/dev/null 2>&1 &"), start
    assert "-s" not in calls[0] and "-s" not in calls[-1], "one phone: no serial needed"
    print("ok keyhelper commands")


def test_keyhelper_uses_serial_and_reports_problems():
    import keyhelper
    from bridge import BridgeError
    # two phones: must be told which
    run, calls = _fake_adb(devices=DEV2)
    try:
        keyhelper.start_helper(adb_path=__file__, run=run)
        raise AssertionError("two phones without a serial should be refused")
    except BridgeError as e:
        assert "usb_serial" in str(e) and "ABC123" in str(e) and "XYZ789" in str(e), str(e)
    # ...and with a serial every phone command is addressed to it
    run, calls = _fake_adb(devices=DEV2)
    keyhelper.start_helper(adb_path=__file__, serial="XYZ789", token="t", run=run)
    assert all(c[:2] == ["-s", "XYZ789"] for c in calls[1:]), calls
    # serial that is not connected
    run, _ = _fake_adb()
    try:
        keyhelper.start_helper(adb_path=__file__, serial="NOPE", run=run)
        raise AssertionError("unknown serial accepted")
    except BridgeError as e:
        assert "NOPE" in str(e)
    # no phone at all
    run, _ = _fake_adb(devices="List of devices attached\n\n")
    try:
        keyhelper.start_helper(adb_path=__file__, run=run)
        raise AssertionError("no phone accepted")
    except BridgeError as e:
        assert "No phone" in str(e)
    # app not installed
    run, _ = _fake_adb(pm="")
    try:
        keyhelper.start_helper(adb_path=__file__, run=run)
        raise AssertionError("missing app accepted")
    except BridgeError as e:
        assert "not installed" in str(e)
    # helper failed to launch: the adb message is passed on
    run, _ = _fake_adb(start_rc=1)
    try:
        keyhelper.start_helper(adb_path=__file__, run=run)
        raise AssertionError("failed start accepted")
    except BridgeError as e:
        assert "no space" in str(e), str(e)
    # adb itself missing
    try:
        keyhelper.start_helper(adb_path=os.path.join(tempfile.mkdtemp(), "nope"), run=run)
        # only an error if adb is not on PATH either; tolerate machines that have adb installed
    except BridgeError as e:
        assert "adb not found" in str(e)
    print("ok keyhelper error handling")


def test_keyhelper_tokens_are_random():
    import keyhelper
    a, b = keyhelper.new_token(), keyhelper.new_token()
    assert a != b and len(a) == 24 and all(c in "0123456789abcdef" for c in a)
    assert keyhelper.parse_apk_path("package:/x/base.apk\npackage:/x/split_a.apk\n") == "/x/base.apk"
    assert keyhelper.parse_apk_path("") is None
    print("ok keyhelper tokens / apk path")


# ------------------------------------------------------------------ real mouse (REALMOUSE)
def _enter(phone, raw=False, **overrides):
    """Connect, optionally announce the helper, then move input to the phone; return its lines."""
    b, screen = make(phone, **overrides)
    b.connect()
    if raw:
        phone.push("RAWKEYS 1 FakeSink")
        end = time.time() + 2
        while not b.raw_keys and time.time() < end:
            time.sleep(0.01)
    phone.lines.clear()
    b.toggle()
    lines = _settle(phone, 3)
    b.disconnect()
    return lines


def test_realmouse_is_announced_before_the_first_position():
    lines = _enter(FakePhone(), raw=True)
    assert "REALMOUSE 1 0" in lines, lines
    first_pos = next(i for i, l in enumerate(lines) if l.startswith("P "))
    assert lines.index("REALMOUSE 1 0") < first_pos < lines.index("ACTIVE 1"), lines
    print("ok REALMOUSE 1 sent before the first position")


def test_realmouse_follows_the_helper_and_the_settings():
    assert "REALMOUSE 0 0" in _enter(FakePhone(), raw=False)                       # no helper -> touch gestures
    assert "REALMOUSE 0 0" in _enter(FakePhone(), raw=True, real_keys=False)       # switched off in settings
    assert "REALMOUSE 1 1" in _enter(FakePhone(), raw=True, right_click_back=True)
    assert "REALMOUSE 0 1" in _enter(FakePhone(), raw=False, right_click_back=True)
    print("ok REALMOUSE follows helper state and settings")


def test_wheel_and_buttons_still_use_the_same_messages():
    phone = FakePhone()
    b, r = _raw_router(phone)
    b.mouse_wheel(1)
    b.mouse_wheel(-2, horizontal=True)
    b.mouse_button("L", True); b.mouse_button("L", False)
    b.mouse_button("X1", True); b.mouse_button("X1", False)
    lines = _settle(phone, 6)
    assert "S 1.000" in lines and "H -2.000" in lines, lines
    assert lines.count("D L") == 1 and lines.count("U L") == 1 and "D X1" in lines and "U X1" in lines, lines
    b.disconnect()
    print("ok wheel/button messages unchanged")


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("ALL PASSED")
