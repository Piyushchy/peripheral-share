package com.piyush.mouseshare

import android.accessibilityservice.AccessibilityService
import android.accessibilityservice.InputMethod
import android.annotation.TargetApi
import android.view.inputmethod.EditorInfo

/**
 * Android 13+ lets an accessibility service that sets FLAG_INPUT_METHOD_EDITOR attach to
 * the focused text field the same way an on-screen keyboard does. Through that connection
 * we can send *real* KeyEvents (with Ctrl/Alt/Shift state), commit text and edit around
 * the caret - so Ctrl+Backspace, Ctrl+Z, Shift+arrows etc. are handled by the app itself,
 * exactly as if a hardware keyboard were attached.
 *
 * This class only remembers which editor is currently attached (for diagnostics and for
 * choosing how to insert text). The connection itself is read via
 * [AccessibilityService.getInputMethod] in [KeyInput].
 */
@TargetApi(33)
class KeyInputMethod(service: AccessibilityService) : InputMethod(service) {

    override fun onStartInput(attribute: EditorInfo, restarting: Boolean) {
        KeyInput.editor = attribute
    }

    override fun onFinishInput() {
        KeyInput.editor = null
    }
}
