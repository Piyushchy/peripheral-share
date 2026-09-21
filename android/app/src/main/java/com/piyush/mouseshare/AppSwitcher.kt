package com.piyush.mouseshare

import android.accessibilityservice.AccessibilityService
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.PixelFormat
import android.graphics.drawable.Drawable
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.view.Gravity
import android.view.WindowManager
import android.view.inputmethod.InputMethodManager

/**
 * Our own Alt+Tab, independent of whatever launcher the phone uses.
 *
 *  - Watches which app is on screen (accessibility window-change events) to keep a
 *    most-recently-used list. The list builds up as you use apps after the service starts.
 *  - Alt+Tab shows a strip of those apps ([SwitcherView]); each Tab slides the highlight.
 *  - Releasing Alt launches the highlighted app. Launching an app's normal launcher intent
 *    brings its existing task to the front exactly as tapping its icon does, and a bound
 *    accessibility service is allowed to start activities from the background.
 *
 * Everything here runs on the main thread.
 */
class AppSwitcher(private val service: AccessibilityService) {

    private val main = Handler(Looper.getMainLooper())
    private val pm: PackageManager = service.packageManager
    private val wm = service.getSystemService(Context.WINDOW_SERVICE) as WindowManager
    private val ownPkg = service.packageName

    private val model = SwitcherModel()
    private var view: SwitcherView? = null

    private var homePkgs: Set<String> = emptySet()
    private var imePkgs: Set<String> = emptySet()
    private var lookupAt = 0L

    private val watchdog = Runnable { cancel() }

    // ------------------------------------------------------------ tracking
    /** Feed every window-state change here. */
    fun onWindowEvent(pkg: String) {
        if (pkg == ownPkg || pkg == "android" || pkg == "com.android.systemui") return
        if (model.isSessionActive) return                 // ignore churn while the strip is open
        refreshLookups()
        if (pkg in imePkgs) return                        // the keyboard is not an app you switch to
        if (pkg in homePkgs) {
            model.setHome()
            return
        }
        if (!isLaunchable(pkg)) return                    // dialogs, share sheets, permission popups...
        model.touch(pkg)
    }

    private fun isLaunchable(pkg: String) = pm.getLaunchIntentForPackage(pkg) != null

    @Suppress("DEPRECATION")
    private fun refreshLookups() {
        val now = SystemClock.uptimeMillis()
        if (now - lookupAt < 30_000 && homePkgs.isNotEmpty()) return
        lookupAt = now
        try {
            val home = Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_HOME)
            homePkgs = pm.queryIntentActivities(home, 0).map { it.activityInfo.packageName }.toSet()
        } catch (e: Exception) {
        }
        try {
            val imm = service.getSystemService(Context.INPUT_METHOD_SERVICE) as InputMethodManager
            imePkgs = imm.enabledInputMethodList.map { it.packageName }.toSet()
        } catch (e: Exception) {
        }
    }

    // ------------------------------------------------------------- session
    /**
     * Alt+Tab (dir = 1) or Shift+Alt+Tab (dir = -1). The first call opens the strip, later calls
     * slide the highlight. Returns false when there are not enough known apps to switch between.
     */
    fun step(dir: Int): Boolean {
        if (!model.isSessionActive) {
            refreshLookups()
            model.retain { isLaunchable(it) }
            if (!model.begin(dir)) return false
            showOverlay()
        } else {
            model.move(dir)
            view?.select(model.sel)
        }
        main.removeCallbacks(watchdog)
        main.postDelayed(watchdog, WATCHDOG_MS)           // in case the Alt-release never arrives
        return true
    }

    /** Alt released: open the highlighted app and close the strip. */
    fun finish() {
        if (!model.isSessionActive) return
        val label = label(model.items[model.sel])
        val pkg = model.end()
        var ok = true
        if (pkg != null) ok = launch(pkg)                 // launch while the overlay is still visible
        hideOverlay()
        main.removeCallbacks(watchdog)
        KeyInput.lastResult = when {
            pkg == null -> "Alt+Tab: stayed on the current app"
            ok -> "Alt+Tab: switched to $label"
            else -> "Alt+Tab: could not open $label"
        }
        if (!ok) service.performGlobalAction(AccessibilityService.GLOBAL_ACTION_RECENTS)
    }

    /** Close the strip without opening anything (Esc, Home, a click, disconnect...). */
    fun cancel() {
        model.cancel()
        hideOverlay()
        main.removeCallbacks(watchdog)
    }

    private fun launch(pkg: String): Boolean {
        val intent = pm.getLaunchIntentForPackage(pkg) ?: return false
        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_RESET_TASK_IF_NEEDED)
        return try {
            service.startActivity(intent)
            true
        } catch (e: Exception) {
            false
        }
    }

    // ------------------------------------------------------------- overlay
    private fun showOverlay() {
        hideOverlay()
        val items = model.items
        val v = SwitcherView(
            service, items.map { label(it) }, items.map { icon(it) },
            model.sel, service.resources.displayMetrics.widthPixels
        )
        val lp = WindowManager.LayoutParams(
            WindowManager.LayoutParams.WRAP_CONTENT,
            WindowManager.LayoutParams.WRAP_CONTENT,
            WindowManager.LayoutParams.TYPE_ACCESSIBILITY_OVERLAY,
            WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or
                WindowManager.LayoutParams.FLAG_NOT_TOUCHABLE or
                WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN,
            PixelFormat.TRANSLUCENT
        )
        lp.gravity = Gravity.CENTER
        try {
            wm.addView(v, lp)
            view = v
        } catch (e: Exception) {
            view = null
        }
    }

    private fun hideOverlay() {
        val v = view ?: return
        view = null
        try {
            wm.removeView(v)
        } catch (e: Exception) {
        }
    }

    // --------------------------------------------------------------- labels
    @Suppress("DEPRECATION")
    private fun label(pkg: String): String = try {
        pm.getApplicationLabel(pm.getApplicationInfo(pkg, 0)).toString()
    } catch (e: Exception) {
        pkg
    }

    private fun icon(pkg: String): Drawable? = try {
        pm.getApplicationIcon(pkg)
    } catch (e: Exception) {
        null
    }

    /** For the setup screen: which apps Alt+Tab currently knows about. */
    fun summary(): String {
        val apps = model.recent()
        return if (apps.isEmpty()) "none yet - use a few apps on the phone first"
        else apps.joinToString(", ") { label(it) }
    }

    companion object {
        private const val WATCHDOG_MS = 10_000L
    }
}
