package com.piyush.mouseshare

import android.accessibilityservice.AccessibilityService
import android.accessibilityservice.InputMethod
import android.annotation.TargetApi
import android.os.Build
import android.os.Bundle
import android.os.SystemClock
import android.text.InputType
import android.view.KeyCharacterMap
import android.view.KeyEvent
import android.view.accessibility.AccessibilityNodeInfo
import android.view.inputmethod.EditorInfo
import kotlin.math.max
import kotlin.math.min

/**
 * Typing on the phone. Two paths, best one first:
 *
 * 1. **Real key events (Android 13+).** The accessibility service attaches to the focused
 *    text field like an on-screen keyboard ([KeyInputMethod]) and sends genuine [KeyEvent]s
 *    with Ctrl/Alt/Shift state, plus committed text. The app handles them itself, so
 *    Ctrl+Backspace, Ctrl+Z, Shift+arrows, Alt+Left and friends behave exactly as they do
 *    with a hardware keyboard - and it also works in fields where set-text is refused.
 *
 * 2. **Fallback.** If there is no keyboard connection, find the focused editable node and
 *    rewrite its text around the caret (ACTION_SET_TEXT). Modifier-aware for word delete,
 *    word jump and Shift-selection, but it cannot do undo or in-app shortcuts.
 *
 * System shortcuts (Alt+Tab, Win keys, Esc) never reach an app as key events; those are
 * handled by [MouseService.navigate] through global actions.
 */
object KeyInput {

    const val MOD_SHIFT = 1
    const val MOD_ALT = 2
    const val MOD_CTRL = 4

    /** Last thing we tried to do and whether it worked - shown on the setup screen. */
    @Volatile var lastResult: String = "-"

    /** The editor the keyboard connection is attached to (set by [KeyInputMethod]). */
    @Volatile var editor: EditorInfo? = null

    // =====================================================================
    // real keyboard connection (Android 13+)
    // =====================================================================

    @TargetApi(33)
    private fun connection(service: AccessibilityService): InputMethod.AccessibilityInputConnection? {
        if (Build.VERSION.SDK_INT < 33) return null
        return try { service.inputMethod?.currentInputConnection } catch (e: Exception) { null }
    }

    /** One line for the setup screen: which path typing will take right now. */
    fun connectionState(service: AccessibilityService): String {
        if (Build.VERSION.SDK_INT < 33) return "Android 13+ needed for real key events (using text fallback)"
        val im = try { service.inputMethod } catch (e: Exception) { null }
        if (im == null) return "keyboard API is off - switch MouseShare off and on again in Accessibility settings"
        return if (connection(service) != null) "text field connected - real key events"
        else "no text field focused (tap one on the phone)"
    }

    private fun metaState(mods: Int): Int {
        var m = 0
        if ((mods and MOD_SHIFT) != 0) m = m or KeyEvent.META_SHIFT_ON or KeyEvent.META_SHIFT_LEFT_ON
        if ((mods and MOD_ALT) != 0) m = m or KeyEvent.META_ALT_ON or KeyEvent.META_ALT_LEFT_ON
        if ((mods and MOD_CTRL) != 0) m = m or KeyEvent.META_CTRL_ON or KeyEvent.META_CTRL_LEFT_ON
        return m
    }

    /**
     * Press and release one key with modifiers, e.g. keyCode DEL + MOD_CTRL = Ctrl+Backspace.
     * keyCode is an android.view.KeyEvent KEYCODE_*.
     */
    fun press(service: AccessibilityService, keyCode: Int, mods: Int): Boolean {
        var code = keyCode
        var m = mods
        // Windows redo is Ctrl+Y, Android's is Ctrl+Shift+Z
        if ((m and MOD_CTRL) != 0 && code == KeyEvent.KEYCODE_Y) {
            code = KeyEvent.KEYCODE_Z
            m = m or MOD_SHIFT
        }
        val ic = connection(service)
        if (ic != null) {
            try {
                val meta = metaState(m)
                val now = SystemClock.uptimeMillis()
                val flags = KeyEvent.FLAG_SOFT_KEYBOARD or KeyEvent.FLAG_KEEP_TOUCH_MODE
                val dev = KeyCharacterMap.VIRTUAL_KEYBOARD
                ic.sendKeyEvent(KeyEvent(now, now, KeyEvent.ACTION_DOWN, code, 0, meta, dev, 0, flags))
                ic.sendKeyEvent(KeyEvent(now, now, KeyEvent.ACTION_UP, code, 0, meta, dev, 0, flags))
                lastResult = "${describe(code, m)} sent as a real key event"
                return true
            } catch (e: Exception) {
                lastResult = "key event failed (${e.message}) - using fallback"
            }
        }
        return legacyPress(service, code, m)
    }

    private fun describe(code: Int, mods: Int): String {
        val sb = StringBuilder()
        if ((mods and MOD_CTRL) != 0) sb.append("Ctrl+")
        if ((mods and MOD_ALT) != 0) sb.append("Alt+")
        if ((mods and MOD_SHIFT) != 0) sb.append("Shift+")
        sb.append(KeyEvent.keyCodeToString(code).removePrefix("KEYCODE_"))
        return sb.toString()
    }

    /** Insert plain text at the caret (replacing any selection). */
    fun type(service: AccessibilityService, text: String): Boolean {
        val ic = connection(service)
        if (ic != null) {
            try {
                val info = editor
                // TYPE_NULL editors (terminals, some games/remote desktops) only understand key events
                val keysOnly = info != null &&
                    (info.inputType and InputType.TYPE_MASK_CLASS) == InputType.TYPE_NULL
                val events = if (keysOnly)
                    KeyCharacterMap.load(KeyCharacterMap.VIRTUAL_KEYBOARD).getEvents(text.toCharArray())
                else null
                if (events != null) {
                    for (e in events) ic.sendKeyEvent(e)
                } else {
                    ic.commitText(text, 1, null)
                }
                lastResult = "typed \"$text\" (keyboard connection)"
                return true
            } catch (e: Exception) {
                lastResult = "typing via keyboard connection failed (${e.message}) - using fallback"
            }
        }
        return typeLegacy(service, text)
    }

    // =====================================================================
    // fallback: rewrite the focused field through the accessibility tree
    // =====================================================================

    private val LEGACY_NAMES = mapOf(
        KeyEvent.KEYCODE_DEL to "BACKSPACE",
        KeyEvent.KEYCODE_FORWARD_DEL to "DEL",
        KeyEvent.KEYCODE_TAB to "TAB",
        KeyEvent.KEYCODE_ENTER to "ENTER",
        KeyEvent.KEYCODE_DPAD_LEFT to "LEFT",
        KeyEvent.KEYCODE_DPAD_RIGHT to "RIGHT",
        KeyEvent.KEYCODE_DPAD_UP to "UP",
        KeyEvent.KEYCODE_DPAD_DOWN to "DOWN",
        KeyEvent.KEYCODE_MOVE_HOME to "HOME",
        KeyEvent.KEYCODE_MOVE_END to "END",
        KeyEvent.KEYCODE_PAGE_UP to "PAGEUP",
        KeyEvent.KEYCODE_PAGE_DOWN to "PAGEDOWN",
        KeyEvent.KEYCODE_ESCAPE to "ESCAPE"
    )

    private fun legacyPress(service: AccessibilityService, code: Int, mods: Int): Boolean {
        val ctrl = (mods and MOD_CTRL) != 0
        val shift = (mods and MOD_SHIFT) != 0
        if (ctrl) {
            val letter = when (code) {
                KeyEvent.KEYCODE_A -> "a"
                KeyEvent.KEYCODE_C -> "c"
                KeyEvent.KEYCODE_V -> "v"
                KeyEvent.KEYCODE_X -> "x"
                else -> null
            }
            if (letter != null) {
                val ok = ctrl(service, letter)
                lastResult = "Ctrl+${letter.uppercase()} " + (if (ok) "ok" else "failed") + " (fallback)"
                return ok
            }
        }
        val name = LEGACY_NAMES[code]
        if (name == null) {
            lastResult = "${describe(code, mods)} ignored (no keyboard connection)"
            return false
        }
        val ok = special(service, name, ctrl, shift)
        lastResult = "${describe(code, mods)} " +
            (if (ok) "ok" else "failed - no editable field focused") + " (fallback)"
        return ok
    }

    /**
     * The text field the user tapped. findFocus() is the fast path; some apps report no
     * input focus, so we then walk the window tree for a focused (or the only) editable node.
     */
    private fun focused(service: AccessibilityService): AccessibilityNodeInfo? {
        try {
            service.findFocus(AccessibilityNodeInfo.FOCUS_INPUT)?.let { if (it.isEditable) return it }
        } catch (e: Exception) {
        }
        try {
            service.findFocus(AccessibilityNodeInfo.FOCUS_ACCESSIBILITY)
                ?.let { if (it.isEditable) return it }
        } catch (e: Exception) {
        }
        val root = try { service.rootInActiveWindow } catch (e: Exception) { null } ?: return null
        return try { findEditable(root, preferFocused = true) ?: findEditable(root, false) }
        finally { root.recycleQuietly() }
    }

    private fun findEditable(
        node: AccessibilityNodeInfo,
        preferFocused: Boolean
    ): AccessibilityNodeInfo? {
        if (node.isEditable && (!preferFocused || node.isFocused)) {
            return AccessibilityNodeInfo.obtain(node)
        }
        for (i in 0 until node.childCount) {
            val child = node.getChild(i) ?: continue
            val found = findEditable(child, preferFocused)
            child.recycleQuietly()
            if (found != null) return found
        }
        return null
    }

    private fun selection(node: AccessibilityNodeInfo, len: Int): Pair<Int, Int> {
        var s = node.textSelectionStart
        var e = node.textSelectionEnd
        if (s < 0 || s > len) s = len
        if (e < 0 || e > len) e = s
        return Pair(min(s, e), max(s, e))
    }

    private fun setText(node: AccessibilityNodeInfo, text: String, caret: Int): Boolean {
        val args = Bundle()
        args.putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, text)
        if (!node.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, args)) return false
        select(node, caret, caret, text.length)
        lastSelStart = -1
        return true
    }

    private fun select(node: AccessibilityNodeInfo, start: Int, end: Int, len: Int) {
        val sel = Bundle()
        sel.putInt(AccessibilityNodeInfo.ACTION_ARGUMENT_SELECTION_START_INT, start.coerceIn(0, len))
        sel.putInt(AccessibilityNodeInfo.ACTION_ARGUMENT_SELECTION_END_INT, end.coerceIn(0, len))
        node.performAction(AccessibilityNodeInfo.ACTION_SET_SELECTION, sel)
    }

    private fun typeLegacy(service: AccessibilityService, text: String): Boolean {
        val node = focused(service)
        if (node == null) {
            lastResult = "typed \"$text\" but no text box is focused"
            return false
        }
        try {
            if (!node.isEditable) {
                lastResult = "typed \"$text\" but the focused view is not editable"
                return false
            }
            lastResult = "typed \"$text\" (fallback)"
            val cur = node.text?.toString() ?: ""
            val (s, e) = selection(node, cur.length)
            return setText(node, cur.substring(0, s) + text + cur.substring(e), s + text.length)
        } finally {
            node.recycleQuietly()
        }
    }

    // ---- word / line helpers (Windows conventions: Ctrl+Left/Right land on word starts)
    private fun isWordChar(c: Char) = c.isLetterOrDigit() || c == '_'

    private fun prevWordStart(t: String, pos: Int): Int {
        var i = pos
        while (i > 0 && t[i - 1].isWhitespace()) i--
        if (i > 0 && isWordChar(t[i - 1])) {
            while (i > 0 && isWordChar(t[i - 1])) i--
        } else {
            while (i > 0 && !isWordChar(t[i - 1]) && !t[i - 1].isWhitespace()) i--
        }
        return i
    }

    private fun nextWordStart(t: String, pos: Int): Int {
        var i = pos
        val n = t.length
        if (i < n && isWordChar(t[i])) {
            while (i < n && isWordChar(t[i])) i++
        } else {
            while (i < n && !isWordChar(t[i]) && !t[i].isWhitespace()) i++
        }
        while (i < n && t[i].isWhitespace()) i++
        return i
    }

    private fun lineStart(t: String, caret: Int): Int = t.lastIndexOf('\n', caret - 1) + 1

    private fun lineEnd(t: String, caret: Int): Int {
        val i = t.indexOf('\n', caret)
        return if (i < 0) t.length else i
    }

    // The selection we last produced with Shift+move, so the next Shift+move keeps its anchor.
    private var lastSelStart = -1
    private var lastSelEnd = -1
    private var lastAnchor = -1

    private fun moveKey(
        node: AccessibilityNodeInfo, name: String, cur: String,
        s: Int, e: Int, ctrl: Boolean, shift: Boolean
    ) {
        val len = cur.length
        val ours = s == lastSelStart && e == lastSelEnd && (lastAnchor == s || lastAnchor == e)
        val anchor = if (ours) lastAnchor else s
        val caret = if (s == e) s else if (anchor == s) e else s     // the end that moves

        if (!shift && s != e && (name == "LEFT" || name == "RIGHT")) {
            val p = if (name == "LEFT") s else e                       // collapse the selection
            select(node, p, p, len)
            lastSelStart = -1
            return
        }
        val target = when (name) {
            "LEFT" -> if (ctrl) prevWordStart(cur, caret) else caret - 1
            "RIGHT" -> if (ctrl) nextWordStart(cur, caret) else caret + 1
            "HOME" -> if (ctrl) 0 else lineStart(cur, caret)
            "END" -> if (ctrl) len else lineEnd(cur, caret)
            "UP" -> lineStep(cur, caret, -1)
            else -> lineStep(cur, caret, 1)
        }.coerceIn(0, len)

        if (shift) {
            val a = min(anchor, target)
            val b = max(anchor, target)
            select(node, a, b, len)
            lastSelStart = a
            lastSelEnd = b
            lastAnchor = anchor
        } else {
            select(node, target, target, len)
            lastSelStart = -1
        }
    }

    /** BACKSPACE ENTER TAB DEL LEFT RIGHT UP DOWN HOME END PAGEUP PAGEDOWN ESCAPE */
    fun special(
        service: AccessibilityService, name: String,
        ctrl: Boolean = false, shift: Boolean = false
    ): Boolean {
        if (name == "ESCAPE") {
            return service.performGlobalAction(AccessibilityService.GLOBAL_ACTION_BACK)
        }
        if (name == "PAGEUP" || name == "PAGEDOWN") return scroll(service, name == "PAGEDOWN")

        val node = focused(service) ?: return false
        try {
            if (!node.isEditable) return false
            val cur = node.text?.toString() ?: ""
            val (s, e) = selection(node, cur.length)
            return when (name) {
                "BACKSPACE" -> when {
                    s != e -> setText(node, cur.removeRange(s, e), s)
                    s > 0 && ctrl -> {
                        val p = prevWordStart(cur, s)
                        setText(node, cur.removeRange(p, s), p)
                    }
                    s > 0 -> setText(node, cur.removeRange(s - 1, s), s - 1)
                    else -> true
                }
                "DEL" -> when {
                    s != e -> setText(node, cur.removeRange(s, e), s)
                    s < cur.length && ctrl -> {
                        val p = nextWordStart(cur, s)
                        setText(node, cur.removeRange(s, p), s)
                    }
                    s < cur.length -> setText(node, cur.removeRange(s, s + 1), s)
                    else -> true
                }
                "TAB" -> {
                    node.performAction(AccessibilityNodeInfo.ACTION_NEXT_HTML_ELEMENT)
                    setText(node, cur.substring(0, s) + "\t" + cur.substring(e), s + 1)
                }
                "ENTER" -> {
                    if (Build.VERSION.SDK_INT >= 30 &&
                        node.performAction(AccessibilityNodeInfo.AccessibilityAction.ACTION_IME_ENTER.id)
                    ) true
                    else setText(node, cur.substring(0, s) + "\n" + cur.substring(e), s + 1)
                }
                "LEFT", "RIGHT", "UP", "DOWN", "HOME", "END" -> {
                    moveKey(node, name, cur, s, e, ctrl, shift)
                    true
                }
                else -> false
            }
        } finally {
            node.recycleQuietly()
        }
    }

    /** Ctrl + a c v x - select all, copy, paste, cut (fallback path only). */
    fun ctrl(service: AccessibilityService, letter: String): Boolean {
        val node = focused(service) ?: return false
        try {
            val cur = node.text?.toString() ?: ""
            return when (letter) {
                "a" -> {
                    val sel = Bundle()
                    sel.putInt(AccessibilityNodeInfo.ACTION_ARGUMENT_SELECTION_START_INT, 0)
                    sel.putInt(AccessibilityNodeInfo.ACTION_ARGUMENT_SELECTION_END_INT, cur.length)
                    node.performAction(AccessibilityNodeInfo.ACTION_SET_SELECTION, sel)
                }
                "c" -> node.performAction(AccessibilityNodeInfo.ACTION_COPY)
                "v" -> node.performAction(AccessibilityNodeInfo.ACTION_PASTE)
                "x" -> node.performAction(AccessibilityNodeInfo.ACTION_CUT)
                else -> false
            }
        } finally {
            node.recycleQuietly()
        }
    }

    /** Caret one visual line up/down - approximated with newlines, good enough for text fields. */
    private fun lineStep(text: String, caret: Int, direction: Int): Int {
        val lineStart = text.lastIndexOf('\n', max(0, caret - 1)) + 1
        val column = caret - lineStart
        return if (direction < 0) {
            if (lineStart == 0) 0
            else {
                val prevStart = text.lastIndexOf('\n', lineStart - 2) + 1
                min(prevStart + column, lineStart - 1)
            }
        } else {
            val lineEnd = text.indexOf('\n', caret)
            if (lineEnd < 0) text.length
            else {
                val nextEnd = text.indexOf('\n', lineEnd + 1).let { if (it < 0) text.length else it }
                min(lineEnd + 1 + column, nextEnd)
            }
        }
    }

    /** Scroll the first scrollable view in the active window one step (also used by Alt+Tab). */
    fun scroll(service: AccessibilityService, forward: Boolean): Boolean {
        val root = service.rootInActiveWindow ?: return false
        try {
            val target = findScrollable(root) ?: return false
            val action = if (forward) AccessibilityNodeInfo.ACTION_SCROLL_FORWARD
            else AccessibilityNodeInfo.ACTION_SCROLL_BACKWARD
            val ok = target.performAction(action)
            target.recycleQuietly()
            return ok
        } finally {
            root.recycleQuietly()
        }
    }

    private fun findScrollable(node: AccessibilityNodeInfo): AccessibilityNodeInfo? {
        if (node.isScrollable) return AccessibilityNodeInfo.obtain(node)
        for (i in 0 until node.childCount) {
            val child = node.getChild(i) ?: continue
            val found = findScrollable(child)
            child.recycleQuietly()
            if (found != null) return found
        }
        return null
    }

    @Suppress("DEPRECATION")
    private fun AccessibilityNodeInfo.recycleQuietly() {
        if (Build.VERSION.SDK_INT < 33) {
            try { recycle() } catch (e: Exception) {}
        }
    }
}
