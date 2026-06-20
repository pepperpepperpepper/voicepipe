package dev.voicepipe.zwangli

import java.util.Calendar
import java.util.TimeZone
import org.junit.Assert.assertEquals
import org.junit.Test

class RelativeAlarmTest {

    private fun at(hour: Int, minute: Int, second: Int): Calendar =
        Calendar.getInstance(TimeZone.getTimeZone("UTC")).apply {
            set(Calendar.HOUR_OF_DAY, hour)
            set(Calendar.MINUTE, minute)
            set(Calendar.SECOND, second)
            set(Calendar.MILLISECOND, 0)
        }

    @Test
    fun `30s from a mid-minute time rounds up to the next minute (not tomorrow)`() {
        // 14:23:10 + 30s = 14:23:40 → must round up to 14:24, else the alarm
        // app schedules 14:23:00 (already past) for tomorrow.
        assertEquals(14 to 24, ClientActionExecutor.relativeAlarmHourMinute(at(14, 23, 10), 30))
    }

    @Test
    fun `sub-minute offset that crosses the minute still rounds forward`() {
        // 14:23:40 + 30s = 14:24:10 → round up to 14:25.
        assertEquals(14 to 25, ClientActionExecutor.relativeAlarmHourMinute(at(14, 23, 40), 30))
    }

    @Test
    fun `two minutes from a mid-minute time rounds up`() {
        // 14:23:40 + 120s = 14:25:40 → round up to 14:26.
        assertEquals(14 to 26, ClientActionExecutor.relativeAlarmHourMinute(at(14, 23, 40), 120))
    }

    @Test
    fun `whole-minute offset from an exact minute boundary is unchanged`() {
        // 14:23:00 + 120s = 14:25:00 (no remainder) → stays 14:25.
        assertEquals(14 to 25, ClientActionExecutor.relativeAlarmHourMinute(at(14, 23, 0), 120))
    }

    @Test
    fun `rounding up across the hour and midnight wraps correctly`() {
        // 23:59:30 + 30s = 00:00:00 next day, no remainder → 00:00.
        assertEquals(0 to 0, ClientActionExecutor.relativeAlarmHourMinute(at(23, 59, 30), 30))
        // 23:59:10 + 30s = 23:59:40 → round up to 00:00.
        assertEquals(0 to 0, ClientActionExecutor.relativeAlarmHourMinute(at(23, 59, 10), 30))
    }
}
