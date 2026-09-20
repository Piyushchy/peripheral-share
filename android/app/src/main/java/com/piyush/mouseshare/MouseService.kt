package com.piyush.mouseshare

import android.accessibilityservice.AccessibilityService
import android.accessibilityservice.GestureDescription
import android.accessibilityservice.InputMethod
import android.annotation.TargetApi
import android.accessibilityservice.GestureDescription.StrokeDescription
import android.content.Context
import android.content.res.Configuration
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.Path
import android.graphics.PixelFormat
import android.net.wifi.WifiManager
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.util.DisplayMetrics
import android.view.Choreographer
import android.view.Gravity
import android.view.View
import android.view.WindowManager
import android.view.accessibility.AccessibilityEvent
import java.io.IOException
import java.io.OutputStream
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.InetSocketAddress
import java.net.ServerSocket
import java.net.Socket
import java.util.Base64
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicBoolean
import kotlin.concurrent.thread
import kotlin.math.abs
import kotlin.math.hypot

/**
 * Phone side of MouseShare.
 *
 * Listens on TCP [PORT]. The Windows app connects, sends the PIN, then streams:
 *   P fx fy        absolute cursor position (0..1)
 *   D/U btn        button down/up   (L = touch, R = Back, M = Home, X1 = Back, X2 = Recents)
 *   S n / H n      vertical / horizontal wheel notches
 *   KT b64         type text            KE code mods   real key event (Android keycode + shift/alt/ctrl)
 *   NAV what       BACK / HOME / RECENTS / ALTTAB / NOTIFICATIONS
 *   ACTIVE 1|0     show / hide the cursor
 *   PING           answered with PONG
 * See windows/bridge.py for the full protocol.
 */
class MouseService : AccessibilityService() {

    companion object {
        const val PORT = 5599
        const val DISCOVERY_PORT = 5598

        @Volatile var instance: MouseService? = null
        @Volatile var status: String = "Starting..."
        @Volatile var lastCommand: String = "-"

        /**
         * Alt+Tab on the PC = "jump to the previous app" (Win+Tab stays the Recents overview).
         * It is done by pressing Recents twice, which most launchers treat as a quick switch.
         * If your launcher behaves oddly, set this to false: Alt+Tab then just opens Recents.
         */
        const val ALT_TAB_QUICK_SWITCH = true
        private const val ALT_TAB_GAP_MS = 700L        // ignore a second Alt+Tab this soon after the first
        private const val QUICK_SWITCH_DELAY_MS = 150L // gap between the two Recents presses
    }

    private val main = Handler(Looper.getMainLooper())
    private val lock = Any()

    private lateinit var wm: WindowManager
    private var wifiLock: WifiManager.WifiLock? = null
    private var sendExec: ExecutorService? = null

    @Volatile private var serverSocket: ServerSocket? = null
    @Volatile private var discoverySocket: DatagramSocket? = null
    @Volatile private var stopping = false
    private var client: Socket? = null          // guarded by lock
    private var writer: OutputStream? = null    // guarded by lock

    @Volatile private var screenW = 1080
    @Volatile private var screenH = 2400

    // ---- cursor (position written from the network thread, drawn once per frame on main)
    @Volatile private var pendingX = 0f
    @Volatile private var pendingY = 0f
    private var curX = 0f
    private var curY = 0f
    private var active = false
    private var cursorView: CursorView? = null
    private var cursorParams: WindowManager.LayoutParams? = null
    private var cursorAttached = false
    private val frameScheduled = AtomicBoolean(false)
    private val frameCallback = Choreographer.FrameCallback {
        frameScheduled.set(false)
        applyPending()
        pump()
    }

    // ---- gesture state (main thread only)
    private var leftDown = false
    private var suppressed = false        // a cancelled press is ignored until the button is released
    private var inFlight = false
    private var activeStroke: StrokeDescription? = null
    private var strokeX = 0f
    private var strokeY = 0f
    private var scrollY = 0f
    private var scrollX = 0f

    // ============================================================ lifecycle
    override fun onServiceConnected() {
        super.onServiceConnected()
        instance = this
        stopping = false
        wm = getSystemService(Context.WINDOW_SERVICE) as WindowManager
        readScreenSize()
        sendExec = Executors.newSingleThreadExecutor()
        acquireWifiLock()
        startServer()
        startDiscoveryResponder()
    }

    /** Lets [KeyInput] attach to the focused text field and send real key events (Android 13+). */
    @TargetApi(33)
    override fun onCreateInputMethod(): InputMethod = KeyInputMethod(this)

    override fun onAccessibilityEvent(event: AccessibilityEvent?) {}

    override fun onInterrupt() {}

    override fun onConfigurationChanged(newConfig: Configuration) {
        super.onConfigurationChanged(newConfig)
        readScreenSize()
        sendAsync(infoLine())
    }

    override fun onUnbind(intent: android.content.Intent?): Boolean {
        shutdown()
        return super.onUnbind(intent)
    }

    override fun onDestroy() {
        shutdown()
        super.onDestroy()
    }

    private fun shutdown() {
        stopping = true
        try { serverSocket?.close() } catch (e: IOException) {}
        try { discoverySocket?.close() } catch (e: Exception) {}
        synchronized(lock) {
            client?.closeQuietly()
            client = null
            writer = null
        }
        try { wifiLock?.release() } catch (e: Exception) {}
        wifiLock = null
        sendExec?.shutdownNow()
        sendExec = null
        detachCursor()
        instance = null
        status = "Service stopped"
    }

    @Suppress("DEPRECATION")
    private fun acquireWifiLock() {
        try {
            val wifi = applicationContext.getSystemService(Context.WIFI_SERVICE) as WifiManager
            val mode = if (Build.VERSION.SDK_INT >= 29) WifiManager.WIFI_MODE_FULL_LOW_LATENCY
            else WifiManager.WIFI_MODE_FULL_HIGH_PERF
            wifiLock = wifi.createWifiLock(mode, "mouseshare").apply {
                setReferenceCounted(false)
                acquire()
            }
        } catch (e: Exception) {
            // not fatal, just slightly higher latency
        }
    }

    @Suppress("DEPRECATION")
    private fun readScreenSize() {
        if (Build.VERSION.SDK_INT >= 30) {
            val b = wm.currentWindowMetrics.bounds
            screenW = b.width()
            screenH = b.height()
        } else {
            val dm = DisplayMetrics()
            wm.defaultDisplay.getRealMetrics(dm)
            screenW = dm.widthPixels
            screenH = dm.heightPixels
        }
    }

    // ============================================================= network
    private fun startServer() {
        thread(name = "mouseshare-accept", isDaemon = true) {
            try {
                val ss = ServerSocket()
                ss.reuseAddress = true
                ss.bind(InetSocketAddress(PORT))
                serverSocket = ss
                status = "Waiting for Windows (port $PORT)"
                while (!ss.isClosed) {
                    val s = ss.accept()
                    thread(name = "mouseshare-client", isDaemon = true) { handleClient(s) }
                }
            } catch (e: IOException) {
                if (!stopping) status = "Server error: ${e.message}"
            }
        }
    }

    private fun handleClient(s: Socket) {
        try {
            s.tcpNoDelay = true
            s.soTimeout = 6000                       // Windows pings every 2 s
            val reader = s.getInputStream().bufferedReader()
            val out = s.getOutputStream()

            val hello = reader.readLine() ?: return
            if (hello.trim() == "PROBE") {                 // discovery handshake, then hang up
                out.write((infoLine() + "\n").toByteArray())
                out.flush()
                return
            }
            val parts = hello.trim().split(" ", limit = 2)
            if (parts[0] != "HELLO" || parts.getOrNull(1)?.trim() != Prefs.pin(this)) {
                out.write("NO\n".toByteArray())
                out.flush()
                return
            }
            synchronized(lock) {
                client?.closeQuietly()               // newest connection wins
                client = s
                writer = out
            }
            send("OK")
            send(infoLine())
            status = "Connected to ${s.inetAddress.hostAddress}"

            while (true) {
                val line = reader.readLine() ?: break
                handleLine(line)
            }
        } catch (e: IOException) {
            // connection dropped or timed out
        } finally {
            s.closeQuietly()
            synchronized(lock) {
                if (client === s) {
                    client = null
                    writer = null
                    main.post { setActive(false) }
                    status = "Waiting for Windows (port $PORT)"
                }
            }
        }
    }

    private fun send(line: String) {
        synchronized(lock) {
            try {
                writer?.write((line + "\n").toByteArray())
                writer?.flush()
            } catch (e: IOException) {
            }
        }
    }

    private fun sendAsync(line: String) {
        try { sendExec?.execute { send(line) } } catch (e: Exception) {}
    }

    private fun handleLine(line: String) {
        val p = line.trim().split(' ')
        if (p[0] != "P" && p[0] != "PING") lastCommand = line.trim().take(60)
        try {
            when (p[0]) {
                "P" -> {
                    pendingX = p[1].toFloat() * screenW
                    pendingY = p[2].toFloat() * screenH
                    requestFrame()
                }
                "D" -> { val b = p[1]; main.post { syncPos(); onButton(b, true) } }
                "U" -> { val b = p[1]; main.post { syncPos(); onButton(b, false) } }
                "S" -> { val n = p[1].toFloat(); main.post { scrollY += n; pump() } }
                "H" -> { val n = p[1].toFloat(); main.post { scrollX += n; pump() } }
                "ACTIVE" -> { val on = p.getOrNull(1) == "1"; main.post { setActive(on) } }
                "KT" -> {
                    val text = String(Base64.getDecoder().decode(p[1]), Charsets.UTF_8)
                    main.post { KeyInput.type(this, text) }
                }
                "KE" -> {
                    val code = p[1].toInt()
                    val mods = p.getOrNull(2)?.toInt() ?: 0
                    main.post { KeyInput.press(this, code, mods) }
                }
                "KK" -> { val name = p[1]; main.post { KeyInput.special(this, name) } }
                "KC" -> { val letter = p[1]; main.post { KeyInput.ctrl(this, letter) } }
                "NAV" -> { val what = p[1]; main.post { navigate(what) } }
                "PING" -> send("PONG")
            }
        } catch (e: Exception) {
            // malformed line: ignore
        }
    }

    // ============================================================== cursor
    private fun requestFrame() {
        if (frameScheduled.compareAndSet(false, true)) {
            main.post { Choreographer.getInstance().postFrameCallback(frameCallback) }
        }
    }

    private fun syncPos() {
        curX = pendingX.coerceIn(0f, (screenW - 1).toFloat())
        curY = pendingY.coerceIn(0f, (screenH - 1).toFloat())
    }

    private fun applyPending() {
        syncPos()
        val lp = cursorParams ?: return
        if (!cursorAttached) return
        lp.x = curX.toInt()
        lp.y = curY.toInt()
        try { wm.updateViewLayout(cursorView, lp) } catch (e: Exception) {}
    }

    private fun setActive(on: Boolean) {
        active = on
        if (on) {
            syncPos()
            attachCursor()
        } else {
            leftDown = false
            scrollX = 0f
            scrollY = 0f
            pump()                       // lifts a finger that is still "down"
            detachCursor()
        }
    }

    private fun attachCursor() {
        if (cursorAttached) return
        val size = (Prefs.cursorDp(this) * resources.displayMetrics.density).toInt().coerceAtLeast(16)
        val lp = WindowManager.LayoutParams(
            size, size,
            WindowManager.LayoutParams.TYPE_ACCESSIBILITY_OVERLAY,
            WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or
                WindowManager.LayoutParams.FLAG_NOT_TOUCHABLE or
                WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN or
                WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS,
            PixelFormat.TRANSLUCENT
        )
        lp.gravity = Gravity.TOP or Gravity.START
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
            lp.layoutInDisplayCutoutMode =
                WindowManager.LayoutParams.LAYOUT_IN_DISPLAY_CUTOUT_MODE_SHORT_EDGES
        }
        lp.x = curX.toInt()
        lp.y = curY.toInt()
        val v = CursorView(this)
        try {
            wm.addView(v, lp)
            cursorView = v
            cursorParams = lp
            cursorAttached = true
        } catch (e: Exception) {
            status = "Cursor overlay failed: ${e.message}"
        }
    }

    private fun detachCursor() {
        if (!cursorAttached) return
        try { wm.removeView(cursorView) } catch (e: Exception) {}
        cursorView = null
        cursorParams = null
        cursorAttached = false
    }

    private fun deviceName(): String =
        (Build.MODEL ?: "phone").replace('\n', ' ').trim().ifEmpty { "phone" }

    private fun infoLine(): String = "INFO $screenW $screenH ${deviceName()}"

    private fun navigate(what: String) {
        KeyInput.lastResult = "system key -> $what"
        when (what) {
            "BACK" -> performGlobalAction(GLOBAL_ACTION_BACK)
            "HOME" -> performGlobalAction(GLOBAL_ACTION_HOME)
            "RECENTS" -> performGlobalAction(GLOBAL_ACTION_RECENTS)
            "ALTTAB" -> quickSwitch()
            "NOTIFICATIONS" -> performGlobalAction(GLOBAL_ACTION_NOTIFICATIONS)
        }
    }

    private var lastAltTab = 0L

    /** Alt+Tab: jump to the previous app. Rate limited so a held Tab key cannot flip back and forth. */
    private fun quickSwitch() {
        val now = SystemClock.uptimeMillis()
        if (now - lastAltTab < ALT_TAB_GAP_MS) return
        lastAltTab = now
        performGlobalAction(GLOBAL_ACTION_RECENTS)
        if (ALT_TAB_QUICK_SWITCH) {
            main.postDelayed({ performGlobalAction(GLOBAL_ACTION_RECENTS) }, QUICK_SWITCH_DELAY_MS)
        }
    }

    /** Answers "MOUSESHARE?" probes so the PC app can find this phone on the network. */
    private fun startDiscoveryResponder() {
        thread(name = "mouseshare-discovery", isDaemon = true) {
            try {
                val ds = DatagramSocket(null)
                ds.reuseAddress = true
                ds.broadcast = true
                ds.bind(InetSocketAddress(DISCOVERY_PORT))
                discoverySocket = ds
                val buf = ByteArray(64)
                while (!ds.isClosed) {
                    val packet = DatagramPacket(buf, buf.size)
                    ds.receive(packet)
                    val msg = String(packet.data, 0, packet.length).trim()
                    if (msg == "MOUSESHARE?") {
                        val reply = "MOUSESHARE $PORT $screenW $screenH ${deviceName()}"
                            .toByteArray()
                        ds.send(DatagramPacket(reply, reply.size, packet.address, packet.port))
                    }
                }
            } catch (e: Exception) {
                if (!stopping) status = "Discovery off: ${e.message}"
            }
        }
    }

    // ============================================================ gestures
    private fun onButton(name: String, down: Boolean) {
        when (name) {
            "L" -> {
                if (down) {
                    leftDown = true
                    suppressed = false
                } else {
                    leftDown = false
                }
                pump()
            }
            "R", "X1" -> if (down) performGlobalAction(GLOBAL_ACTION_BACK)
            "M" -> if (down) performGlobalAction(GLOBAL_ACTION_HOME)
            "X2" -> if (down) performGlobalAction(GLOBAL_ACTION_RECENTS)
        }
    }

    /** Drives the touch state machine. Only one gesture may be in flight at a time. */
    private fun pump() {
        if (inFlight) return
        val current = activeStroke
        if (current == null) {
            if (leftDown && !suppressed) {
                val path = Path().apply { moveTo(curX, curY) }
                strokeX = curX
                strokeY = curY
                dispatch(StrokeDescription(path, 0, 50, true), true)       // finger down
            } else if (scrollX != 0f || scrollY != 0f) {
                dispatchScroll()
            }
            return
        }
        val moved = abs(curX - strokeX) > 0.5f || abs(curY - strokeY) > 0.5f
        if (!leftDown) {
            dispatch(current.continueStroke(segmentToCursor(), 0, 30, false), false)   // finger up
        } else if (moved) {
            dispatch(current.continueStroke(segmentToCursor(), 0, 16, true), true)     // drag
        }
    }

    private fun segmentToCursor(): Path {
        val p = Path()
        p.moveTo(strokeX, strokeY)
        if (abs(curX - strokeX) > 0.5f || abs(curY - strokeY) > 0.5f) p.lineTo(curX, curY)
        strokeX = curX
        strokeY = curY
        return p
    }

    private fun dispatchScroll() {
        val perNotch = 90f * resources.displayMetrics.density
        // wheel up (+) = content moves down = finger moves down
        val dy = (scrollY * perNotch).coerceIn(-0.6f * screenH, 0.6f * screenH)
        // wheel right (+) = content moves left = finger moves left
        val dx = (-scrollX * perNotch).coerceIn(-0.6f * screenW, 0.6f * screenW)
        scrollX = 0f
        scrollY = 0f
        if (abs(dx) < 1f && abs(dy) < 1f) return

        val x0 = (curX - dx / 2).coerceIn(4f, screenW - 4f)
        val x1 = (curX + dx / 2).coerceIn(4f, screenW - 4f)
        val y0 = (curY - dy / 2).coerceIn(4f, screenH - 4f)
        val y1 = (curY + dy / 2).coerceIn(4f, screenH - 4f)
        val path = Path()
        path.moveTo(x0, y0)
        path.lineTo(x1, y1)
        val dist = hypot(x1 - x0, y1 - y0)
        val duration = (dist * 1.2f).toLong().coerceIn(120L, 400L)   // slower swipe = less fling
        dispatch(StrokeDescription(path, 0, duration, false), false)
    }

    private fun dispatch(stroke: StrokeDescription, hold: Boolean) {
        inFlight = true
        val gesture = GestureDescription.Builder().addStroke(stroke).build()
        val started = dispatchGesture(gesture, object : AccessibilityService.GestureResultCallback() {
            override fun onCompleted(gestureDescription: GestureDescription?) {
                inFlight = false
                activeStroke = if (hold) stroke else null
                pump()
            }

            override fun onCancelled(gestureDescription: GestureDescription?) {
                inFlight = false
                activeStroke = null
                suppressed = true
                pump()
            }
        }, main)
        if (!started) {
            inFlight = false
            activeStroke = null
            suppressed = true
        }
    }

    private fun Socket.closeQuietly() {
        try { close() } catch (e: IOException) {}
    }
}

/** The on-screen mouse pointer. Its top-left corner is the hotspot. */
class CursorView(context: Context) : View(context) {
    private val fillPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = Color.WHITE
        style = Paint.Style.FILL
    }
    private val strokePaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = Color.BLACK
        style = Paint.Style.STROKE
        strokeJoin = Paint.Join.ROUND
    }
    private val arrow = Path()

    override fun onDraw(canvas: Canvas) {
        val w = width.toFloat()
        val h = height.toFloat()
        val pad = w * 0.07f
        strokePaint.strokeWidth = pad
        val s = minOf(w, h) - pad * 2
        arrow.reset()
        arrow.moveTo(pad, pad)
        arrow.lineTo(pad, pad + s * 0.86f)
        arrow.lineTo(pad + s * 0.22f, pad + s * 0.66f)
        arrow.lineTo(pad + s * 0.40f, pad + s * 0.98f)
        arrow.lineTo(pad + s * 0.52f, pad + s * 0.92f)
        arrow.lineTo(pad + s * 0.35f, pad + s * 0.62f)
        arrow.lineTo(pad + s * 0.64f, pad + s * 0.62f)
        arrow.close()
        canvas.drawPath(arrow, fillPaint)
        canvas.drawPath(arrow, strokePaint)
    }
}
