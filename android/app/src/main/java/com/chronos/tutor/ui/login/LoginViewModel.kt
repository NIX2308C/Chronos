package com.chronos.tutor.ui.login

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.chronos.tutor.data.AuthRepository
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

data class LoginUiState(
    val signUp: Boolean = false,
    val email: String = "",
    val password: String = "",
    val showPassword: Boolean = false,
    val role: String = "student",
    val teacherCode: String = "",
    val busy: Boolean = false,
    val error: String? = null,
    /** Bumped on each failure so the card can re-run its shake animation. */
    val errorNonce: Int = 0,
) {
    val teacherCodeVisible: Boolean get() = signUp && role == "teacher"
    val canSubmit: Boolean get() = !busy && email.isNotBlank() && password.isNotBlank()
}

class LoginViewModel(private val auth: AuthRepository) : ViewModel() {

    private val _state = MutableStateFlow(LoginUiState())
    val state: StateFlow<LoginUiState> = _state.asStateFlow()

    fun setMode(signUp: Boolean) = _state.update { it.copy(signUp = signUp, error = null) }
    fun setEmail(v: String) = _state.update { it.copy(email = v) }
    fun setPassword(v: String) = _state.update { it.copy(password = v) }
    fun toggleShowPassword() = _state.update { it.copy(showPassword = !it.showPassword) }
    fun setRole(v: String) = _state.update { it.copy(role = v) }
    fun setTeacherCode(v: String) = _state.update { it.copy(teacherCode = v) }

    /**
     * The `busy` guard is load-bearing, not cosmetic: creating an account signs
     * the user in immediately, which would otherwise race the navigation away
     * from this screen. The web carries the same guard (web/login.html:239).
     */
    fun submit(onSuccess: () -> Unit) {
        val s = _state.value
        if (!s.canSubmit) return
        _state.update { it.copy(busy = true, error = null) }

        viewModelScope.launch {
            val result = runCatching {
                if (s.signUp) {
                    auth.signUp(
                        email = s.email.trim(),
                        password = s.password,
                        role = s.role,
                        teacherCode = s.teacherCode.takeIf { s.role == "teacher" },
                    )
                } else {
                    auth.signIn(s.email.trim(), s.password)
                }
            }
            result.fold(
                onSuccess = {
                    _state.update { it.copy(busy = false) }
                    onSuccess()
                },
                onFailure = { e ->
                    _state.update {
                        it.copy(
                            busy = false,
                            error = e.message ?: "Something went wrong.",
                            errorNonce = it.errorNonce + 1,
                        )
                    }
                },
            )
        }
    }
}
