package com.piyush.mouseshare

import android.content.Context
import kotlin.random.Random

object Prefs {
    private const val FILE = "mouseshare"

    private fun sp(ctx: Context) = ctx.getSharedPreferences(FILE, Context.MODE_PRIVATE)

    /** The PIN the Windows app must send. Generated on first use. */
    fun pin(ctx: Context): String {
        val existing = sp(ctx).getString("pin", null)
        if (!existing.isNullOrBlank()) return existing
        val fresh = Random.nextInt(100000, 1000000).toString()
        sp(ctx).edit().putString("pin", fresh).apply()
        return fresh
    }

    fun setPin(ctx: Context, pin: String) {
        sp(ctx).edit().putString("pin", pin.trim()).apply()
    }

    fun cursorDp(ctx: Context): Int = sp(ctx).getInt("cursor_dp", 24)

    fun setCursorDp(ctx: Context, dp: Int) {
        sp(ctx).edit().putInt("cursor_dp", dp).apply()
    }
}
