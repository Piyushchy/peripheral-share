# MouseShare

Use one mouse and keyboard across your Windows PC and your Android phone, like a
dual-monitor setup. Cross the screen edge, press a shortcut or click a mouse button
to move the pointer onto the phone; typing follows the pointer. Works over WiFi or USB.

```
MouseShare/
  windows/   Python app (stdlib only) - captures mouse/keyboard, sends them to the phone
  android/   Kotlin app (Android Studio project, no dependencies) - cursor overlay + input
  .github/   CI that builds MouseShare.exe and the APK
```

## Features
- Pointer moves to the phone by **screen edge**, **keyboard shortcut**, or an **assigned mouse button** - each one can be turned off independently.
- **Keyboard follows the pointer**: click a text box on the phone and type with your PC keyboard; move back to the PC and typing goes there again.
- **REAL Alt+Tab, real keys and a REAL mouse** (optional helper, see "Real keys and mouse" below): the
  PC's keys and mouse reach Android exactly as hardware would. Alt held + Tab tapped repeatedly is
  handled by the phone itself; the wheel, hover, clicks and drags are real mouse events, so scrolling
  near a screen edge can no longer pull down the notification shade or close the app.
- **Key events into text fields** (no helper needed, Android 13+): shortcuts are sent as genuine key presses with their Ctrl/Alt/Shift state, so Ctrl+Backspace, Ctrl+Delete, Ctrl+arrows, Shift+arrows, Ctrl+Z / Ctrl+Y, Ctrl+A/C/V/X, Alt+Left, Ctrl+F, Ctrl+=/- and F-keys behave like a hardware keyboard.
- Optional **system-key routing**: while the pointer is on the phone, Win+D = Home, Win+Tab = Recents, **Alt+Tab = app switcher (MouseShare draws its own strip of recent apps)**, Win+A = notifications, Esc = Back. Turn it off and Win, Win+anything and Alt+Tab stay on the PC even while the pointer is on the phone.
- **WiFi or USB** (USB uses `adb forward`, so it needs no network at all).
- **Auto-discovery** of phones on the network and of USB devices.
- **Tray icon** with Connect/Disconnect; closing the window hides it instead of disconnecting.
- **Start with Windows**, optionally hidden in the tray, with auto-connect and auto-reconnect.
- Emergency exit: **Ctrl+Alt+Shift+F12** always returns input to the PC.

## Requirements

### Using a published release
- Windows 10/11 PC.
- Android phone running Android 8.0 (API 26) or later.
- The phone app's MouseShare accessibility service enabled.
- WiFi mode: the PC and phone must be on the same network.
- USB mode: Android platform-tools (`adb`), USB debugging, and a USB cable.
- **Real key and mouse events**: `adb` and USB debugging are also required to start the helper once after each phone reboot. The checkbox only enables or disables use of the helper; it does not install `adb` or start the helper by itself.

### Building from source
- Python 3.9+ with Tkinter (included in the standard Windows installer).
- Android Studio with Android SDK Platform 34 and Java 17.
- An Android phone running Android 8.0 (API 26) or later.
- Android platform-tools (`adb`) and USB debugging for USB connections or the real-event helper.

Published builds contain the Windows executable and Android APK, but they do not bundle Android platform-tools or bypass Android's accessibility, USB-debugging, or per-boot helper requirements.

## Install

### Phone
1. Open `android/` in **Android Studio**, plug in the phone with USB debugging on, press **Run**.
   (Or download the APK from the Releases page.)
2. Open **MouseShare** -> **Open Accessibility settings** -> switch **MouseShare** on.
   Android 13+: if the switch is greyed out, Settings > Apps > MouseShare > menu > *Allow restricted settings*.
   Re-enable the service after every reinstall - Android turns it off.
3. The app shows the phone's IP, port and PIN.

### PC
- **Easy:** download `MouseShare.exe` from Releases and run it.
- **From source:** install Python 3.9+ and run `windows\run.bat` (or `python app.py` to see errors).

The PC app scans on launch and lists every phone it finds over USB and WiFi.
Double-click a row to connect (or select it, enter the PIN and press **Connect**).
WiFi discovery uses a UDP broadcast plus a TCP sweep of your subnet, so it still
works on routers that drop broadcasts. USB entries need no IP address at all.

### USB instead of WiFi
Install Android platform-tools (adb), enable USB debugging, plug the cable in, hit **Scan**,
pick the USB entry, and connect. The app runs `adb forward` for you.

## Switching

| Method | Where | Notes |
|---|---|---|
| Screen edge | Switching tab | Pick which side the phone sits on. The cursor enters at the same height; push past the phone's opposite edge to come back. A rest delay and corner margin prevent accidental jumps. |
| Keyboard shortcut | Switching tab | Click the box, press any combo. Default Ctrl+Alt+M. Works in both directions. |
| Mouse button | Switching tab | Wheel click, side button 1, or side button 2. |
| Emergency exit | always on | Ctrl+Alt+Shift+F12. |

Turn edge crossing off and keep only the shortcut, or any other combination.

## On the phone

| PC input | Phone |
|---|---|
| Move | on-screen cursor |
| Left click / drag | tap / swipe |
| Wheel | scroll |
| Right click, side button 1 | Back |
| Wheel click | Home |
| Side button 2 | Recent apps |
| Typing | text into the focused box (your PC layout is respected) |
| Backspace, Delete, Enter, Tab, arrows, Home/End, F-keys | real key presses |
| Ctrl+Backspace / Ctrl+Delete | delete previous / next word |
| Ctrl+arrows, Shift+arrows, Ctrl+Shift+arrows | jump by word / select |
| Ctrl+A / C / V / X / Z / Y | select all / copy / paste / cut / undo / redo |
| Other Ctrl+key and Alt+key | delivered to the app as a shortcut (Ctrl+F, Alt+Left...) |
| Win (tap), Win+D, Win+M | Home (optional) |
| Win+Tab | Recent apps (optional) |
| Alt+Tab | **with the real-key helper: the phone's own Alt+Tab** (hold Alt, tap Tab repeatedly, release Alt). Without it: a MouseShare app-switcher strip |
| Win+A, Win+N | Notification shade (optional) |
| Esc | Back (optional) |

## Real keys and mouse (real Alt+Tab, real scrolling)

Android only accepts genuine key events from the *shell user* (what `adb shell` runs as); an ordinary
app - even an accessibility service - can only type into the focused text box. So for real system
shortcuts MouseShare uses a tiny helper that lives inside the MouseShare APK and is started once per
phone boot from the PC:

1. Install Android **platform-tools** on the PC (`adb`). On the phone turn on **Developer options ->
   USB debugging** (Xiaomi/Redmi/POCO also need **USB debugging (Security settings)**).
2. Plug the phone in, tap **Allow** on the phone, connect MouseShare as usual.
3. PC app -> **Switching** tab -> Keyboard -> **Start real-key helper**. The status should turn green:
   `Real keys ON (...)`. The phone's setup screen shows the same on its `Real keys:` line.
4. Unplug if you like: the helper keeps running until the phone reboots (then click the button again).

While it is on, **the mouse is real too**. Android receives genuine mouse events instead of finger
swipes: moving is a hover, the wheel is a real scroll event, buttons are real buttons and dragging is
a real drag. A finger swipe from near the top, bottom or sides is what Android treats as a system
gesture (notification shade, home, back), so the old wheel-as-swipe could trigger those; a mouse wheel
cannot. Left, right and middle are real mouse buttons; the two side buttons are Back and Forward.
Home and Recents are on the Win key (Win = Home, Win+Tab = Recents). Right-click behaves as your phone
treats a real mouse's right-click (Back on many phones). If it does nothing on yours, tick "Right-click
also sends Back" on the Switching tab. The phone's setup screen shows `Mouse: real mouse events`.
Scrolling speed is Android's per-notch distance times the "Scroll speed" setting.

Also with the helper on, the PC sends each key as a real down / up event: **Alt is held on the phone while you
tap Tab again and again**, exactly as with a hardware keyboard. Modifiers, Backspace, arrows, Enter,
Tab, F-keys and every Ctrl/Alt shortcut go this way. Ordinary typing is still sent as text so your PC
keyboard layout decides which characters appear. Win-key shortcuts and Esc keep their Home / Recents /
Back mappings.

Security: the helper listens on `127.0.0.1` only and needs a random one-time token that the PC hands
to the phone app over the PIN-protected link, so other apps and other machines cannot use it.
Turn the feature off any time with "Send REAL key events" on the Switching tab.

Manual start (if the button fails):

    adb shell pm path com.piyush.mouseshare
    adb shell "CLASSPATH='<path from above>' setsid nohup app_process / com.piyush.mouseshare.KeyServer <token> </dev/null >/dev/null 2>&1 &"

then send `KEYSRV <token>` (the button does this for you).

## How typing reaches the phone
1. **Real key events (Android 13+).** The accessibility service attaches to the focused text
   field the way an on-screen keyboard does and sends genuine `KeyEvent`s. The app itself
   handles Ctrl+Backspace, undo, selection, etc. The setup screen shows
   `Keyboard: text field connected - real key events` when a field is focused.
2. **Fallback.** With no keyboard connection (older Android, or nothing focused) the text of
   the focused field is rewritten through the accessibility API. Word delete, word jump and
   Shift-selection still work; undo and in-app shortcuts do not.

Plain typing is always sent as text (not key codes) so the PC's keyboard layout decides what
appears. System shortcuts (Alt+Tab, Win keys, Esc) never reach an app as key events on any
Android phone, so they are mapped to phone actions instead.

## Troubleshooting
- **Start real-key helper says "no phone found over adb"**: plug in with USB debugging on and tap
  Allow on the phone; `adb devices` must list it as `device` (not `unauthorized`).
- **Scrolling still opens the notification shade / closes the app**: the phone's setup screen must say
  `Mouse: real mouse events`. If it says `touch gestures`, the helper is not running or "Send REAL key
  and mouse events" is off. Without the helper, wheel scrolling is still a swipe (now kept at least 56dp
  away from every edge), which is better but cannot be identical to a real wheel.
- **Right-click does nothing in real-mouse mode**: your phone does not turn a right-click into Back by
  itself. Tick "Right-click also sends Back". (If it now goes Back twice, untick it.)
- **`Real keys OFF: ... not reachable`**: the helper is not running (or died). Click the button again.
  If it never starts, run the manual commands above and read adb's error.
- **`Real keys OFF: ... no way to reach the input service`**: this Android build hides the input
  service from the shell helper. Nothing more can be done from an app; the normal (non-real) keys
  keep working.
- **Real keys are ON but Alt+Tab does nothing**: not every phone maker implements Alt+Tab. Test with
  a Bluetooth keyboard: if Alt+Tab does nothing there either, the phone simply has no such shortcut.
  Injected keys do not work on some Xiaomi/Redmi/POCO phones until "USB debugging (Security
  settings)" is on.
- **Ctrl+Backspace etc. do nothing**: after updating the app, switch the MouseShare accessibility
  service off and on again - the new keyboard permission is only picked up on re-enable.
  The setup screen's `Keyboard:` line says which path is active.
- **Alt+Tab only opens Recents**: the switcher builds its list from the apps you open *after* the
  MouseShare service starts (Android does not let apps read the real Recents list). Open two or
  three apps on the phone and it will switch between them. The setup screen's `Alt+Tab apps:`
  line shows what it currently knows. With fewer than two known apps it opens plain Recents.
- **Alt+Tab shows the strip but the app does not open**: the `Last keystroke` line on the setup
  screen says `Alt+Tab: switched to <app>` or `could not open <app>` (then Recents opens
  instead). If Alt+Tab switches Windows apps on the PC instead, "System keys act on the device
  the cursor is on" is turned off in the PC app.
- **Nothing types on the phone**: the accessibility service must have *window content*
  access. Reinstalling the app turns the service off - switch it back on. The phone's
  setup screen shows the last command it received and what happened to the last keystroke;
  the PC window shows the last command it sent. Together they tell you which side is stuck.
- **"Target machine actively refused it"**: the service is off, so nothing is listening.
- **No phones found**: check both devices are on the same WiFi, or use USB.
- **Crashes / odd behaviour**: `%APPDATA%\MouseShare\log.txt` has the hook errors.

## Development
```
cd windows
python test_bridge.py     # switching, keyboard, discovery and reconnect logic, no phone needed
```
The Windows code is split into `bridge.py` (platform-independent logic, fully tested),
`winio.py` (ctypes hooks), `discovery.py`, `win_tray.py`, `keys.py` (key routing and the
Windows-to-Android keycode table) and `app.py` (tkinter GUI).
The protocol is documented at the top of `bridge.py`.

## Building the Windows exe
```
cd windows
build.bat                 # -> windows\dist\MouseShare.exe
```
It installs PyInstaller and uses `MouseShare.spec` (one file, no console, tray icon bundled).
GitHub Actions does the same on every push; tagging `v1.0.0` publishes the exe and the APK
to a Release.

## Known limits
- Android does not let normal apps be a real mouse or keyboard, so the cursor is an overlay
  and clicks are injected touch gestures. Typing uses the keyboard connection described above.
  Games that read raw input (not text fields) will ignore it.
- Without the real-key helper the mouse can only be emulated with touch gestures: wheel scrolling is a
  swipe kept away from the screen edges, and hover, right-click and drag behave like a finger, not a
  mouse.
- Without the real-key helper, system-level shortcuts cannot be produced by an ordinary app, so Alt+Tab
  falls back to MouseShare's own switcher: a strip of recently used apps, the highlight slides on each
  Tab, and the highlighted app is launched when Alt is released. Esc, Home or a mouse click while Alt is
  held closes it. Apps used before the service started are not in the list yet.
- The real-key helper needs adb once per phone boot (USB debugging).
- If the "Send REAL key and mouse events" checkbox is off, or the helper is not running,
  MouseShare uses the touch-gesture and fallback-key paths instead; real Alt+Tab, hover,
  and real wheel events are unavailable.
- While the pointer is on the phone, the Windows cursor stays parked mid-screen.
- Plain TCP protected by a PIN - use it on your own network.
- Password fields may refuse text in the fallback path; the real-key-event path usually works.
- iPhone is not supported (iOS blocks this).
- Routers with AP/client isolation block WiFi mode; use USB or a hotspot.

## License
MIT - see [LICENSE](LICENSE).
