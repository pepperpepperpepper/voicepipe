package dev.voicepipe.zwangli

import android.content.Context
import android.content.pm.PackageManager
import android.provider.ContactsContract
import androidx.core.content.ContextCompat

/**
 * Resolves a spoken recipient *name* ("Ran Blake") to an email address by
 * querying the device's Contacts. Used so "email Ran Blake" addresses the mail
 * to the right person instead of dropping the literal name into the To field.
 *
 * All lookups are best-effort: a missing READ_CONTACTS permission, no match, or
 * any provider error returns null and the caller falls back to the raw name.
 */
object ContactResolver {

    /** True when an input already looks like an email address (skip lookup). */
    fun looksLikeEmail(value: String): Boolean {
        val v = value.trim()
        return v.contains('@') && !v.contains(' ')
    }

    /**
     * Best email address for a contact whose display name matches [name], or
     * null. Prefers an exact (case-insensitive) display-name match; otherwise
     * falls back to the first prefix match.
     */
    fun emailForName(context: Context, name: String): String? {
        val query = name.trim()
        if (query.isEmpty()) return null
        if (ContextCompat.checkSelfPermission(context, android.Manifest.permission.READ_CONTACTS)
            != PackageManager.PERMISSION_GRANTED
        ) {
            return null
        }
        return try {
            context.contentResolver.query(
                ContactsContract.CommonDataKinds.Email.CONTENT_URI,
                arrayOf(
                    ContactsContract.CommonDataKinds.Email.ADDRESS,
                    ContactsContract.CommonDataKinds.Email.DISPLAY_NAME_PRIMARY,
                ),
                "${ContactsContract.CommonDataKinds.Email.DISPLAY_NAME_PRIMARY} LIKE ?",
                arrayOf("%$query%"),
                null,
            )?.use { cursor ->
                val addrIdx = cursor.getColumnIndex(ContactsContract.CommonDataKinds.Email.ADDRESS)
                val nameIdx =
                    cursor.getColumnIndex(ContactsContract.CommonDataKinds.Email.DISPLAY_NAME_PRIMARY)
                if (addrIdx < 0) return null
                var firstAddress: String? = null
                while (cursor.moveToNext()) {
                    val address = cursor.getString(addrIdx)?.takeIf { it.isNotBlank() } ?: continue
                    val display = if (nameIdx >= 0) cursor.getString(nameIdx).orEmpty() else ""
                    if (display.equals(query, ignoreCase = true)) return address // exact wins
                    if (firstAddress == null) firstAddress = address
                }
                firstAddress
            }
        } catch (_: Exception) {
            null
        }
    }

    /**
     * Phone numbers for contacts whose display name matches [name]. Used so
     * "call Sam Spears" dials the contact rather than searching the web for a
     * business. Exact (case-insensitive) display-name matches are returned
     * first; otherwise any prefix/substring matches follow. De-duplicates by
     * number. Returns an empty list on no match / no permission / any error.
     */
    fun phonesForName(context: Context, name: String): List<CallCandidate> {
        val query = name.trim()
        if (query.isEmpty()) return emptyList()
        if (ContextCompat.checkSelfPermission(context, android.Manifest.permission.READ_CONTACTS)
            != PackageManager.PERMISSION_GRANTED
        ) {
            return emptyList()
        }
        return try {
            context.contentResolver.query(
                ContactsContract.CommonDataKinds.Phone.CONTENT_URI,
                arrayOf(
                    ContactsContract.CommonDataKinds.Phone.NUMBER,
                    ContactsContract.CommonDataKinds.Phone.DISPLAY_NAME_PRIMARY,
                ),
                "${ContactsContract.CommonDataKinds.Phone.DISPLAY_NAME_PRIMARY} LIKE ?",
                arrayOf("%$query%"),
                null,
            )?.use { cursor ->
                val numIdx = cursor.getColumnIndex(ContactsContract.CommonDataKinds.Phone.NUMBER)
                val nameIdx =
                    cursor.getColumnIndex(ContactsContract.CommonDataKinds.Phone.DISPLAY_NAME_PRIMARY)
                if (numIdx < 0) return emptyList()
                val seen = HashSet<String>()
                val exact = ArrayList<CallCandidate>()
                val partial = ArrayList<CallCandidate>()
                while (cursor.moveToNext()) {
                    val number = cursor.getString(numIdx)?.takeIf { it.isNotBlank() } ?: continue
                    val display = if (nameIdx >= 0) cursor.getString(nameIdx).orEmpty() else ""
                    val key = number.filter { !it.isWhitespace() }
                    if (!seen.add(key)) continue
                    val candidate = CallCandidate(name = display.ifBlank { null }, phone = number)
                    if (display.equals(query, ignoreCase = true)) exact.add(candidate)
                    else partial.add(candidate)
                }
                exact + partial
            } ?: emptyList()
        } catch (_: Exception) {
            emptyList()
        }
    }
}
