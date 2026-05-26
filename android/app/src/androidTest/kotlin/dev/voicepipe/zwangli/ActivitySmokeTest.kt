package dev.voicepipe.zwangli

import androidx.lifecycle.Lifecycle
import androidx.test.core.app.ActivityScenario
import androidx.test.ext.junit.runners.AndroidJUnit4
import org.junit.Assert.assertEquals
import org.junit.Test
import org.junit.runner.RunWith

/** Smoke tests that just verify each Activity inflates and reaches RESUMED
 *  without throwing. Catches binding mistakes (a missing view ID, a typo
 *  in a Material widget class name, an unregistered manifest entry) that
 *  wouldn't surface in a JVM unit test.
 */
@RunWith(AndroidJUnit4::class)
class ActivitySmokeTest {

    @Test
    fun main_activity_reaches_resumed() {
        ActivityScenario.launch(MainActivity::class.java).use { scenario ->
            assertEquals(Lifecycle.State.RESUMED, scenario.state)
        }
    }

    @Test
    fun configurator_activity_reaches_resumed() {
        ActivityScenario.launch(ConfiguratorActivity::class.java).use { scenario ->
            assertEquals(Lifecycle.State.RESUMED, scenario.state)
        }
    }

    @Test
    fun configurator_lifecycle_survives_pause_resume() {
        ActivityScenario.launch(ConfiguratorActivity::class.java).use { scenario ->
            scenario.moveToState(Lifecycle.State.STARTED)
            scenario.moveToState(Lifecycle.State.RESUMED)
            assertEquals(Lifecycle.State.RESUMED, scenario.state)
        }
    }
}
