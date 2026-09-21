package com.piyush.mouseshare

import java.util.Locale

/**
 * The helper commands the phone service sends for mouse input (see [KeyServer]). Pure, so the
 * mapping from the PC's button names to Android's is unit-tested.
 *
 * PC button names: L left, R right, M middle, X1 / X2 the two side buttons.
 */
object MouseLines {

    private const val BUTTON_PRIMARY = 1      // MotionEvent.BUTTON_PRIMARY
    private const val BUTTON_SECONDARY = 2
    private const val BUTTON_TERTIARY = 4
    private const val KEYCODE_BACK = 4        // KeyEvent.KEYCODE_BACK
    private const val KEYCODE_FORWARD = 125

    /** Pointer moved to (x, y) screen pixels. */
    fun hover(x: Float, y: Float): String = String.format(Locale.US, "MM %.1f %.1f", x, y)

    /** Wheel notches: v > 0 up, h > 0 right (the PC already applied speed and natural scrolling). */
    fun wheel(h: Float, v: Float): String = String.format(Locale.US, "MW %.3f %.3f", h, v)

    /**
     * A PC button press/release as helper lines. Left/right/middle become real mouse buttons; the
     * side buttons become Back / Forward key presses. [rightClickBack] additionally sends Back on
     * right-click for phones that do not turn a secondary click into Back themselves.
     */
    fun button(name: String, down: Boolean, rightClickBack: Boolean): List<String> {
        val d = if (down) 1 else 0
        val mask = when (name) {
            "L" -> BUTTON_PRIMARY
            "R" -> BUTTON_SECONDARY
            "M" -> BUTTON_TERTIARY
            else -> 0
        }
        if (mask != 0) {
            val out = arrayListOf("MB $mask $d")
            if (name == "R" && rightClickBack) out.add(key(KEYCODE_BACK, down))
            return out
        }
        return when (name) {
            "X1" -> listOf(key(KEYCODE_BACK, down))
            "X2" -> listOf(key(KEYCODE_FORWARD, down))
            else -> emptyList()
        }
    }

    private fun key(code: Int, down: Boolean) = if (down) "KD $code 0" else "KU $code"
}
