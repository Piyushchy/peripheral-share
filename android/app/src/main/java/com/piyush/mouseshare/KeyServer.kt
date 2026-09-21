package com.piyush.mouseshare

import android.os.IBinder
import android.view.InputDevice
import android.view.InputEvent
import android.view.KeyEvent
import android.view.MotionEvent
import java.io.IOException
import java.lang.reflect.Method
import java.net.InetAddress
import java.net.ServerSocket
import java.net.Socket
import kotlin.concurrent.thread
import kotlin.system.exitProcess

/**
 * The "real keyboard" helper. Android does not let an ordinary app (not even an accessibility
 * service) put key events into the system's input pipeline, so Alt+Tab and other system
 * shortcuts cannot be produced from inside the app. The shell user (what `adb shell` runs as)
 * may, so this class is started once per phone boot from the PC through adb:
 *
 *     CLASSPATH=<this app's apk> app_process / com.piyush.mouseshare.KeyServer <token>
 *
 * It is NOT a normal Android app process (no Context, no Looper) - it only uses system
 * services through reflection, the same way `adb shell input` and scrcpy do.
 *
 * Security: it listens on 127.0.0.1 only and every connection must present the random token
 * the PC generated for this run (the MouseShare service learns it over the PIN-protected link).
 *
 * Protocol, one line each:
 *   AUTH <token>       -> "OK <how keys are injected>"  or  "ERR <reason>"
 *   KD <code> [repeat] -> key down (Android keycode)
 *   KU <code>          -> key up
 *   MM <x> <y>         -> mouse moved to this screen position (pixels)
 *   MB <mask> <1|0>    -> mouse button down/up (MotionEvent.BUTTON_*: 1 left, 2 right, 4 middle)
 *   MW <h> <v>         -> wheel turned (notches; v > 0 = up, h > 0 = right)
 *   RESET              -> release every key and button that is still down
 *   QUIT               -> stop the helper
 */
/** Where the helper's key events end up: the real system injector, or a fake one in tests. */
interface KeySink {
    val ready: Boolean
    val name: String
    val error: String
    fun inject(down: Boolean, code: Int, meta: Int, repeat: Int, downTime: Long, eventTime: Long): Boolean
    fun injectMotion(
        action: Int, x: Float, y: Float, buttons: Int, actionButton: Int,
        hscroll: Float, vscroll: Float, downTime: Long, eventTime: Long
    ): Boolean
}

/** Networking + key-state logic of the helper. Free of Android classes so it is unit-testable. */
class KeyServerCore(
    private val token: String,
    private val sink: KeySink,
    private val onQuit: () -> Unit = { exitProcess(0) }
) {
    @Volatile private var current: Socket? = null
    private val state = RawKeyState()
    private val downTimes = HashMap<Int, Long>()
    private val mouse = RawMouseState()
    private var mouseDownTime = 0L
    private val lock = Any()

    /** Bind (localhost only) and accept clients on background threads. */
    fun start(port: Int): ServerSocket {
        val server = ServerSocket(port, 4, InetAddress.getByName("127.0.0.1"))
        thread(name = "keyserver-accept", isDaemon = true) {
            while (!server.isClosed) {
                val s = try { server.accept() } catch (e: IOException) { break }
                thread(name = "keyserver-client", isDaemon = true) { serve(s) }
            }
        }
        return server
    }

    private fun now(): Long = System.nanoTime() / 1_000_000L    // same clock as SystemClock.uptimeMillis()

    private fun serve(s: Socket) {
        try {
            s.tcpNoDelay = true
            s.soTimeout = 5000                                  // must authenticate quickly
            val reader = s.getInputStream().bufferedReader()
            val out = s.getOutputStream()
            val first = reader.readLine()?.trim() ?: return
            val parts = first.split(' ')
            if (parts.size != 2 || parts[0] != "AUTH" || !constantTimeEquals(parts[1], token)) {
                out.write("ERR bad token\n".toByteArray()); out.flush()
                return
            }
            if (!sink.ready) {
                out.write("ERR ${sink.error}\n".toByteArray()); out.flush()
                return
            }
            synchronized(lock) {
                current?.let { try { it.close() } catch (e: IOException) {} }   // newest connection wins
                current = s
                releaseAll()                                    // keys the old connection left down
            }
            out.write("OK ${sink.name}\n".toByteArray()); out.flush()
            s.soTimeout = 0

            while (true) {
                val line = reader.readLine() ?: break
                if (line.trim() == "QUIT") { onQuit(); break }
                handle(line)
            }
        } catch (e: IOException) {
        } finally {
            try { s.close() } catch (e: IOException) {}
            synchronized(lock) {
                if (current === s) {
                    current = null
                    releaseAll()                                // never leave Alt/Ctrl stuck
                }
            }
        }
    }

    private fun constantTimeEquals(a: String, b: String): Boolean {
        if (a.length != b.length) return false
        var diff = 0
        for (i in a.indices) diff = diff or (a[i].code xor b[i].code)
        return diff == 0
    }

    private fun handle(line: String) {
        val p = line.trim().split(' ')
        try {
            when (p[0]) {
                "KD" -> keyDown(p[1].toInt(), (p.getOrNull(2)?.toInt() ?: 0) != 0)
                "KU" -> keyUp(p[1].toInt())
                "MM" -> mouseEvents { mouse.move(p[1].toFloat(), p[2].toFloat()) }
                "MB" -> mouseEvents { mouse.button(p[1].toInt(), p[2] == "1") }
                "MW" -> mouseEvents { mouse.scroll(p[1].toFloat(), p[2].toFloat()) }
                "RESET" -> synchronized(lock) { releaseAll() }
            }
        } catch (e: Exception) {
            // malformed line: ignore
        }
    }

    private fun keyDown(code: Int, repeat: Boolean) = synchronized(lock) {
        val ev = state.down(code, repeat) ?: return@synchronized
        val t = now()
        if (ev.repeat == 0) downTimes[code] = t
        sink.inject(true, ev.code, ev.meta, ev.repeat, downTimes[code] ?: t, t)
        Unit
    }

    private fun keyUp(code: Int) = synchronized(lock) {
        val ev = state.up(code) ?: return@synchronized
        val t = now()
        sink.inject(false, ev.code, ev.meta, 0, downTimes.remove(code) ?: t, t)
        Unit
    }

    private fun mouseEvents(make: () -> List<RawMouseState.MEv>) = synchronized(lock) {
        sendMouse(make())
    }

    private fun sendMouse(events: List<RawMouseState.MEv>) {
        for (e in events) {
            val t = now()
            if (e.action == MotionEvent.ACTION_DOWN) mouseDownTime = t
            // hover and wheel events are not part of a press, so they carry their own time; every
            // event of a press (DOWN, PRESS, MOVE, RELEASE, UP) carries the time of its DOWN
            val downTime = if (e.action == MotionEvent.ACTION_HOVER_MOVE ||
                e.action == MotionEvent.ACTION_SCROLL) t else mouseDownTime
            sink.injectMotion(e.action, e.x, e.y, e.buttons, e.actionButton, e.hscroll, e.vscroll, downTime, t)
        }
    }

    private fun releaseAll() {
        val t = now()
        for (ev in state.releaseAll()) {
            sink.inject(false, ev.code, ev.meta, 0, downTimes.remove(ev.code) ?: t, t)
        }
        sendMouse(mouse.releaseAll())
    }
}

object KeyServer {

    const val PORT = 47003

    @JvmStatic
    fun main(args: Array<String>) {
        val token = args.firstOrNull()
        if (token.isNullOrBlank()) {
            System.err.println("usage: KeyServer <token>")
            exitProcess(2)
        }
        val server = try {
            KeyServerCore(token, SystemInjector()).start(PORT)
        } catch (e: IOException) {
            System.err.println("cannot listen on $PORT: ${e.message}")
            exitProcess(3)
        }
        while (!server.isClosed) {
            try { Thread.sleep(60_000) } catch (e: InterruptedException) { break }
        }
    }
}

/**
 * Hands a KeyEvent to Android's InputManager service. The method moved between Android
 * versions, so several routes are tried; the first that resolves is used, and if a call
 * later fails the next route takes over.
 */
class SystemInjector : KeySink {

    private class Route(val label: String, val target: Any, val method: Method, val threeArg: Boolean)

    private val routes = ArrayList<Route>()
    private var active = 0

    override val ready: Boolean get() = routes.isNotEmpty()
    override val name: String get() = routes.getOrNull(active)?.label ?: "none"
    override var error: String = "no way to reach the input service found"
        private set

    init {
        val problems = ArrayList<String>()

        // 1. Android 14+: InputManagerGlobal.getInstance()
        tryStatic("android.hardware.input.InputManagerGlobal", "InputManagerGlobal", problems)

        // 2. talk to the "input" system service directly (works on every version)
        try {
            val sm = Class.forName("android.os.ServiceManager")
            val binder = sm.getMethod("getService", String::class.java).invoke(null, "input") as? IBinder
            if (binder != null) {
                val stub = Class.forName("android.hardware.input.IInputManager\$Stub")
                val proxy = stub.getMethod("asInterface", IBinder::class.java).invoke(null, binder)
                val iface = Class.forName("android.hardware.input.IInputManager")
                if (proxy != null) addRoute("IInputManager", proxy, iface)
            }
        } catch (e: Throwable) {
            problems.add("service: ${e.javaClass.simpleName}")
        }

        // 3. older: InputManager.getInstance()
        tryStatic("android.hardware.input.InputManager", "InputManager", problems)

        if (routes.isEmpty()) error = problems.joinToString("; ").ifEmpty { error }
    }

    private fun tryStatic(className: String, label: String, problems: MutableList<String>) {
        try {
            val cls = Class.forName(className)
            val get = cls.getDeclaredMethod("getInstance")
            get.isAccessible = true
            val inst = get.invoke(null)
            if (inst != null) addRoute(label, inst, inst.javaClass)
        } catch (e: Throwable) {
            problems.add("$label: ${e.javaClass.simpleName}")
        }
    }

    private fun addRoute(label: String, target: Any, cls: Class<*>) {
        val int = Int::class.javaPrimitiveType
        try {
            val m = cls.getMethod("injectInputEvent", InputEvent::class.java, int)
            m.isAccessible = true
            routes.add(Route(label, target, m, false))
            return
        } catch (e: NoSuchMethodException) {
        }
        try {
            val m = cls.getMethod("injectInputEvent", InputEvent::class.java, int, int)
            m.isAccessible = true
            routes.add(Route(label, target, m, true))
        } catch (e: NoSuchMethodException) {
        }
    }

    override fun inject(down: Boolean, code: Int, meta: Int, repeat: Int, downTime: Long, eventTime: Long): Boolean {
        val ev = KeyEvent(
            downTime, eventTime,
            if (down) KeyEvent.ACTION_DOWN else KeyEvent.ACTION_UP,
            code, repeat, meta,
            -1,                                   // KeyCharacterMap.VIRTUAL_KEYBOARD
            0, 0, InputDevice.SOURCE_KEYBOARD
        )
        return injectEvent(ev)
    }

    override fun injectMotion(
        action: Int, x: Float, y: Float, buttons: Int, actionButton: Int,
        hscroll: Float, vscroll: Float, downTime: Long, eventTime: Long
    ): Boolean {
        val props = MotionEvent.PointerProperties()
        props.id = 0
        props.toolType = MotionEvent.TOOL_TYPE_MOUSE
        val coords = MotionEvent.PointerCoords()
        coords.x = x
        coords.y = y
        coords.pressure = if (buttons != 0) 1f else 0f
        coords.size = 1f
        coords.setAxisValue(MotionEvent.AXIS_HSCROLL, hscroll)
        coords.setAxisValue(MotionEvent.AXIS_VSCROLL, vscroll)
        val ev = MotionEvent.obtain(
            downTime, eventTime, action, 1, arrayOf(props), arrayOf(coords),
            0, buttons, 1f, 1f,
            0,                                    // device id, as scrcpy uses for injected mouse events
            0, InputDevice.SOURCE_MOUSE, 0
        )
        if (actionButton != 0) {
            try {                                 // hidden setter; without it apps just see 0
                MotionEvent::class.java.getMethod("setActionButton", Int::class.javaPrimitiveType)
                    .invoke(ev, actionButton)
            } catch (e: Throwable) {
            }
        }
        return try { injectEvent(ev) } finally { ev.recycle() }
    }

    /** Inject one event asynchronously. False if every route failed. */
    private fun injectEvent(ev: InputEvent): Boolean {
        var i = active
        var tried = 0
        while (tried < routes.size) {
            val r = routes[i]
            try {
                val res = if (r.threeArg) r.method.invoke(r.target, ev, 0, -1)     // ASYNC, any target uid
                else r.method.invoke(r.target, ev, 0)                               // ASYNC
                if (res != false) {
                    active = i
                    return true
                }
            } catch (e: Throwable) {
            }
            i = (i + 1) % routes.size
            tried++
        }
        return false
    }
}
