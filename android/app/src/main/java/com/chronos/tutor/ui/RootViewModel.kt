package com.chronos.tutor.ui

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.chronos.tutor.data.AuthRepository
import com.chronos.tutor.data.AuthState
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch

/**
 * Decides what the app opens on, and reacts to the session dying underneath it.
 *
 * The splash is held (see MainActivity) until this leaves [AuthState.Loading],
 * so the user never sees a login screen flash before being sent to the tutor.
 */
class RootViewModel(
    private val auth: AuthRepository,
    signedOutEvents: SharedFlow<Unit>,
) : ViewModel() {

    private val _state = MutableStateFlow<AuthState>(AuthState.Loading)
    val state: StateFlow<AuthState> = _state.asStateFlow()

    init {
        refresh()
        viewModelScope.launch {
            signedOutEvents.collect { _state.value = AuthState.SignedOut }
        }
    }

    fun refresh() {
        viewModelScope.launch {
            _state.value = runCatching { auth.resolve() }
                .getOrElse { AuthState.SignedOut }
        }
    }

    fun onAuthenticated() = refresh()

    fun signOut() {
        viewModelScope.launch {
            auth.signOut()
            _state.value = AuthState.SignedOut
        }
    }
}
