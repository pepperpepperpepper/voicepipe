package dev.voicepipe.zwangli

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/** One audio self-test case from assets/test_manifest.json: a hosted sample
 *  clip and the client_action it should route to. */
@Serializable
data class TestSample(
    val label: String,
    val url: String,
    @SerialName("expect_type") val expectType: String,
    // Optional: for accessibility_global, the specific action (home/notifications/…).
    @SerialName("expect_action") val expectAction: String? = null,
)

@Serializable
data class TestManifest(val samples: List<TestSample> = emptyList())
