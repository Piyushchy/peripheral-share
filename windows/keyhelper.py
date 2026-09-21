"""
keyhelper.py - start the phone's "real keyboard" helper over adb.

Why this exists: Android only lets the shell user (what `adb shell` runs as) put genuine key
events into the system's input pipeline. An ordinary app - even an accessibility service -
cannot, which is why system shortcuts such as Alt+Tab could not be sent before. So, once per
phone boot, this launches a tiny helper (class com.piyush.mouseshare.KeyServer, which lives
inside the MouseShare APK) as the shell user. The phone app then forwards the PC's key-down /
key-up events to it and the helper injects them, exactly as a hardware keyboard would.

Needs: adb on the PC (Android platform-tools) and USB debugging (or wireless debugging) on.
The helper stays running after the cable is unplugged; it stops when the phone reboots.

Manual equivalent (for troubleshooting):
    adb shell pm path com.piyush.mouseshare
    adb shell "CLASSPATH='<path from above>' setsid nohup app_process / \\
               com.piyush.mouseshare.KeyServer <token> </dev/null >/dev/null 2>&1 &"
"""
import secrets

from bridge import BridgeError
from discovery import find_adb, parse_devices, _run

PKG = "com.piyush.mouseshare"
HELPER_CLASS = "com.piyush.mouseshare.KeyServer"


def new_token():
    """Random one-run password. The helper only accepts connections that present it."""
    return secrets.token_hex(12)


def parse_apk_path(stdout):
    """`pm path` prints 'package:/data/app/.../base.apk' (one line per split apk)."""
    for line in stdout.splitlines():
        line = line.strip()
        if line.startswith("package:") and line.endswith(".apk"):
            return line[len("package:"):]
    return None


def start_command(apk_path, token):
    """Shell command that launches the helper detached, so it outlives the adb connection."""
    return ("CLASSPATH='%s' setsid nohup app_process / %s %s </dev/null >/dev/null 2>&1 &"
            % (apk_path, HELPER_CLASS, token))


def start_helper(adb_path="", serial="", token=None, run=_run):
    """
    Start (or restart) the helper on the phone. Returns the token, which the caller must then
    send to the phone app (bridge.send_keysrv) so it can connect. Raises BridgeError with a
    message meant for the user. `run` is injectable for tests.
    """
    adb = find_adb(adb_path)
    if not adb:
        raise BridgeError("adb not found. Install Android platform-tools, or set the adb path "
                          "in config.json.")
    token = token or new_token()

    def adb_run(args, timeout=15):
        try:
            return run(adb, args, timeout=timeout)
        except Exception as e:                       # OSError, SubprocessError, TimeoutExpired
            raise BridgeError("Could not run adb: %s" % e)

    devices = parse_devices(adb_run(["devices", "-l"]).stdout)
    serial = (serial or "").strip()
    if serial:
        if serial not in [d["serial"] for d in devices]:
            raise BridgeError("Phone %s is not connected over adb (or USB debugging was not "
                              "allowed on it)." % serial)
    elif not devices:
        raise BridgeError("No phone found over adb. Plug it in with USB debugging on and tap "
                          "'Allow' on the phone.")
    elif len(devices) > 1:
        raise BridgeError("Several phones are connected over adb; set usb_serial in config.json "
                          "to pick one (%s)." % ", ".join(d["serial"] for d in devices))
    base = ["-s", serial] if serial else []

    res = adb_run(base + ["shell", "pm", "path", PKG])
    apk = parse_apk_path(res.stdout)
    if not apk:
        raise BridgeError("MouseShare is not installed on the phone (or adb cannot see it).")

    # stop an old helper first; the [c] keeps the pattern from matching this very command
    adb_run(base + ["shell", "pkill -f '[c]om.piyush.mouseshare.KeyServer'"])

    res = adb_run(base + ["shell", start_command(apk, token)])
    if res.returncode != 0:
        detail = (res.stderr or res.stdout or "unknown error").strip().splitlines()
        raise BridgeError("Could not start the helper: %s" % (detail[-1] if detail else "unknown error"))
    return token
