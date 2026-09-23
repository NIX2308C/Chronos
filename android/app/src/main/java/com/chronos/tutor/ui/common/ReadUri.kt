package com.chronos.tutor.ui.common

import android.content.ContentResolver
import android.net.Uri
import android.provider.OpenableColumns
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.io.ByteArrayOutputStream
import java.io.InputStream

/** app.py's MAX_UPLOAD_MB. Anything larger would only come back as a 413. */
const val MAX_UPLOAD_BYTES = 10L * 1024 * 1024

class FileTooLarge : Exception("That file is too large. The limit is ${MAX_UPLOAD_BYTES / (1024 * 1024)} MB.")

/**
 * Name, MIME type and bytes of a file the user picked with OpenDocument.
 *
 * Never reads more than [MAX_UPLOAD_BYTES]: the picker accepts any size, and
 * reading a large file whole would crash the app with an out-of-memory error.
 *
 * @throws FileTooLarge when the file is over the limit.
 */
internal suspend fun readUri(resolver: ContentResolver, uri: Uri): Triple<String, String?, ByteArray> =
    withContext(Dispatchers.IO) {
        var name: String? = null
        var size: Long? = null
        resolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME, OpenableColumns.SIZE), null, null, null)?.use {
            if (it.moveToFirst()) {
                name = it.getString(0)
                size = if (it.isNull(1)) null else it.getLong(1)
            }
        }
        // The reported size can be missing or wrong, so the read is capped as well.
        if ((size ?: 0L) > MAX_UPLOAD_BYTES) throw FileTooLarge()
        val bytes = resolver.openInputStream(uri)?.use { readCapped(it, MAX_UPLOAD_BYTES) } ?: error("unreadable")
        Triple(name ?: "document", resolver.getType(uri), bytes)
    }

/** Reads [input] to the end, or throws [FileTooLarge] once it passes [max] bytes. */
internal fun readCapped(input: InputStream, max: Long): ByteArray {
    val out = ByteArrayOutputStream()
    val buf = ByteArray(64 * 1024)
    var total = 0L
    while (true) {
        val n = input.read(buf)
        if (n < 0) break
        total += n
        if (total > max) throw FileTooLarge()
        out.write(buf, 0, n)
    }
    return out.toByteArray()
}

/** What to show when [readUri] fails. */
internal fun Throwable.readFailureText(): String =
    (this as? FileTooLarge)?.message ?: "Couldn't read that file."

/** The upload types app.py's extract_file_text accepts. */
val DOC_MIME_TYPES = arrayOf(
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "text/plain", "text/markdown", "text/csv",
)
