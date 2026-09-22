package com.chronos.tutor.data

import com.chronos.tutor.net.stringOrNull
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.boolean
import kotlinx.serialization.json.int
import kotlinx.serialization.json.jsonPrimitive

/** The four activity shapes _parse_tool_result (app.py) guarantees. */
sealed interface LearningTool {
    val title: String

    data class Quiz(override val title: String, val questions: List<Question>, val revealAnswers: Boolean) : LearningTool
    data class Question(val prompt: String, val options: List<String>, val answer: Int, val explanation: String)

    data class Flashcards(override val title: String, val cards: List<Pair<String, String>>) : LearningTool

    data class ConceptMap(override val title: String, val nodes: List<Node>, val links: List<Link>) : LearningTool
    data class Node(val label: String, val detail: String)
    data class Link(val from: Int, val to: Int, val label: String)

    data class ReviewSheet(override val title: String, val sections: List<Pair<String, List<String>>>) : LearningTool
}

/** Label used in menus and status lines; TOOL_LABELS in web/student.html. */
fun toolLabel(type: String) = when (type) {
    "quiz" -> "Quiz"
    "flashcards" -> "Flashcards"
    "concept_map" -> "Concept map"
    "review_sheet" -> "Review sheet"
    else -> "Activity"
}

/** In-thread progress for a /tools/run call. */
data class ToolStatus(val state: State, val text: String) {
    enum class State { USING, DONE, FAILED, CANCELED }
}

internal fun parseTool(o: JsonObject?): LearningTool? {
    o ?: return null
    val title = o["title"]?.stringOrNull().orEmpty()
    fun arr(k: String) = (o[k] as? JsonArray)?.mapNotNull { it as? JsonObject } ?: emptyList()
    fun JsonObject.s(k: String) = this[k]?.stringOrNull().orEmpty()
    fun JsonObject.i(k: String) = this[k]?.let { runCatching { it.jsonPrimitive.int }.getOrNull() }
    return when (o["type"]?.stringOrNull()) {
        "quiz" -> arr("questions").mapNotNull { q ->
            val options = (q["options"] as? JsonArray)?.mapNotNull { it.stringOrNull() } ?: return@mapNotNull null
            val answer = q.i("answer") ?: return@mapNotNull null
            if (q.s("prompt").isEmpty() || options.size < 2 || answer !in options.indices) return@mapNotNull null
            LearningTool.Question(q.s("prompt"), options, answer, q.s("explanation"))
        }.takeIf { it.isNotEmpty() }?.let {
            LearningTool.Quiz(title.ifEmpty { "Practice quiz" }, it,
                o["reveal_final_answers"]?.let { r -> runCatching { r.jsonPrimitive.boolean }.getOrNull() } == true)
        }
        "flashcards" -> arr("cards").mapNotNull { c ->
            if (c.s("front").isEmpty() || c.s("back").isEmpty()) null else c.s("front") to c.s("back")
        }.takeIf { it.isNotEmpty() }?.let { LearningTool.Flashcards(title.ifEmpty { "Flashcards" }, it) }
        "concept_map" -> {
            val nodes = arr("nodes").mapNotNull { n -> n.s("label").takeIf { it.isNotEmpty() }?.let { LearningTool.Node(it, n.s("detail")) } }
            if (nodes.isEmpty()) null else LearningTool.ConceptMap(
                title.ifEmpty { "Concept map" }, nodes,
                arr("links").mapNotNull { l ->
                    val f = l.i("from") ?: return@mapNotNull null
                    val t = l.i("to") ?: return@mapNotNull null
                    if (f in nodes.indices && t in nodes.indices) LearningTool.Link(f, t, l.s("label")) else null
                },
            )
        }
        "review_sheet" -> arr("sections").mapNotNull { s ->
            s.s("heading").takeIf { it.isNotEmpty() }?.let { h ->
                h to ((s["points"] as? JsonArray)?.mapNotNull { it.stringOrNull() } ?: emptyList())
            }
        }.takeIf { it.isNotEmpty() }?.let { LearningTool.ReviewSheet(title.ifEmpty { "Review sheet" }, it) }
        else -> null
    }
}
