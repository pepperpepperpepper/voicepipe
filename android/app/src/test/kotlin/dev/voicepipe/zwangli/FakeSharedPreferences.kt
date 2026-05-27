package dev.voicepipe.zwangli

import android.content.SharedPreferences

/** In-memory SharedPreferences for JVM unit tests.
 *
 *  Implements just enough of the surface area for tests that exercise
 *  [Settings] — get/put for String/Boolean/Int, remove, contains. No
 *  listeners, no async commits, no type coercion beyond what Settings uses.
 */
class FakeSharedPreferences : SharedPreferences {

    private val store = mutableMapOf<String, Any?>()

    override fun getAll(): Map<String, *> = store.toMap()

    override fun getString(key: String, defValue: String?): String? =
        store[key] as? String ?: defValue

    @Suppress("UNCHECKED_CAST")
    override fun getStringSet(key: String, defValues: Set<String>?): Set<String>? =
        store[key] as? Set<String> ?: defValues

    override fun getInt(key: String, defValue: Int): Int =
        (store[key] as? Int) ?: defValue

    override fun getLong(key: String, defValue: Long): Long =
        (store[key] as? Long) ?: defValue

    override fun getFloat(key: String, defValue: Float): Float =
        (store[key] as? Float) ?: defValue

    override fun getBoolean(key: String, defValue: Boolean): Boolean =
        (store[key] as? Boolean) ?: defValue

    override fun contains(key: String): Boolean = store.containsKey(key)

    override fun edit(): SharedPreferences.Editor = FakeEditor()

    override fun registerOnSharedPreferenceChangeListener(
        listener: SharedPreferences.OnSharedPreferenceChangeListener?,
    ) = Unit

    override fun unregisterOnSharedPreferenceChangeListener(
        listener: SharedPreferences.OnSharedPreferenceChangeListener?,
    ) = Unit

    private inner class FakeEditor : SharedPreferences.Editor {
        private val pending = mutableMapOf<String, Any?>()
        private val removals = mutableSetOf<String>()
        private var clearAll = false

        override fun putString(key: String, value: String?): SharedPreferences.Editor {
            pending[key] = value; return this
        }

        override fun putStringSet(
            key: String,
            values: Set<String>?,
        ): SharedPreferences.Editor {
            pending[key] = values; return this
        }

        override fun putInt(key: String, value: Int): SharedPreferences.Editor {
            pending[key] = value; return this
        }

        override fun putLong(key: String, value: Long): SharedPreferences.Editor {
            pending[key] = value; return this
        }

        override fun putFloat(key: String, value: Float): SharedPreferences.Editor {
            pending[key] = value; return this
        }

        override fun putBoolean(key: String, value: Boolean): SharedPreferences.Editor {
            pending[key] = value; return this
        }

        override fun remove(key: String): SharedPreferences.Editor {
            removals.add(key); return this
        }

        override fun clear(): SharedPreferences.Editor {
            clearAll = true; return this
        }

        override fun commit(): Boolean {
            applyPending()
            return true
        }

        override fun apply() {
            applyPending()
        }

        private fun applyPending() {
            if (clearAll) store.clear()
            for (k in removals) store.remove(k)
            for ((k, v) in pending) {
                if (v == null) store.remove(k) else store[k] = v
            }
        }
    }
}
