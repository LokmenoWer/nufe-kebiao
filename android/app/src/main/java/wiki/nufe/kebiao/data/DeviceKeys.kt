package wiki.nufe.kebiao.data

import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

object DeviceKeys {
    private const val STORE = "AndroidKeyStore"
    private const val DATA_ALIAS = "nufe_local_data_v1"
    private const val IV_BYTES = 12

    private fun store() = KeyStore.getInstance(STORE).apply { load(null) }

    @Synchronized
    private fun dataKey(): SecretKey {
        (store().getKey(DATA_ALIAS, null) as SecretKey?)?.let { return it }
        return KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, STORE).apply {
            init(
                KeyGenParameterSpec.Builder(
                    DATA_ALIAS,
                    KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT,
                ).setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                    .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                    .setKeySize(256)
                    .build(),
            )
        }.generateKey()
    }

    fun encrypt(plain: ByteArray, purpose: String): ByteArray {
        val cipher = Cipher.getInstance("AES/GCM/NoPadding").apply {
            init(Cipher.ENCRYPT_MODE, dataKey())
            updateAAD(purpose.toByteArray(Charsets.UTF_8))
        }
        return cipher.iv + cipher.doFinal(plain)
    }

    fun decrypt(blob: ByteArray, purpose: String): ByteArray {
        require(blob.size >= IV_BYTES + 16) { "Invalid encrypted data" }
        val cipher = Cipher.getInstance("AES/GCM/NoPadding").apply {
            init(Cipher.DECRYPT_MODE, dataKey(), GCMParameterSpec(128, blob, 0, IV_BYTES))
            updateAAD(purpose.toByteArray(Charsets.UTF_8))
        }
        return cipher.doFinal(blob, IV_BYTES, blob.size - IV_BYTES)
    }

    fun reset() {
        val store = store()
        listOf(DATA_ALIAS, "nufe_device_sign_v1", "nufe_profile_data_v1").forEach(store::deleteEntry)
    }
}
