package wiki.nufe.kebiao.data

import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import java.security.KeyPairGenerator
import java.security.KeyStore
import java.security.PrivateKey
import java.security.Signature
import java.security.spec.ECGenParameterSpec
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

/**
 * 本机密钥，都放在 Android Keystore 里、不可导出：
 * - 设备签名密钥（P-256）：向服务器证明“这是配对过的那台手机”；
 * - 个人信息加密密钥（AES-GCM）：个人信息落盘前加密。
 */
object DeviceKeys {
    private const val STORE = "AndroidKeyStore"
    private const val SIGN_ALIAS = "nufe_device_sign_v1"
    private const val DATA_ALIAS = "nufe_profile_data_v1"
    private const val IV_BYTES = 12
    private const val TAG_BITS = 128

    private fun store() = KeyStore.getInstance(STORE).apply { load(null) }

    /** 公钥（SPKI DER 的 base64），没有就先生成。 */
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
        val encoded = store().getCertificate(SIGN_ALIAS).publicKey.encoded
        return Base64.encodeToString(encoded, Base64.NO_WRAP)
    }

    /** ECDSA-SHA256 签名（DER）的 base64。 */
    fun sign(message: ByteArray): String {
        val key = store().getKey(SIGN_ALIAS, null) as PrivateKey
        val signature = Signature.getInstance("SHA256withECDSA").apply {
            initSign(key)
            update(message)
        }.sign()
        return Base64.encodeToString(signature, Base64.NO_WRAP)
    }

    private fun dataKey(): SecretKey {
        (store().getKey(DATA_ALIAS, null) as SecretKey?)?.let { return it }
        return KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, STORE).apply {
            init(
                KeyGenParameterSpec.Builder(
                    DATA_ALIAS,
                    KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT,
                )
                    .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                    .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                    .build(),
            )
        }.generateKey()
    }

    /** 12 字节 IV + 密文。 */
    fun encrypt(plain: ByteArray): ByteArray {
        val cipher = Cipher.getInstance("AES/GCM/NoPadding").apply { init(Cipher.ENCRYPT_MODE, dataKey()) }
        return cipher.iv + cipher.doFinal(plain)
    }

    fun decrypt(blob: ByteArray): ByteArray {
        val cipher = Cipher.getInstance("AES/GCM/NoPadding").apply {
            init(Cipher.DECRYPT_MODE, dataKey(), GCMParameterSpec(TAG_BITS, blob, 0, IV_BYTES))
        }
        return cipher.doFinal(blob, IV_BYTES, blob.size - IV_BYTES)
    }

    /** 解绑 / 移除链接：两把密钥都删掉，下次配对换新。 */
    fun reset() {
        val store = store()
        store.deleteEntry(SIGN_ALIAS)
        store.deleteEntry(DATA_ALIAS)
    }
}
