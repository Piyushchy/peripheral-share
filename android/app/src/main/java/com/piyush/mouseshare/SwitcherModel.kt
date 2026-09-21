package com.piyush.mouseshare

/**
 * The bookkeeping behind the Alt+Tab switcher. No Android classes in here so it can be
 * unit-tested anywhere.
 *
 * Android does not let a normal app read the Recents list, so we build our own
 * most-recently-used list from the window changes the accessibility service sees.
 *
 *  - [touch]      an app came to the front            -> moves to the top of the list
 *  - [setHome]    the home screen / Recents came up    -> nothing is "current"
 *  - [begin]      Alt+Tab pressed: freeze a snapshot and pick the first target
 *  - [move]       Tab / Shift+Tab while Alt is held: move the highlight (wraps around)
 *  - [end]        Alt released: returns the package to open (or null to stay put)
 */
class SwitcherModel(private val maxShown: Int = 8, private val keep: Int = 24) {

    private val mru = ArrayList<String>()          // most recent first
    private var current: String? = null            // app on screen right now, null = home/other

    /** Snapshot taken by [begin]; empty when no switcher session is open. */
    var items: List<String> = emptyList()
        private set

    /** Highlighted index in [items]. */
    var sel: Int = 0
        private set

    val isSessionActive: Boolean get() = items.isNotEmpty()

    /** True when items[0] is the app already on screen (Windows-style: Tab skips past it). */
    private var currentInList = false

    // ------------------------------------------------------------------ tracking
    fun touch(pkg: String) {
        mru.remove(pkg)
        mru.add(0, pkg)
        while (mru.size > keep) mru.removeAt(mru.size - 1)
        current = pkg
    }

    fun setHome() {
        current = null
    }

    /** Drop apps that can no longer be launched (uninstalled, disabled). */
    fun retain(valid: (String) -> Boolean) {
        mru.retainAll { valid(it) }
        if (current != null && current !in mru) current = null
    }

    fun recent(): List<String> = mru.take(maxShown)

    // ------------------------------------------------------------------- session
    /** Start a session. dir > 0 = Alt+Tab, dir < 0 = Shift+Alt+Tab. False if there is nothing to switch to. */
    fun begin(dir: Int): Boolean {
        val list = recent()
        val inList = current != null && list.firstOrNull() == current
        val needed = if (inList) 2 else 1
        if (list.size < needed) return false
        items = list
        currentInList = inList
        sel = if (dir > 0) (if (inList) 1 else 0) else list.size - 1
        return true
    }

    fun move(dir: Int) {
        val n = items.size
        if (n == 0) return
        sel = ((sel + (if (dir > 0) 1 else -1)) % n + n) % n
    }

    /** Alt released. Returns the package to open, or null if the highlight is on the app already showing. */
    fun end(): String? {
        if (!isSessionActive) return null
        val target = if (currentInList && sel == 0) null else items[sel]
        items = emptyList()
        sel = 0
        if (target != null) touch(target)          // so a quick second Alt+Tab sees the new order
        return target
    }

    /** Abandon the session without opening anything. */
    fun cancel() {
        items = emptyList()
        sel = 0
    }
}
