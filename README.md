# Peripheral Share

Peripheral Share (the application is named **MouseShare** in the Android and Windows
builds) lets you use one mouse and keyboard across a Windows PC and an Android phone,
like a dual-monitor setup. Cross the screen edge, press a shortcut, or click a mouse
button to move the pointer onto the phone; typing follows the pointer. It works over
WiFi or USB.

```
MouseShare/
  windows/   Python app (stdlib only) - captures mouse/keyboard, sends them to the phone
  android/   Kotlin app (Android Studio project, no dependencies) - cursor overlay + input
  .github/   CI that builds MouseShare.exe and the APK
```

## Features
- Pointer moves to the phone by **screen edge**, **keyboard shortcut**, or an **assigned mouse button** - each one can be turned off independently.
- **Keyboard follows the pointer**: click a text box on the phone and type with your PC keyboard; move back to the PC and typing goes there again.
- **Real key events on the phone** (Android 13+): shortcuts are sent as genuine key presses with their Ctrl/Alt/Shift state, so Ctrl+Backspace, Ctrl+Delete, Ctrl+arrows, Shift+arrows, Ctrl+Z / Ctrl+Y, Ctrl+A/C/V/X, Alt+Left, Ctrl+F, Ctrl+=/- and F-keys behave like a hardware keyboard.
- Optional **system-key routing**: while the pointer is on the phone, Win+D = Home, Win+Tab = Recents, **Alt+Tab = previous app**, Win+A = notifications, Esc = Back. Turn it off and Win, Win+anything and Alt+Tab stay on the PC even while the pointer is on the phone.
- **WiFi or USB** (USB uses `adb forward`, so it needs no network at all).
- **Auto-discovery** of phones on the network and of USB devices.
- **Tray icon** with Connect/Disconnect; closing the window hides it instead of disconnecting.
- **Start with Windows**, optionally hidden in the tray, with auto-connect and auto-reconnect.
- Emergency exit: **Ctrl+Alt+Shift+F12** always returns input to the PC.

## Make it work

## Download the ready-to-use apps

You do **not** need Android Studio, Python, or a compiler to use Peripheral Share.
Open the repository's [latest GitHub Release](../../releases/latest) and download:

- `PeripheralShare-Windows.exe` for the Windows PC.
- `PeripheralShare-Android.apk` for the Android phone.

On Windows, run the `.exe`. On Android, open the `.apk` and allow installation from
your browser or file manager when Android asks. Then follow the setup steps below to
enable the accessibility service and connect the two devices.

The Android APK is currently distributed as a debug-signed build for sideloading.
Android may show a security warning because it was downloaded outside Google Play.

### Requirements

- Windows 10 or later for the desktop app.
- Python 3.9+ if running the desktop app from source. `tkinter` is included with the
  standard Windows Python installer.
- Android Studio with the Android SDK, SDK Platform 34, and Java 17 to build the
  phone app.
- An Android phone running Android 8.0 (API 26) or later.
- For WiFi: both devices must be on the same network. For USB: Android platform-tools
  (`adb`) and USB debugging are required.

### 1. Install the phone app

For normal users, install `PeripheralShare-Android.apk` from the latest release.
Only use the Android Studio instructions below if you are developing the project.

1. Open **MouseShare** on the phone, select **Open Accessibility settings**, and
   enable the MouseShare service. This permission is required for the cursor, taps,
   drags, scrolling, and keyboard input.
2. Set a PIN of at least four digits in the phone app. Keep the phone's displayed IP
   address and port available for WiFi setup.

Developers can instead open the repository's `android/` folder in Android Studio,
connect the phone with USB debugging enabled, and press **Run**.

After every reinstall, enable the accessibility service again because Android may turn
it off. On Android 13+, use **Settings > Apps > MouseShare > Allow restricted settings**
if the accessibility switch is disabled.

### 2. Start the Windows app

Use either option:

- **Release build:** download and run `PeripheralShare-Windows.exe` from the latest
  GitHub Release.
- **From source:** open PowerShell or Command Prompt, then run:

  ```bat
  cd windows
  run.bat
  ```

  To run with visible Python errors instead, use `python app.py`.

The app scans for phones automatically. Double-click a discovered device, enter the
same PIN as on the phone, and select **Connect**.

### 3. Choose WiFi or USB

- **WiFi:** keep both devices on the same network, click **Scan again**, select the
  phone's WiFi entry, and connect.
- **USB:** install Android platform-tools, keep USB debugging enabled, connect the
  cable, click **Scan again**, select the USB entry, and connect. The app configures
  `adb forward` automatically, so no phone IP address is needed.

### 4. Configure switching

Open the **Switching** tab and enable any combination of:

- Screen-edge crossing.
- A keyboard shortcut (default: `Ctrl+Alt+M`).
- The middle mouse button or either side button.

Move the pointer back past the opposite edge, use the same shortcut, or use the
configured mouse button to return input to Windows. The emergency shortcut
`Ctrl+Alt+Shift+F12` always returns input to the PC.

The **Options** tab includes keyboard sharing, system-key routing, sensitivity, scroll
speed, auto-connect, reconnect, tray behavior, and start-with-Windows settings.

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
| Alt+Tab | jump to the previous app (optional) |
| Win+A, Win+N | Notification shade (optional) |
| Esc | Back (optional) |

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
- **Ctrl+Backspace etc. do nothing**: after updating the app, switch the MouseShare accessibility
  service off and on again - the new keyboard permission is only picked up on re-enable.
  The setup screen's `Keyboard:` line says which path is active.
- **Alt+Tab opens Recents but does not switch**: launchers differ in how they treat a double
  press of Recents. Set `ALT_TAB_QUICK_SWITCH = false` in `MouseService.kt` to make Alt+Tab
  just open Recents. If Alt+Tab switches Windows apps on the PC instead, "System keys act on
  the device the cursor is on" is turned off in the PC app.
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
- System-level shortcuts cannot be injected by an ordinary app, so Alt+Tab is emulated with
  the Recents action (a true Alt+Tab switcher with cycling is not possible).
- While the pointer is on the phone, the Windows cursor stays parked mid-screen.
- Plain TCP protected by a PIN - use it on your own network.
- Password fields may refuse text in the fallback path; the real-key-event path usually works.
- iPhone is not supported (iOS blocks this).
- Routers with AP/client isolation block WiFi mode; use USB or a hotspot.

## License
MIT - see [LICENSE](LICENSE).
