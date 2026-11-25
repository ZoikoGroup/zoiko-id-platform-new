package com.zoiko.chipbridge

// NOT BUILT (like the rest of this module): written from Google's documented Play Integrity Standard API, never compiled or run.

import android.content.Context
import android.util.Base64
import com.google.android.play.core.integrity.IntegrityManagerFactory
import com.google.android.play.core.integrity.StandardIntegrityManager.PrepareIntegrityTokenRequest
import com.google.android.play.core.integrity.StandardIntegrityManager.StandardIntegrityTokenProvider
import com.google.android.play.core.integrity.StandardIntegrityManager.StandardIntegrityTokenRequest
import java.security.MessageDigest

/**
 * The device report for one capture (ZID-CP-WF01 S05, Q18). The platform issues a one-time nonce; the capture's challenge is "<nonce>:<sha256 hex of the
 * media>", and Play is asked to bind requestHash = base64url(SHA-256(challenge)), exactly what the server recomputes. The server decodes the token with
 * Google and checks the package, the signing certificate, the device verdict and the freshness; this class only asks Play for the token.
 *
 * Send the result as the capture's `attestation`: {"platform": "ANDROID_PLAY_INTEGRITY", "report": {"integrity_token": <token>}}.
 */
class CaptureAttestation(context: Context, private val cloudProjectNumber: Long) {
    private val manager = IntegrityManagerFactory.createStandard(context.applicationContext)
    private var provider: StandardIntegrityTokenProvider? = null

    /** Call once early (it warms Play up); a capture then gets its token quickly. */
    fun prepare(onReady: () -> Unit, onError: (Exception) -> Unit) {
        manager.prepareIntegrityToken(PrepareIntegrityTokenRequest.builder().setCloudProjectNumber(cloudProjectNumber).build())
            .addOnSuccessListener { provider = it; onReady() }
            .addOnFailureListener(onError)
    }

    /** The token for this capture. Without one the server treats the capture's origin as not established, never as safe. */
    fun tokenFor(nonce: String, media: ByteArray, onToken: (String) -> Unit, onError: (Exception) -> Unit) {
        val p = provider ?: return onError(IllegalStateException("prepare() first"))
        p.request(StandardIntegrityTokenRequest.builder().setRequestHash(requestHash(nonce, media)).build())
            .addOnSuccessListener { onToken(it.token()) }
            .addOnFailureListener(onError)
    }

    companion object {
        fun requestHash(nonce: String, media: ByteArray): String {
            val sha = MessageDigest.getInstance("SHA-256")
            val mediaHex = sha.digest(media).joinToString("") { "%02x".format(it) }
            val challenge = "$nonce:$mediaHex".toByteArray(Charsets.UTF_8)
            return Base64.encodeToString(MessageDigest.getInstance("SHA-256").digest(challenge), Base64.URL_SAFE or Base64.NO_PADDING or Base64.NO_WRAP)
        }
    }
}
