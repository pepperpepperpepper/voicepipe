package dev.voicepipe.zwangli

import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.speech.RecognitionListener
import android.speech.RecognizerIntent
import android.speech.SpeechRecognizer

class SpeechRecognitionController(
    private val context: Context,
    private val callbacks: Callbacks,
) {
    interface Callbacks {
        fun onListeningStart()
        fun onPartial(text: String)
        fun onFinal(text: String)
        fun onError(message: String, recoverable: Boolean)
        fun onListeningStop()
    }

    private var recognizer: SpeechRecognizer? = null
    private var listening: Boolean = false

    val isListening: Boolean get() = listening

    fun start() {
        if (listening) return
        if (!SpeechRecognizer.isRecognitionAvailable(context)) {
            callbacks.onError("speech recognition not available on this device", false)
            return
        }
        val sr = recognizer ?: SpeechRecognizer.createSpeechRecognizer(context).also {
            it.setRecognitionListener(listener)
            recognizer = it
        }
        val intent = Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH).apply {
            putExtra(
                RecognizerIntent.EXTRA_LANGUAGE_MODEL,
                RecognizerIntent.LANGUAGE_MODEL_FREE_FORM,
            )
            putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, true)
            putExtra(RecognizerIntent.EXTRA_CALLING_PACKAGE, context.packageName)
        }
        listening = true
        callbacks.onListeningStart()
        sr.startListening(intent)
    }

    fun stop() {
        if (!listening) return
        recognizer?.stopListening()
    }

    fun cancel() {
        recognizer?.cancel()
        if (listening) {
            listening = false
            callbacks.onListeningStop()
        }
    }

    fun destroy() {
        recognizer?.destroy()
        recognizer = null
        listening = false
    }

    private val listener = object : RecognitionListener {
        override fun onReadyForSpeech(params: Bundle?) {}
        override fun onBeginningOfSpeech() {}
        override fun onRmsChanged(rmsdB: Float) {}
        override fun onBufferReceived(buffer: ByteArray?) {}
        override fun onEndOfSpeech() {}
        override fun onEvent(eventType: Int, params: Bundle?) {}

        override fun onPartialResults(partialResults: Bundle?) {
            val words = partialResults?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)
            SpeechRecognition.topResult(words)?.let(callbacks::onPartial)
        }

        override fun onResults(results: Bundle?) {
            listening = false
            val words = results?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)
            val top = SpeechRecognition.topResult(words)
            if (top != null) callbacks.onFinal(top)
            else callbacks.onError("no speech recognized", true)
            callbacks.onListeningStop()
        }

        override fun onError(error: Int) {
            listening = false
            callbacks.onError(
                SpeechRecognition.describeError(error),
                SpeechRecognition.isRecoverable(error),
            )
            callbacks.onListeningStop()
        }
    }
}
