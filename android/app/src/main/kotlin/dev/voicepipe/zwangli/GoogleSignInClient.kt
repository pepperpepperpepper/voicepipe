package dev.voicepipe.zwangli

import android.content.Context
import androidx.credentials.ClearCredentialStateRequest
import androidx.credentials.CredentialManager
import androidx.credentials.CredentialOption
import androidx.credentials.CustomCredential
import androidx.credentials.GetCredentialRequest
import androidx.credentials.exceptions.GetCredentialException
import com.google.android.libraries.identity.googleid.GetGoogleIdOption
import com.google.android.libraries.identity.googleid.GetSignInWithGoogleOption
import com.google.android.libraries.identity.googleid.GoogleIdTokenCredential

/**
 * "Sign in with Google" via Credential Manager (Option A — requires Google
 * Play Services on the device).
 *
 * Produces a Google ID token (a JWT) that the app sends as the bearer. The
 * SERVER verifies the token (signature, aud == our Web client ID, exp, iss)
 * and enforces the single-email allowlist — this class cannot and does not
 * decide which account is allowed; any Google account can mint a token.
 *
 * The Web client ID is [BuildConfig.GOOGLE_WEB_CLIENT_ID], registered in
 * Google Cloud Console against the app package + signing SHA-1.
 */
class GoogleSignInClient(context: Context) {

    private val credentialManager = CredentialManager.create(context.applicationContext)

    /** The signed-in account: the bearer [idToken] plus the [email] for display. */
    data class Account(val idToken: String, val email: String?)

    /** Sign-in failed for a reason other than "no available account". */
    class SignInError(message: String, cause: Throwable? = null) : Exception(message, cause)

    /**
     * Interactive sign-in behind the "Sign in with Google" button — shows the
     * account chooser / consent. [activityContext] MUST be an Activity (the
     * Credential Manager UI needs it). Throws [SignInError] on failure.
     */
    suspend fun signIn(activityContext: Context): Account {
        val option = GetSignInWithGoogleOption.Builder(BuildConfig.GOOGLE_WEB_CLIENT_ID).build()
        return try {
            requestCredential(activityContext, option)
        } catch (e: GetCredentialException) {
            throw SignInError(e.message ?: "Google sign-in failed", e)
        }
    }

    /**
     * Silent re-auth for an already-authorized account (no UI), used to
     * re-mint an expiring token from an Activity without prompting. Returns
     * null when no authorized account is available — the caller should then
     * fall back to interactive [signIn].
     */
    suspend fun silentSignIn(activityContext: Context): Account? {
        val option = GetGoogleIdOption.Builder()
            .setServerClientId(BuildConfig.GOOGLE_WEB_CLIENT_ID)
            .setFilterByAuthorizedAccounts(true)
            .setAutoSelectEnabled(true)
            .build()
        return try {
            requestCredential(activityContext, option)
        } catch (e: GetCredentialException) {
            // No authorized account / user dismissal / no GMS → fall back to UI.
            null
        }
    }

    /** Clear the cached credential state (sign out). Best-effort. */
    suspend fun signOut() {
        try {
            credentialManager.clearCredentialState(ClearCredentialStateRequest())
        } catch (_: Exception) {
        }
    }

    private suspend fun requestCredential(
        activityContext: Context,
        option: CredentialOption,
    ): Account {
        val request = GetCredentialRequest.Builder().addCredentialOption(option).build()
        val response = credentialManager.getCredential(activityContext, request)
        val credential = response.credential
        if (
            credential is CustomCredential &&
            credential.type == GoogleIdTokenCredential.TYPE_GOOGLE_ID_TOKEN_CREDENTIAL
        ) {
            val google = GoogleIdTokenCredential.createFrom(credential.data)
            return Account(idToken = google.idToken, email = google.id)
        }
        throw SignInError("unexpected credential type: ${credential.type}")
    }
}
