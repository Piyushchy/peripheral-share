package com.piyush.mouseshare

import android.view.MotionEvent

/**
 * Turns "the mouse moved / a button changed / the wheel turned" into the exact MotionEvent
 * sequence a physical mouse produces on Android. Pure logic (only compile-time constants from
 * android.view.MotionEvent), so it is unit-tested on a plain JVM.
 *
 * A real mouse looks like this to apps - and none of it is a touch, so none of it can trigger
 * the system edge gestures (pull-down shade, home/back swipes) that a finger swipe does:
 *   - moving with no button held      -> ACTION_HOVER_MOVE
 *   - first button goes down          -> ACTION_DOWN, then ACTION_BUTTON_PRESS
 *   - moving while a button is held   -> ACTION_MOVE  (a drag)
 *   - another button goes down        -> ACTION_BUTTON_PRESS
 *   - a button is released            -> ACTION_BUTTON_RELEASE, and ACTION_UP after the last one
 *   - the wheel turns                 -> ACTION_SCROLL with AXIS_VSCROLL / AXIS_HSCROLL
 */
class RawMouseState {

    class MEv(
        val action: Int,
        val x: Float,
        val y: Float,
        val buttons: Int,          // button state after this event
        val actionButton: Int,     // which button changed (BUTTON_PRESS / BUTTON_RELEASE only)
        val hscroll: Float,
        val vscroll: Float
    )

    var x = 0f
        private set
    var y = 0f
        private set
    private var buttons = 0

    val anyButton: Boolean get() = buttons != 0

    fun move(nx: Float, ny: Float): List<MEv> {
        x = nx
        y = ny
        val action = if (buttons != 0) MotionEvent.ACTION_MOVE else MotionEvent.ACTION_HOVER_MOVE
        return listOf(ev(action))
    }

    /** [mask] is a MotionEvent.BUTTON_* value (primary 1, secondary 2, tertiary 4). */
    fun button(mask: Int, down: Boolean): List<MEv> {
        if (mask == 0) return emptyList()
        val out = ArrayList<MEv>(2)
        if (down) {
            if ((buttons and mask) != 0) return emptyList()          // already down
            val first = buttons == 0
            buttons = buttons or mask
            if (first) out.add(ev(MotionEvent.ACTION_DOWN))
            out.add(ev(MotionEvent.ACTION_BUTTON_PRESS, mask))
        } else {
            if ((buttons and mask) == 0) return emptyList()          // was never down
            buttons = buttons and mask.inv()
            out.add(ev(MotionEvent.ACTION_BUTTON_RELEASE, mask))
            if (buttons == 0) out.add(ev(MotionEvent.ACTION_UP))
        }
        return out
    }

    /** Wheel: [v] > 0 scrolls up, [h] > 0 scrolls right (Android's axis convention), in notches. */
    fun scroll(h: Float, v: Float): List<MEv> {
        if (h == 0f && v == 0f) return emptyList()
        return listOf(MEv(MotionEvent.ACTION_SCROLL, x, y, buttons, 0, h, v))
    }

    /** Let go of everything (pointer went back to the PC, link dropped). */
    fun releaseAll(): List<MEv> {
        val out = ArrayList<MEv>()
        var bit = 1
        while (buttons != 0 && bit <= 0x10) {
            if ((buttons and bit) != 0) out.addAll(button(bit, false))
            bit = bit shl 1
        }
        return out
    }

    private fun ev(action: Int, actionButton: Int = 0) =
        MEv(action, x, y, buttons, actionButton, 0f, 0f)
}
