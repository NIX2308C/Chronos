package com.chronos.tutor.net

import java.io.IOException

/**
 * Every failure the backend can hand back, as a type the UI can switch on.
 *
 * The status mapping is ported from apiJson() in web/auth.js:100-115 so both
 * clients say the same thing for the same failure. The two cases the web
 * doesn't distinguish — 429 and 413 — get their own messages here, because
 * app.py rate-limits six routes and caps uploads at MAX_UPLOAD_MB, and
 * "Something went wrong" for a throttle is a visible defect.
 */
sealed class ApiError(val userMessage: String) : IOException(userMessage) {

    /** No network, DNS failure, connection refused. */
    class Offline(val cause_: Throwable? = null) :
        ApiError("Can't reach Chronos. Check your connection and try again.")

    /** 401 — token missing, expired or rejected. */
    class Unauthorized(val serverMessage: String? = null) :
        ApiError(serverMessage ?: "Please sign in again.")

    /**
     * 403. app.py returns this both for "not your class" and for the roleless
     * account case ("Finish creating your account first.") — the caller checks
     * [needsRole] to tell them apart, because the second one is recoverable.
     */
    class Forbidden(val serverMessage: String? = null) :
        ApiError(serverMessage ?: "You don't have access to this.") {
        val needsRole: Boolean
            get() = serverMessage?.contains("Finish creating your account", ignoreCase = true) == true
    }

    /** 429. [retryAfterSeconds] comes from the Retry-After header when present. */
    class RateLimited(val retryAfterSeconds: Int?, val serverMessage: String? = null) :
        ApiError(
            serverMessage ?: when (retryAfterSeconds) {
                null -> "You're going a bit fast. Give it a moment."
                else -> "You're going a bit fast. Try again in ${retryAfterSeconds}s."
            }
        )

    /** 413 — body over MAX_UPLOAD_MB. app.py turns this into JSON, not an HTML page. */
    class TooLarge(val serverMessage: String? = null) :
        ApiError(serverMessage ?: "That file is too large. The limit is 10 MB.")

    /** Any other non-2xx, or a 2xx body carrying an {"error": ...} key. */
    class Server(val status: Int, val serverMessage: String? = null) :
        ApiError(serverMessage ?: "Something went wrong. Please try again.")

    /** The response wasn't the JSON we expected. */
    class Malformed(val detail: String) :
        ApiError("Chronos sent something unexpected. Please try again.")
}

/**
 * Firebase Auth error codes to human copy. The messages are ported verbatim
 * from friendlyAuthError() in web/auth.js:117-127 so both clients say the same
 * thing for the same failure.
 *
 * The codes themselves are NOT the same, which is the trap: the web SDK reports
 * "auth/wrong-password" while the Android SDK reports "ERROR_WRONG_PASSWORD".
 * Normalising to lowercase-with-hyphens makes one set of substring checks cover
 * both, and keeps this readable next to the JS it came from.
 */
fun friendlyAuthError(code: String?, fallback: String?): String {
    val c = code?.lowercase()?.replace('_', '-') ?: return fallback ?: "Something went wrong."
    return when {
        c.contains("wrong-password") || c.contains("invalid-credential") -> "Wrong email or password."
        c.contains("user-not-found")       -> "No account found with that email."
        c.contains("email-already-in-use") -> "An account with that email already exists."
        c.contains("weak-password")        -> "Password must be at least 6 characters."
        c.contains("invalid-email")        -> "That doesn't look like a valid email."
        c.contains("too-many-requests")    -> "Too many attempts. Try again in a moment."
        else -> fallback ?: "Something went wrong."
    }
}

/** True when a sign-up failed only because the email is already taken. */
fun isEmailAlreadyInUse(code: String?, message: String?): Boolean {
    val c = code?.lowercase()?.replace('_', '-').orEmpty()
    return c.contains("email-already-in-use") ||
        message?.contains("already in use", ignoreCase = true) == true
}
