package com.chronos.tutor.ui.common

import android.content.Context
import android.media.MediaPlayer
import com.chronos.tutor.R

/** playSound() in web/student.html: one sound at a time, "using" quieter than the rest. */
class Sounds(private val context: Context) {
    enum class Kind(val res: Int, val volume: Float) {
        USING(R.raw.toolkit_using, .18f),
        DONE(R.raw.toolkit_done, .24f),
        FAILED(R.raw.toolkit_fail, .24f),
        CORRECT(R.raw.quiz_correct, .24f),
        INCORRECT(R.raw.quiz_incorrect, .24f),
        HIGH_SCORE(R.raw.high_score, .24f),
        LOW_SCORE(R.raw.low_score, .24f),
    }

    private var current: MediaPlayer? = null

    fun play(kind: Kind) {
        stop()
        current = runCatching {
            MediaPlayer.create(context, kind.res)?.apply {
                setVolume(kind.volume, kind.volume)
                setOnCompletionListener { it.release(); if (current === it) current = null }
                start()
            }
        }.getOrNull()
    }

    fun stop() {
        current?.let { runCatching { it.stop() }; it.release() }
        current = null
    }
}
