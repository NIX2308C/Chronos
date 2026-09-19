package com.chronos.tutor.net

import com.google.firebase.auth.FirebaseAuth
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.tasks.await
import okhttp3.Interceptor
import okhttp3.Request
import okhttp3.Response

/**
 * Attaches the Firebase ID token to every backend call, exactly as apiFetch()
 * does on the web (web/auth.js:91-96).
 *
 * runBlocking here is correct rather than a smell: OkHttp interceptors already
 * run on a background thread, and getIdToken(false) serves a cached token
 * without a network call for ~55 of its 60 minutes.
 *
 * On a 401 the token is refreshed once and the call replayed — that covers the
 * narrow window where a token expires mid-session. A second 401 means the
 * session is genuinely gone, and [onSignedOut] lets the root ViewModel bounce
 * the user to the login screen instead of showing a broken screen.
 */
class AuthInterceptor(
    private val auth: () -> FirebaseAuth = { FirebaseAuth.getInstance() },
    private val onSignedOut: () -> Unit = {},
) : Interceptor {

    override fun intercept(chain: Interceptor.Chain): Response {
        val first = chain.proceed(chain.request().withToken(token(forceRefresh = false)))
        if (first.code != 401) return first

        val fresh = token(forceRefresh = true)
        if (fresh == null) {
            onSignedOut()
            return first
        }
        // The body of the discarded response must be closed or the connection leaks.
        first.close()

        val second = chain.proceed(chain.request().withToken(fresh))
        if (second.code == 401) onSignedOut()
        return second
    }

    private fun token(forceRefresh: Boolean): String? = runBlocking {
        runCatching {
            auth().currentUser?.getIdToken(forceRefresh)?.await()?.token
        }.getOrNull()
    }

    private fun Request.withToken(token: String?): Request =
        if (token == null) this
        else newBuilder().header("Authorization", "Bearer $token").build()
}
