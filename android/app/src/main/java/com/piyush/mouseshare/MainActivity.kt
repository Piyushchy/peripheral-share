package com.piyush.mouseshare

import android.app.Activity
import android.content.Intent
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.provider.Settings
import android.text.InputType
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.SeekBar
import android.widget.TextView
import android.widget.Toast
import java.net.Inet4Address
import java.net.NetworkInterface
import java.util.Collections

/** Tiny setup screen. The real work happens in [MouseService]. */
class MainActivity : Activity() {

    private lateinit var statusView: TextView
    private lateinit var addressView: TextView
    private lateinit var pinEdit: EditText
    private lateinit var sizeLabel: TextView
    private lateinit var debugView: TextView

    private val ui = Handler(Looper.getMainLooper())
    private val tick = object : Runnable {
        override fun run() {
            refresh()
            ui.postDelayed(this, 1000)
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val density = resources.displayMetrics.density
        val pad = (16 * density).toInt()

        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(pad, pad, pad, pad)
        }
        val scroll = ScrollView(this)
        scroll.addView(root)
        setContentView(scroll)

        fun label(text: String, size: Float = 15f): TextView {
            val tv = TextView(this)
            tv.text = text
            tv.textSize = size
            tv.setPadding(0, pad / 2, 0, pad / 4)
            return tv
        }

        root.addView(label("MouseShare - phone side", 22f))

        statusView = label("", 16f)
        root.addView(statusView)

        debugView = label("", 13f)
        root.addView(debugView)

        root.addView(label("Step 1: turn the service on"))
        val accBtn = Button(this)
        accBtn.text = "Open Accessibility settings"
        accBtn.setOnClickListener { startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS)) }
        root.addView(accBtn)
        root.addView(label("Find \"MouseShare\" in the list (it may be under Installed apps) and switch it on. " +
            "This permission is what lets the app draw a cursor and perform taps, drags and scrolls."))

        root.addView(label("Step 2: type this into the Windows app"))
        addressView = label("", 20f)
        root.addView(addressView)

        root.addView(label("PIN (must match the PC)"))
        pinEdit = EditText(this)
        pinEdit.inputType = InputType.TYPE_CLASS_NUMBER
        pinEdit.setText(Prefs.pin(this))
        root.addView(pinEdit)
        val pinBtn = Button(this)
        pinBtn.text = "Save PIN"
        pinBtn.setOnClickListener {
            val v = pinEdit.text.toString().trim()
            if (v.length < 4) {
                Toast.makeText(this, "Use at least 4 digits", Toast.LENGTH_SHORT).show()
            } else {
                Prefs.setPin(this, v)
                Toast.makeText(this, "PIN saved", Toast.LENGTH_SHORT).show()
            }
        }
        root.addView(pinBtn)

        sizeLabel = label("")
        root.addView(sizeLabel)
        val seek = SeekBar(this)
        seek.max = 40                                   // 12 .. 52 dp
        seek.progress = Prefs.cursorDp(this) - 12
        sizeLabel.text = "Cursor size: ${Prefs.cursorDp(this)} dp"
        seek.setOnSeekBarChangeListener(object : SeekBar.OnSeekBarChangeListener {
            override fun onProgressChanged(bar: SeekBar?, progress: Int, fromUser: Boolean) {
                val dp = progress + 12
                Prefs.setCursorDp(this@MainActivity, dp)
                sizeLabel.text = "Cursor size: $dp dp (applies next time the mouse enters the phone)"
            }

            override fun onStartTrackingTouch(bar: SeekBar?) {}
            override fun onStopTrackingTouch(bar: SeekBar?) {}
        })
        root.addView(seek)

        root.addView(label(
            "While the mouse is on the phone:\n" +
                "  Left click / drag = touch and swipe\n" +
                "  Wheel = scroll\n" +
                "  Right click or side button 1 = Back\n" +
                "  Wheel click = Home\n" +
                "  Side button 2 = Recent apps\n" +
                "  Typing goes into the focused text box as real key presses\n" +
                "  (Ctrl+Backspace, Ctrl+arrows, Shift+arrows, Ctrl+Z/Y, Ctrl+A/C/V/X...)\n" +
                "  Win = Home, Win+Tab = Recent apps, Esc = Back\n" +
                "  Alt+Tab = the phone's own Alt+Tab once the real-key helper is on\n" +
                "  (PC app: Start real-key helper); until then MouseShare shows its own switcher\n" +
                "  (system keys can be turned off on the PC)"
        ))
        root.addView(label(
            "Connecting:\n" +
                "  WiFi - same network as the PC; the PC's Scan button finds this phone automatically.\n" +
                "  USB - turn on Developer options > USB debugging, plug the cable in, then pick the " +
                "USB device in the PC app. No WiFi needed."
        ))
    }

    override fun onResume() {
        super.onResume()
        ui.post(tick)
    }

    override fun onPause() {
        super.onPause()
        ui.removeCallbacks(tick)
    }

    private fun refresh() {
        val svc = MouseService.instance
        statusView.text = if (svc == null) {
            "Service is OFF - enable MouseShare in Accessibility settings."
        } else {
            "Service is ON - ${MouseService.status}"
        }
        debugView.text = if (svc == null) ""
        else "Last command: ${MouseService.lastCommand}\nLast keystroke: ${KeyInput.lastResult}\n" +
            "Keyboard: ${KeyInput.connectionState(svc)}\n" +
            "Real keys: ${MouseService.rawStatus.removePrefix("real keys: ")}\n" +
            "Mouse: ${svc.mouseMode()}\n" +
            "Alt+Tab apps (fallback list): ${svc.switcherSummary()}"
        val ip = localIp()
        addressView.text = if (ip == null) "No WiFi address found" else "$ip   port ${MouseService.PORT}"
    }

    private fun localIp(): String? {
        try {
            val all = Collections.list(NetworkInterface.getNetworkInterfaces())
                .sortedBy { if (it.name.startsWith("wlan") || it.name.startsWith("ap")) 0 else 1 }
            for (ni in all) {
                if (!ni.isUp || ni.isLoopback) continue
                for (a in Collections.list(ni.inetAddresses)) {
                    if (a is Inet4Address && !a.isLoopbackAddress) return a.hostAddress
                }
            }
        } catch (e: Exception) {
        }
        return null
    }
}
