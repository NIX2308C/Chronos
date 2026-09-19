package com.chronos.tutor.data

/** A conversation. Mirrors the local chat object in web/student.html:493-496. */
data class Chat(
    val id: String,
    val title: String,
    val classId: String,
    val messages: List<Message> = emptyList(),
    val loaded: Boolean = false,
    /**
     * True until the server has answered once. The temp id is sent as `chat_id`
     * and BECOMES the Firestore doc id (app.py:1852), so it must not be
     * stripped — attachments and the returned chat_id both depend on it.
     */
    val isNew: Boolean = false,
    val draft: String = "",
)

data class Message(
    val role: Role,
    val content: String,
    val blocked: Boolean = false,
    val reviewed: Boolean = false,
    /**
     * Derived, never a wire field: material_gap && !reviewed && !blocked.
     * The web computes this in two places that must agree
     * (student.html:557 and :1142); here it is computed once, in [gapFrom].
     */
    val gap: Boolean = false,
    val sources: List<String> = emptyList(),
    /** Set while a reply is still streaming in. */
    val streaming: Boolean = false,
    /** Appended when a stream dies mid-answer; the partial text is kept. */
    val errorNote: String? = null,
) {
    enum class Role { STUDENT, TUTOR }
}

/**
 * The one place the three annotation states are decided. Order is load-bearing:
 * blocked beats everything, and reviewed beats gap — see app.py:1917-1919 where
 * both reviewed and material_gap can be true at once.
 */
fun gapFrom(materialGap: Boolean, reviewed: Boolean, blocked: Boolean): Boolean =
    materialGap && !reviewed && !blocked

/** What the tutor asks the app to build. Validated again server-side. */
data class ToolRequest(val type: String, val topic: String)

/** A course the user belongs to. */
data class CourseClass(
    val id: String,
    val name: String,
    val toolkits: List<String> = emptyList(),
    val joinCode: String? = null,
)

/** Server reachability, shown as the header dot. */
enum class ServerStatus { ONLINE, WAKING, OFFLINE }
