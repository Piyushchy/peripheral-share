package com.piyush.mouseshare

import android.animation.ValueAnimator
import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.RectF
import android.graphics.Typeface
import android.graphics.drawable.Drawable
import android.text.TextPaint
import android.text.TextUtils
import android.view.View
import android.view.animation.DecelerateInterpolator
import kotlin.math.min

/**
 * The Alt+Tab strip: a dark rounded panel with one tile per recent app and a highlight that
 * slides from tile to tile when Tab is pressed. Drawn by hand (no layout files) and shown as
 * a non-touchable accessibility overlay by [AppSwitcher].
 */
class SwitcherView(
    context: Context,
    private val labels: List<String>,
    private val icons: List<Drawable?>,
    startSel: Int,
    screenWidthPx: Int
) : View(context) {

    private val density = context.resources.displayMetrics.density
    private fun dp(v: Float) = v * density

    private val n = labels.size
    private val pad = dp(16f)
    private val gap = dp(8f)
    private val cardW = dp(68f)
    private val cardH = dp(96f)
    private val titleH = dp(32f)
    private val iconSize = dp(48f)
    private val viewportW: Float          // width of the strip that is visible at once
    private val contentW: Float           // width of all tiles side by side
    private val panelW: Float
    private val panelH = pad + titleH + cardH + pad

    private var hl = startSel.toFloat()          // animated highlight position (in tiles)
    private var target = startSel
    private var anim: ValueAnimator? = null

    private val bg = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = Color.argb(238, 32, 33, 36) }
    private val hlFill = Paint(Paint.ANTI_ALIAS_FLAG).apply { color = Color.argb(64, 255, 255, 255) }
    private val hlStroke = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        color = Color.argb(230, 138, 180, 248)
        style = Paint.Style.STROKE
        strokeWidth = dp(2.5f)
    }
    private val titlePaint = TextPaint(Paint.ANTI_ALIAS_FLAG).apply {
        color = Color.WHITE
        textSize = dp(16f)
        typeface = Typeface.DEFAULT_BOLD
        textAlign = Paint.Align.CENTER
    }
    private val labelPaint = TextPaint(Paint.ANTI_ALIAS_FLAG).apply {
        color = Color.argb(220, 232, 234, 237)
        textSize = dp(11f)
        textAlign = Paint.Align.CENTER
    }
    private val rect = RectF()

    init {
        // as many tiles as fit across the screen at a readable size; the rest scroll into view
        val maxViewport = screenWidthPx - dp(24f) - 2 * pad
        val fits = ((maxViewport + gap) / (cardW + gap)).toInt().coerceAtLeast(1)
        val visible = min(n, fits)
        viewportW = visible * cardW + (visible - 1) * gap
        contentW = n * cardW + (n - 1) * gap
        panelW = viewportW + 2 * pad
    }

    override fun onMeasure(widthMeasureSpec: Int, heightMeasureSpec: Int) {
        setMeasuredDimension(panelW.toInt(), panelH.toInt())
    }

    /** Slide the highlight to tile [i]. */
    fun select(i: Int) {
        target = i.coerceIn(0, n - 1)
        anim?.cancel()
        anim = ValueAnimator.ofFloat(hl, target.toFloat()).apply {
            duration = 190
            interpolator = DecelerateInterpolator()
            addUpdateListener {
                hl = it.animatedValue as Float
                invalidate()
            }
            start()
        }
        invalidate()
    }

    override fun onDetachedFromWindow() {
        anim?.cancel()
        super.onDetachedFromWindow()
    }

    private fun fit(text: String, paint: TextPaint, width: Float): String =
        TextUtils.ellipsize(text, paint, width, TextUtils.TruncateAt.END).toString()

    override fun onDraw(canvas: Canvas) {
        rect.set(0f, 0f, panelW, panelH)
        canvas.drawRoundRect(rect, dp(22f), dp(22f), bg)

        // name of the highlighted app, like the title line in the Windows switcher
        val titleBase = pad + dp(20f)
        canvas.drawText(fit(labels.getOrElse(target) { "" }, titlePaint, panelW - 2 * pad),
            panelW / 2f, titleBase, titlePaint)

        val top = pad + titleH

        // scroll the strip so the highlight stays centred once there are more tiles than fit
        val maxOffset = (contentW - viewportW).coerceAtLeast(0f)
        val offset = (hl * (cardW + gap) - (viewportW - cardW) / 2f).coerceIn(0f, maxOffset)

        canvas.save()
        val m = dp(6f)                                           // keep the highlight outline unclipped
        canvas.clipRect(pad - m, top - m, pad + viewportW + m, top + cardH + m)
        canvas.translate(pad - offset, 0f)

        // sliding highlight
        val hx = hl * (cardW + gap)
        rect.set(hx, top, hx + cardW, top + cardH)
        canvas.drawRoundRect(rect, dp(14f), dp(14f), hlFill)
        canvas.drawRoundRect(rect, dp(14f), dp(14f), hlStroke)

        for (i in 0 until n) {
            val left = i * (cardW + gap)
            val cx = left + cardW / 2f
            val iconTop = top + dp(10f)
            icons[i]?.let {
                it.setBounds(
                    (cx - iconSize / 2f).toInt(), iconTop.toInt(),
                    (cx + iconSize / 2f).toInt(), (iconTop + iconSize).toInt()
                )
                it.draw(canvas)
            }
            canvas.drawText(fit(labels[i], labelPaint, cardW - dp(8f)), cx,
                iconTop + iconSize + dp(20f), labelPaint)
        }
        canvas.restore()
    }
}
