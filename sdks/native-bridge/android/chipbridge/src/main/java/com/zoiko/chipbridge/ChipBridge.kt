package com.zoiko.chipbridge

import android.nfc.Tag
import android.nfc.tech.IsoDep
import android.util.Base64
import java.io.IOException

/**
 * The whole phone side of the platform's chip read: bytes in, bytes out, status bytes included (sdks/native-bridge/README.md).
 *
 * NOT COMPILED, NOT RUN: written from Android's documented IsoDep API. It has never touched a passport.
 *
 * Rules it keeps: it sends exactly the bytes it is given, returns exactly what the chip answers, never looks inside either, never keeps or logs one, and says only
 * "the chip could not be reached" when anything fails.
 */
class ChipBridge private constructor(private val iso: IsoDep) {

    /** Send one command APDU; the answer is the chip's response with SW1 SW2 at the end. */
    @Throws(IOException::class)
    fun transceive(apdu: ByteArray): ByteArray {
        if (apdu.size < 4 || apdu.size > MAX_COMMAND) throw IOException("command size")
        return iso.transceive(apdu)
    }

    fun close() {
        runCatching { iso.close() }
    }

    /** The shape a web view's JavaScript interface needs: base64 in, base64 out, or `ERROR:` and a fixed word. Nothing about the cause leaves the phone. */
    fun transceiveBase64(apduBase64: String): String = try {
        val command = Base64.decode(apduBase64, Base64.NO_WRAP)
        Base64.encodeToString(transceive(command), Base64.NO_WRAP)
    } catch (e: Exception) {
        "ERROR:device"
    }

    companion object {
        /** An extended-length command for a 2048-bit DH value is about 270 bytes; nothing the platform sends comes near this. */
        const val MAX_COMMAND = 4096

        /** Connect to a tag the reader found. Returns null when it is not an ISO 14443-4 tag (a passport always is). */
        fun open(tag: Tag, timeoutMillis: Int = 15_000): ChipBridge? {
            val iso = IsoDep.get(tag) ?: return null
            return try {
                iso.connect()
                iso.timeout = timeoutMillis // a chip can take seconds on its first secure-messaging command
                ChipBridge(iso)
            } catch (e: IOException) {
                null
            }
        }
    }
}
