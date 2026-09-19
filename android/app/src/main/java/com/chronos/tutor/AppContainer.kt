package com.chronos.tutor

import android.content.Context
import com.chronos.tutor.data.AuthRepository
import com.chronos.tutor.data.Prefs
import com.chronos.tutor.net.Api
import com.chronos.tutor.net.AuthInterceptor
import com.google.firebase.auth.FirebaseAuth
import kotlinx.coroutines.flow.MutableSharedFlow
import okhttp3.OkHttpClient
import java.util.concurrent.TimeUnit

/**
 * Dependency wiring, by hand.
 *
 * Five singletons do not justify a DI framework: Hilt would mean a KSP
 * processor, an annotated Application, and an annotation on every ViewModel, to
 * replace the twenty lines below.
 */
class AppContainer(context: Context, firebaseReady: Boolean) {

    /** Emits when the session is gone for good, so the root can bounce to login. */
    val signedOutEvents = MutableSharedFlow<Unit>(extraBufferCapacity = 1)

    val prefs = Prefs(context.applicationContext)

    // Null when Firebase could not be configured. Everything auth-shaped is
    // nullable from here down, so a misconfigured build still starts and shows
    // its real UI instead of dying in Application.onCreate.
    private val firebaseAuth: FirebaseAuth? =
        if (firebaseReady) runCatching { FirebaseAuth.getInstance() }.getOrNull() else null

    private val httpClient: OkHttpClient = OkHttpClient.Builder()
        // Defaults stay short on purpose. /chat and /tools/run raise their own
        // timeout per call (Api.SLOW_CALL_TIMEOUT_MS) — a client-wide 90s would
        // leave a dead /health poll hanging for a minute and a half.
        .connectTimeout(15, TimeUnit.SECONDS)
        .readTimeout(30, TimeUnit.SECONDS)
        .writeTimeout(30, TimeUnit.SECONDS)
        .addInterceptor(
            AuthInterceptor(
                auth = { firebaseAuth },   // no token attached when null
                onSignedOut = { signedOutEvents.tryEmit(Unit) },
            )
        )
        .build()

    val api = Api(client = httpClient)

    /** Null when Firebase is unconfigured; the UI shows an unconfigured state. */
    val authRepository: AuthRepository? =
        firebaseAuth?.let { AuthRepository(api, it) }
}
