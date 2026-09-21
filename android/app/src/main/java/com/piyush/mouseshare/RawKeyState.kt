package com.piyush.mouseshare

import android.view.KeyEvent

/**
 * Turns the PC's raw "key went down" / "key went up" messages into the exact key events a
 * hardware keyboard would produce. Pure logic (only compile-time key constants from
 * android.view.KeyEvent), so it is unit-tested on a plain JVM.
 *
 * What matters for system shortcuts such as Alt+Tab:
 *  - the DOWN event of a modifier already carries that modifier in its meta state,
 *  - the UP event of a modifier no longer carries it (Android's Alt+Tab switcher looks for
 *    exactly this to know Alt was released),
 *  - every other key carries the modifiers held at that moment.
 */
class RawKeyState {

    class Ev(val down: Boolean, val code: Int, val meta: Int, val repeat: Int)

    private val held = LinkedHashSet<Int>()          // every key currently down, in press order
    private val heldMods = LinkedHashSet<Int>()
    private val repeats = HashMap<Int, Int>()

    /** Meta state of the modifiers held right now. */
    fun meta(): Int {
        var m = 0
        for (c in heldMods) m = m or (META[c] ?: 0)
        return m
    }

    /**
     * A key went down. [repeatHint] is true for the PC's auto-repeat. Returns null when there is
     * nothing to inject (a modifier repeating - hardware keyboards do not repeat those).
     */
    fun down(code: Int, repeatHint: Boolean): Ev? {
        val isMod = code in META
        val already = code in held
        if (isMod && already) return null
        held.add(code)
        if (isMod) heldMods.add(code)
        val repeat = if (already || repeatHint) (repeats[code] ?: 0) + 1 else 0
        repeats[code] = repeat
        return Ev(true, code, meta(), repeat)
    }

    /** A key went up. Null if we never saw it go down (do not send a stray release). */
    fun up(code: Int): Ev? {
        if (!held.remove(code)) return null
        repeats.remove(code)
        heldMods.remove(code)
        return Ev(false, code, meta(), 0)
    }

    /** Release everything (pointer went back to the PC, link dropped): normal keys first, modifiers last. */
    fun releaseAll(): List<Ev> {
        val out = ArrayList<Ev>()
        val plain = held.filter { it !in META }
        val mods = held.filter { it in META }
        for (c in plain + mods) up(c)?.let { out.add(it) }
        return out
    }

    fun isDown(code: Int) = code in held

    companion object {
        /** modifier keycode -> meta bits it contributes (generic bit + left/right bit) */
        val META: Map<Int, Int> = mapOf(
            KeyEvent.KEYCODE_ALT_LEFT to (KeyEvent.META_ALT_ON or KeyEvent.META_ALT_LEFT_ON),
            KeyEvent.KEYCODE_ALT_RIGHT to (KeyEvent.META_ALT_ON or KeyEvent.META_ALT_RIGHT_ON),
            KeyEvent.KEYCODE_SHIFT_LEFT to (KeyEvent.META_SHIFT_ON or KeyEvent.META_SHIFT_LEFT_ON),
            KeyEvent.KEYCODE_SHIFT_RIGHT to (KeyEvent.META_SHIFT_ON or KeyEvent.META_SHIFT_RIGHT_ON),
            KeyEvent.KEYCODE_CTRL_LEFT to (KeyEvent.META_CTRL_ON or KeyEvent.META_CTRL_LEFT_ON),
            KeyEvent.KEYCODE_CTRL_RIGHT to (KeyEvent.META_CTRL_ON or KeyEvent.META_CTRL_RIGHT_ON),
            KeyEvent.KEYCODE_META_LEFT to (KeyEvent.META_META_ON or KeyEvent.META_META_LEFT_ON),
            KeyEvent.KEYCODE_META_RIGHT to (KeyEvent.META_META_ON or KeyEvent.META_META_RIGHT_ON)
        )
    }
}
