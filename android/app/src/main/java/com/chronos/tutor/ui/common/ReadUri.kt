package com.chronos.tutor.ui.common

import android.content.ContentResolver
import android.net.Uri
import android.provider.OpenableColumns
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

/** Name, MIME type and bytes of a file the user picked with OpenDocument. */
internal suspend fun readUri(resolver: ContentResolver, uri: Uri): Triple<String, String?, ByteArray> =
    withContext(Dispatchers.IO) {
        val name = resolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME), null, null, null)?.use {
            if (it.moveToFirst()) it.getString(0) else null
        } ?: "document"
        val bytes = resolver.openInputStream(uri)?.use { it.readBytes() } ?: error("unreadable")
        Triple(name, resolver.getType(uri), bytes)
    }

/** The upload types app.py's extract_file_text accepts. */
val DOC_MIME_TYPES = arrayOf(
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "text/plain", "text/markdown", "text/csv",
)
