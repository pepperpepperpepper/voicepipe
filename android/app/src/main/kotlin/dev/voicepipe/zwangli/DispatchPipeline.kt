package dev.voicepipe.zwangli

import android.content.Context

class DispatchPipeline(
    context: Context,
    private val client: DispatchClient = DispatchClient(),
    private val executor: ClientActionExecutor = ClientActionExecutor(context.applicationContext),
) {

    data class Outcome(
        val response: DispatchResponse? = null,
        val summary: ClientActionExecutor.Summary? = null,
        val error: Throwable? = null,
    )

    fun run(serverUrl: String, token: String, transcript: String): Outcome {
        val url = Settings.normalizeUrl(serverUrl)
        val response = try {
            client.dispatch(
                url,
                token,
                DispatchRequest(
                    transcript = transcript,
                    capabilities = ClientActions.CAPABILITIES,
                ),
            )
        } catch (e: Throwable) {
            return Outcome(error = e)
        }
        val summary = executor.execute(response.clientActions)
        return Outcome(response = response, summary = summary)
    }

    /** Audio path: upload a recorded clip; the server transcribes + dispatches. */
    fun runAudio(serverUrl: String, token: String, audio: ByteArray): Outcome {
        val url = Settings.normalizeUrl(serverUrl)
        val response = try {
            client.transcribeDispatch(url, token, audio, ClientActions.CAPABILITIES)
        } catch (e: Throwable) {
            return Outcome(error = e)
        }
        val summary = executor.execute(response.clientActions)
        return Outcome(response = response, summary = summary)
    }
}
