package com.chronos.tutor.data

import com.chronos.tutor.net.Api
import com.chronos.tutor.net.ApiError
import com.chronos.tutor.net.friendlyAuthError
import com.chronos.tutor.net.isEmailAlreadyInUse
import com.chronos.tutor.net.stringOrNull
import com.google.firebase.auth.FirebaseAuth
import com.google.firebase.auth.FirebaseAuthException
import com.google.firebase.auth.FirebaseUser
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.tasks.await
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject

/** [isDev] is decided by the server (DEV_EMAILS); it only unlocks developer tooling. */
data class Me(val uid: String, val email: String?, val role: String, val isDev: Boolean = false)

/** What the app knows about the current user. Drives the nav start destination. */
sealed interface AuthState {
    data object Loading : AuthState
    data object SignedOut : AuthState
    /** Signed into Firebase but no role on the backend — recoverable, not a dead end. */
    data object NeedsRole : AuthState
    data class Ready(val me: Me) : AuthState
    /**
     * Firebase could not be configured, so authentication is impossible in this
     * build. The app still starts and still shows its real UI — this state only
     * disables the sign-in form and explains why.
     */
    data class Unconfigured(val message: String) : AuthState
}

/**
 * Identity. Ported from web/auth.js, whose signup-recovery logic is the most
 * safety-critical code in the project: an account left between "Firebase
 * account created" and "role assigned" cannot be signed into and cannot be
 * recreated, so that email is permanently unusable unless the resume path
 * works. All three branches below are load-bearing — see the comments.
 */
class AuthRepository(
    private val api: Api,
    private val auth: FirebaseAuth,
    /** Persists the last identity so launch need not wait on /auth/me. Null in tests. */
    private val prefs: Prefs? = null,
) {
    // In-memory only. The web caches the role in sessionStorage with a 60s TTL
    // because a page reload wipes JS state; an Android process doesn't reload
    // per screen, so process lifetime is both simpler and strictly fresher.
    private var cachedRole: String? = null
    private var cachedUid: String? = null

    val currentUser: FirebaseUser? get() = auth.currentUser

    suspend fun signIn(email: String, password: String): Me = withContext(Dispatchers.IO) {
        try {
            auth.signInWithEmailAndPassword(email, password).await()
        } catch (e: Exception) {
            throw IllegalStateException(e.friendly())
        }
        clearRole()
        me() ?: throw IllegalStateException("Signed in, but no account details came back.")
    }

    /**
     * Create the Firebase account, then record the role on the backend.
     * email-already-in-use routes to [resumeSignup] rather than failing, which
     * is what makes a half-created account recoverable.
     */
    suspend fun signUp(email: String, password: String, role: String, teacherCode: String?): Me =
        withContext(Dispatchers.IO) {
            val cred = try {
                auth.createUserWithEmailAndPassword(email, password).await()
            } catch (e: Exception) {
                // Android reports ERROR_EMAIL_ALREADY_IN_USE where the web SDK
                // reports auth/email-already-in-use; isEmailAlreadyInUse covers both.
                if (isEmailAlreadyInUse((e as? FirebaseAuthException)?.errorCode, e.message)) {
                    return@withContext resumeSignup(email, password, role, teacherCode, e)
                }
                throw IllegalStateException(e.friendly())
            }
            register(role, teacherCode, rollback = true, user = cred.user)
        }

    /**
     * Finish a signup that died between "Firebase account created" and "role
     * assigned". Signing in first means this can only ever repair an account
     * whose password the caller already knows.
     */
    private suspend fun resumeSignup(
        email: String, password: String, role: String, teacherCode: String?, takenErr: Exception,
    ): Me {
        val cred = try {
            auth.signInWithEmailAndPassword(email, password).await()
        } catch (_: Exception) {
            throw IllegalStateException(takenErr.friendly())   // not ours to finish
        }

        // /auth/me answers only for accounts that have a role, so a failure
        // here is the "unfinished" signal we're looking for.
        val complete = runCatching { fetchMe() }.getOrNull() != null
        if (complete) {
            runCatching { auth.signOut() }
            throw IllegalStateException(takenErr.friendly())   // a real, finished account
        }
        return register(role, teacherCode, rollback = false, user = cred.user)
    }

    /**
     * Record the role on the backend for an account that already exists.
     *
     * [rollback] says whether we created that account moments ago and may
     * therefore delete it again. For a resumed signup the account predates this
     * call, and deleting it over a wrong teacher code would destroy an account
     * that may already be someone's.
     */
    private suspend fun register(
        role: String, teacherCode: String?, rollback: Boolean, user: FirebaseUser?,
    ): Me {
        try {
            val body = buildJsonObject {
                put("role", JsonPrimitive(role))
                if (!teacherCode.isNullOrBlank()) put("teacher_code", JsonPrimitive(teacherCode))
            }
            val data = api.post("/auth/register", body)
            val me = data.toMe() ?: throw ApiError.Malformed("/auth/register returned no role")
            remember(me)
            return me
        } catch (e: Exception) {
            if (rollback) runCatching { user?.delete()?.await() }
            runCatching { auth.signOut() }
            throw e
        }
    }

    /**
     * Assign a role to an account that is signed into Firebase but has none —
     * the state /auth/me reports as 403 "Finish creating your account first."
     *
     * rollback is false: this account predates the call, so a wrong teacher
     * code must never delete it.
     */
    suspend fun finishSignup(role: String, teacherCode: String?): Me = withContext(Dispatchers.IO) {
        register(role, teacherCode, rollback = false, user = auth.currentUser)
    }

    /** Current identity + role, or null when signed out. */
    suspend fun me(): Me? = withContext(Dispatchers.IO) {
        val user = auth.currentUser ?: return@withContext null
        cachedRole?.takeIf { cachedUid == user.uid }?.let {
            return@withContext Me(user.uid, user.email, it, cachedDev)
        }
        fetchMe()
    }

    private suspend fun fetchMe(): Me? {
        val user = auth.currentUser ?: return null
        val me = api.get("/auth/me").toMe() ?: return null
        remember(me)
        return me
    }

    /**
     * The identity saved by the last successful /auth/me, if it belongs to the
     * Firebase user still signed in. Lets launch skip the network; the caller
     * must still [resolve] to confirm it.
     */
    suspend fun cached(): Me? = withContext(Dispatchers.IO) {
        val user = auth.currentUser ?: return@withContext null
        prefs?.lastMe()?.takeIf { it.uid == user.uid }
    }

    /** Resolves what the nav graph should open on. */
    suspend fun resolve(): AuthState = withContext(Dispatchers.IO) {
        if (auth.currentUser == null) return@withContext signedOut()
        try {
            me()?.let { AuthState.Ready(it) } ?: signedOut()
        } catch (e: ApiError.Forbidden) {
            // "Finish creating your account first." — roleless, and recoverable.
            if (e.needsRole) { prefs?.setLastMe(null); AuthState.NeedsRole } else throw e
        } catch (e: ApiError.Unauthorized) {
            signedOut()
        }
    }

    private suspend fun signedOut(): AuthState {
        prefs?.setLastMe(null)
        return AuthState.SignedOut
    }

    suspend fun signOut() = withContext(Dispatchers.IO) {
        clearRole()
        auth.signOut()
    }

    private suspend fun remember(me: Me) {
        cachedUid = me.uid; cachedRole = me.role; cachedDev = me.isDev
        prefs?.setLastMe(me)
    }

    private suspend fun clearRole() {
        cachedUid = null; cachedRole = null; cachedDev = false
        prefs?.setLastMe(null)
    }

    private var cachedDev = false

    private fun kotlinx.serialization.json.JsonObject.toMe(): Me? {
        val uid = this["uid"]?.stringOrNull() ?: return null
        val role = this["role"]?.stringOrNull() ?: return null
        return Me(uid, this["email"]?.stringOrNull(), role, this["is_dev"]?.stringOrNull() == "true")
    }

    private fun Exception.friendly(): String =
        friendlyAuthError((this as? FirebaseAuthException)?.errorCode, message)
}
