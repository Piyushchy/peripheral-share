"""
discovery.py - find phones running MouseShare.

Three ways, because each one fails on some setups:
  1. UDP broadcast "MOUSESHARE?" on 5598 - instant, but many routers and some
     Android builds drop broadcasts.
  2. TCP subnet sweep - connect to every address on your /24 and send "PROBE";
     the phone answers with its INFO line. Slower (a second or two) but reliable.
  3. adb - lists USB-attached phones. Those are reached through `adb forward`,
     so they need no IP address at all.
"""
import concurrent.futures
import os
import shutil
import socket
import subprocess
import sys
import time

from bridge import BridgeError, DISCOVERY_PORT, PORT

PROBE_UDP = b"MOUSESHARE?"
CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


# ------------------------------------------------------------------ WiFi
def parse_reply(text):
    """'MOUSESHARE 5599 1080 2400 Pixel 7' -> dict, or None if it isn't ours."""
    parts = text.strip().split()
    if len(parts) < 4 or parts[0] != "MOUSESHARE":
        return None
    try:
        return {
            "port": int(parts[1]),
            "w": int(parts[2]),
            "h": int(parts[3]),
            "name": " ".join(parts[4:]) or "phone",
        }
    except ValueError:
        return None


def parse_info(text):
    """'INFO 1080 2400 Pixel 7' (the TCP probe answer) -> dict, or None."""
    parts = text.strip().split()
    if len(parts) < 3 or parts[0] != "INFO":
        return None
    try:
        return {"port": PORT, "w": int(parts[1]), "h": int(parts[2]),
                "name": " ".join(parts[3:]) or "phone"}
    except ValueError:
        return None


def local_ipv4s():
    """Our own addresses on real networks (no loopback, no link-local)."""
    ips = set()
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 53))          # no packets sent; just picks the default route
        ips.add(s.getsockname()[0])
    except OSError:
        pass
    finally:
        s.close()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith(("127.", "169.254.")):
                ips.add(ip)
    except OSError:
        pass
    return sorted(ips)


def broadcast_addresses():
    addrs = {"255.255.255.255"}
    for ip in local_ipv4s():
        addrs.add(ip.rsplit(".", 1)[0] + ".255")
    return sorted(addrs)


def scan_udp(timeout=1.0):
    found = {}
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        s.settimeout(0.2)
        for addr in broadcast_addresses():
            for _ in range(2):                     # UDP: a lost probe is normal
                try:
                    s.sendto(PROBE_UDP, (addr, DISCOVERY_PORT))
                except OSError:
                    pass
        end = time.time() + timeout
        while time.time() < end:
            try:
                data, src = s.recvfrom(512)
            except socket.timeout:
                continue
            except OSError:
                break
            info = parse_reply(data.decode("utf-8", "ignore"))
            if info:
                info["ip"] = src[0]
                info["transport"] = "wifi"
                found[src[0]] = info
    finally:
        s.close()
    return found


def probe_host(ip, port=PORT, timeout=0.6):
    """Speak the PROBE handshake to one address. Returns a device dict or None."""
    try:
        with socket.create_connection((ip, port), timeout=timeout) as s:
            s.settimeout(timeout)
            s.sendall(b"PROBE\n")
            data = s.makefile("rb").readline().decode("utf-8", "ignore")
    except OSError:
        return None
    info = parse_info(data)
    if info:
        info["ip"] = ip
        info["transport"] = "wifi"
    return info


def scan_sweep(port=PORT, timeout=0.6):
    """Try every address on each of our /24 subnets."""
    targets = []
    for ip in local_ipv4s():
        prefix = ip.rsplit(".", 1)[0]
        targets += ["%s.%d" % (prefix, n) for n in range(1, 255) if "%s.%d" % (prefix, n) != ip]
    found = {}
    if not targets:
        return found
    with concurrent.futures.ThreadPoolExecutor(max_workers=128) as pool:
        for info in pool.map(lambda t: probe_host(t, port, timeout), targets):
            if info:
                found[info["ip"]] = info
    return found


def scan_wifi(deep=True, port=PORT):
    found = scan_udp()
    if deep:
        for ip, info in scan_sweep(port).items():
            found.setdefault(ip, info)
    return sorted(found.values(), key=lambda d: d["name"])


# ------------------------------------------------------------------- USB
def find_adb(configured=""):
    if configured and os.path.isfile(configured):
        return configured
    on_path = shutil.which("adb")
    if on_path:
        return on_path
    local = os.environ.get("LOCALAPPDATA", "")
    candidates = [
        os.path.join(local, "Android", "Sdk", "platform-tools", "adb.exe"),
        r"C:\Android\platform-tools\adb.exe",
        r"C:\Program Files\Android\platform-tools\adb.exe",
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "adb", "adb.exe"),
    ]
    for path in candidates:
        if os.path.isfile(path):
            return path
    return None


def _run(adb, args, timeout=15):
    return subprocess.run([adb] + args, capture_output=True, text=True,
                          timeout=timeout, creationflags=CREATE_NO_WINDOW)


def parse_devices(stdout):
    """Parse `adb devices -l` -> [{'serial','name'}] (only devices that are ready)."""
    out = []
    for line in stdout.splitlines()[1:]:
        line = line.strip()
        if not line or line.startswith("*"):
            continue
        parts = line.split()
        if len(parts) < 2 or parts[1] != "device":
            continue
        name = parts[0]
        for token in parts[2:]:
            if token.startswith("model:"):
                name = token.split(":", 1)[1].replace("_", " ")
                break
        out.append({"serial": parts[0], "name": name})
    return out


def scan_usb(adb_path=""):
    adb = find_adb(adb_path)
    if not adb:
        return []
    try:
        res = _run(adb, ["devices", "-l"])
    except (OSError, subprocess.SubprocessError):
        return []
    devices = parse_devices(res.stdout)
    for d in devices:
        d["transport"] = "usb"
        d["port"] = PORT
    return devices


def adb_forward(adb_path="", phone_port=PORT, serial=None):
    """`adb forward tcp:<port> tcp:<port>`; returns the local port to dial. No IP needed."""
    adb = find_adb(adb_path)
    if not adb:
        raise BridgeError("adb not found. Install Android platform-tools, or set the adb path "
                          "in config.json.")
    args = (["-s", serial] if serial else []) + \
           ["forward", "tcp:%d" % phone_port, "tcp:%d" % phone_port]
    try:
        res = _run(adb, args)
    except (OSError, subprocess.SubprocessError) as e:
        raise BridgeError("Could not run adb: %s" % e)
    if res.returncode != 0:
        msg = (res.stderr or res.stdout or "unknown error").strip().splitlines()[-1]
        if "no devices" in msg or "device not found" in msg:
            msg += " - is the cable in and USB debugging allowed?"
        raise BridgeError("adb forward failed: %s" % msg)
    return phone_port


# ------------------------------------------------------------------- both
def scan_all(adb_path="", deep=True):
    """USB devices first (they are the most reliable), then WiFi."""
    usb = []
    try:
        usb = scan_usb(adb_path)
    except Exception:
        pass
    try:
        wifi = scan_wifi(deep=deep)
    except Exception:
        wifi = []
    return usb + wifi


def label(device):
    if device.get("transport") == "usb":
        return "USB   %s  (%s)" % (device.get("name", "phone"), device.get("serial", ""))
    return "WiFi  %s  (%s)" % (device.get("name", "phone"), device.get("ip", "?"))
