package com.piyush.mouseshare

import java.io.IOException
import java.io.OutputStream
import java.net.InetSocketAddress
import java.net.Socket
import java.util.concurrent.Executors
import kotlin.concurrent.thread

/**
 * MouseShare service -> [KeyServer] connection (localhost only). The service forwards the
 * PC's raw key events here; the helper injects them into Android as real key events.
 */
class HelperLink(private val port: Int = KeyServer.PORT) {

    @Volatile private var sock: Socket? = null
    @Volatile private var out: OutputStream? = null

    /** How the helper injects keys (from its greeting), e.g. "InputManagerGlobal". */
    @Volatile var info: String = ""
        private set

    /** Called (on a background thread) when an established connection to the helper is lost. */
    @Volatile var onLost: (() -> Unit)? = null

    val isUp: Boolean get() = sock != null

    /**
     * All writes happen on this one thread, in order. Android forbids socket writes on the main
     * thread (NetworkOnMainThreadException) and callers such as the cursor code run there.
     */
    private val writer = Executors.newSingleThreadExecutor { r ->
        Thread(r, "mouseshare-helper-writer").apply { isDaemon = true }
    }

    /**
     * Connect and authenticate, retrying for a few seconds because the PC starts the helper
     * just before asking us to connect. Returns null on success, otherwise a readable reason.
     * Blocking - call from a background thread.
     */
    fun connect(token: String, waitMs: Long = 6000): String? {
        close()
        val deadline = System.currentTimeMillis() + waitMs
        var last: String
        while (true) {
            try {
                val s = Socket()
                s.connect(InetSocketAddress("127.0.0.1", port), 1000)
                s.tcpNoDelay = true
                s.soTimeout = 4000
                val w = s.getOutputStream()
                w.write("AUTH $token\n".toByteArray())
                w.flush()
                val r = s.getInputStream().bufferedReader()
                val reply = r.readLine()
                if (reply == null) {
                    last = "helper closed the connection"
                    s.close()
                } else if (!reply.startsWith("OK")) {
                    s.close()
                    return reply.removePrefix("ERR").trim().ifEmpty { "helper refused the connection" }
                } else {
                    s.soTimeout = 0
                    sock = s
                    out = w
                    info = reply.removePrefix("OK").trim()
                    thread(name = "mouseshare-helper-watch", isDaemon = true) {
                        try { while (r.readLine() != null) { /* helper only talks at login */ } }
                        catch (e: IOException) {}
                        if (sock === s) {
                            close()
                            onLost?.invoke()
                        }
                    }
                    return null
                }
            } catch (e: IOException) {
                last = "helper not reachable (${e.message ?: "connection refused"}) - was it started from the PC?"
            }
            if (System.currentTimeMillis() >= deadline) return last
            try { Thread.sleep(400) } catch (e: InterruptedException) { return last }
        }
    }

    /**
     * Queue one protocol line (KD/KU/MM/MB/MW/RESET) for the helper. Safe to call from any
     * thread, never blocks. Returns false if the helper is not connected; if a queued write
     * later fails the link is closed and [onLost] is called.
     */
    fun send(line: String): Boolean {
        val o = out ?: return false
        val bytes = (line + "\n").toByteArray()
        try {
            writer.execute {
                try {
                    o.write(bytes)
                    o.flush()
                } catch (e: IOException) {
                    if (out === o) {
                        close()
                        onLost?.invoke()
                    }
                }
            }
        } catch (e: Exception) {
            return false
        }
        return true
    }

    fun close() {
        val s = sock
        sock = null
        out = null
        info = ""
        try { s?.close() } catch (e: IOException) {}
    }
}
