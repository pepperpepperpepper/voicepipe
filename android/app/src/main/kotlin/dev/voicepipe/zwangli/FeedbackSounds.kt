package dev.voicepipe.zwangli

object FeedbackSounds {

    fun resourceFor(event: String): Int? = MAP[event]

    private val MAP: Map<String, Int> = mapOf(
        "success" to R.raw.success,
        "dispatch_ok" to R.raw.success,
        "action_ok" to R.raw.success,
        "error" to R.raw.error,
        "dispatch_error" to R.raw.error,
        "action_error" to R.raw.error,
        "action_missing" to R.raw.error,
        "match" to R.raw.match,
        "trigger_match" to R.raw.match,
        "pending" to R.raw.match,
    )
}
