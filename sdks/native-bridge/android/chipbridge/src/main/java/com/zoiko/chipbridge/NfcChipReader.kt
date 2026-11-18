package com.zoiko.chipbridge

import android.app.Activity
import android.nfc.NfcAdapter
import android.nfc.Tag
import android.webkit.JavascriptInterface
import android.webkit.WebView

/**
 * Finds the chip and hands it to a web view as `window.ZoikoChip.transceive(base64)`.
 *
 * NOT COMPILED, NOT RUN. The web page (our console or the customer's own page) then calls the TypeScript `transceiveFromNative(window.ZoikoChip)` and
 * `readChipThroughRelay(...)`, which talk to the platform. This class never sees the document details, the document number or any chip data.
 */
class NfcChipReader(private val activity: Activity, private val webView: WebView) {

    @Volatile private var bridge: ChipBridge? = null

    /** What JavaScript can call. Calls arrive on a background thread, so blocking on the chip here does not block the page. */
    private inner class Host {
        @JavascriptInterface
        fun transceive(apduBase64: String): String = bridge?.transceiveBase64(apduBase64) ?: "ERROR:device"

        @JavascriptInterface
        fun isConnected(): Boolean = bridge != null
    }

    /** NFC is optional: a phone without it must offer another route, not fail to install (see the manifest). */
    fun isAvailable(): Boolean = NfcAdapter.getDefaultAdapter(activity)?.isEnabled == true

    fun start(onChip: () -> Unit, onLost: () -> Unit) {
        webView.addJavascriptInterface(Host(), "ZoikoChip")
        NfcAdapter.getDefaultAdapter(activity)?.enableReaderMode(
            activity,
            { tag: Tag ->
                bridge?.close()
                bridge = ChipBridge.open(tag)
                if (bridge != null) activity.runOnUiThread(onChip) else activity.runOnUiThread(onLost)
            },
            // reader mode keeps the system from handing the passport to another app; a passport has no NDEF message, so skip that check
            NfcAdapter.FLAG_READER_NFC_A or NfcAdapter.FLAG_READER_NFC_B or NfcAdapter.FLAG_READER_SKIP_NDEF_CHECK,
            null,
        )
    }

    fun stop() {
        NfcAdapter.getDefaultAdapter(activity)?.disableReaderMode(activity)
        bridge?.close()
        bridge = null
        webView.removeJavascriptInterface("ZoikoChip")
    }
}
