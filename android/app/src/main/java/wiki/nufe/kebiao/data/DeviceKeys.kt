package wiki.nufe.kebiao.data

import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import java.security.KeyPairGenerator
import java.security.KeyStore
import java.security.PrivateKey
import java.security.Signature
import java.security.spec.ECGenParameterSpec
import java.util.Base64
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

object DeviceKeys {
    private const val STORE = "AndroidKeyStore"
    private const val DATA_ALIAS = "nufe_local_data_v1"
    // 托管到本站时证明“这是绑定过的那台手机”的 P-256 签名密钥，不可导出；只在用户勾选托管后才生成
    private const val SIGN_ALIAS = "nufe_device_sign_v2"
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

    /** 设备公钥（SPKI DER 的 base64），没有就先生成。 */
    @Synchronized
    fun publicKey(): String {
        val store = store()
        if (!store.containsAlias(SIGN_ALIAS)) {
            KeyPairGenerator.getInstance(KeyProperties.KEY_ALGORITHM_EC, STORE).apply {
                initialize(
                    KeyGenParameterSpec.Builder(SIGN_ALIAS, KeyProperties.PURPOSE_SIGN)
                        .setAlgorithmParameterSpec(ECGenParameterSpec("secp256r1"))
                        .setDigests(KeyProperties.DIGEST_SHA256)
                        .build(),
                )
            }.generateKeyPair()
        }
        return Base64.getEncoder().encodeToString(store().getCertificate(SIGN_ALIAS).publicKey.encoded)
    }

    /** ECDSA-SHA256 签名（DER）的 base64。 */
    fun sign(message: ByteArray): String {
        val key = store().getKey(SIGN_ALIAS, null) as PrivateKey
        val signature = Signature.getInstance("SHA256withECDSA").apply {
            initSign(key)
            update(message)
        }.sign()
        return Base64.getEncoder().encodeToString(signature)
    }

    fun reset() {
        val store = store()
        listOf(DATA_ALIAS, "nufe_device_sign_v1", "nufe_profile_data_v1").forEach(store::deleteEntry)
    }

    /** 取消托管后再删签名密钥：先要用它签最后一次“取消托管”的请求。 */
    fun resetSigning() {
        store().deleteEntry(SIGN_ALIAS)
    }
}
