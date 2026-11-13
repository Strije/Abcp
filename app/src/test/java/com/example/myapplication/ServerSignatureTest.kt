package com.example.myapplication

import com.example.myapplication.server.ServerAddress
import com.example.myapplication.server.decodeBase64
import com.example.myapplication.server.verifyServerSignature
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.security.KeyFactory
import java.security.KeyPair
import java.security.KeyPairGenerator
import java.security.Signature
import java.security.spec.ECGenParameterSpec
import java.security.spec.X509EncodedKeySpec
import java.util.Base64

class ServerSignatureTest {
    private fun newKeys(): KeyPair =
        KeyPairGenerator.getInstance("EC").apply { initialize(ECGenParameterSpec("secp256r1")) }.generateKeyPair()

    private fun b64(bytes: ByteArray): String = Base64.getEncoder().encodeToString(bytes)

    /** Как подписывает сервер: ECDSA P-256 / SHA-256, DER, над «avtodrug92-server:» + nonce */
    private fun sign(keys: KeyPair, nonce: String): String {
        val s = Signature.getInstance("SHA256withECDSA")
        s.initSign(keys.private)
        s.update("avtodrug92-server:$nonce".toByteArray(Charsets.UTF_8))
        return b64(s.sign())
    }

    @Test
    fun acceptsOurServer() {
        val keys = newKeys()
        val nonce = "Ab3dEf6hIj9lMn2pQr5tUv8xYz1BcD4e"
        assertTrue(verifyServerSignature(b64(keys.public.encoded), nonce, sign(keys, nonce)))
    }

    @Test
    fun rejectsWrongSignature() {
        val keys = newKeys()
        val pub = b64(keys.public.encoded)
        val nonce = "Ab3dEf6hIj9lMn2pQr5tUv8xYz1BcD4e"
        // Подпись другой строки (старый ответ, подсунутый повторно)
        assertFalse(verifyServerSignature(pub, nonce, sign(keys, "другая-строка")))
        // Подпись чужим ключом
        assertFalse(verifyServerSignature(pub, nonce, sign(newKeys(), nonce)))
        // Подпись без нашей приставки
        val bare = Signature.getInstance("SHA256withECDSA").run {
            initSign(keys.private)
            update(nonce.toByteArray(Charsets.UTF_8))
            b64(sign())
        }
        assertFalse(verifyServerSignature(pub, nonce, bare))
    }

    @Test
    fun rejectsGarbage() {
        val keys = newKeys()
        val pub = b64(keys.public.encoded)
        val nonce = "Ab3dEf6hIj9lMn2pQr5tUv8xYz1BcD4e"
        val good = sign(keys, nonce)
        assertFalse(verifyServerSignature(pub, nonce, ""))
        assertFalse(verifyServerSignature(pub, nonce, "не base64 !!!"))
        assertFalse(verifyServerSignature(pub, nonce, "AAAA"))
        assertFalse(verifyServerSignature("", nonce, good))
        assertFalse(verifyServerSignature("AAAA", nonce, good))
        assertFalse(verifyServerSignature(pub, "", good))
    }

    @Test
    fun base64MatchesStandard() {
        for (n in 0..40) {
            val bytes = ByteArray(n) { (it * 37 + n * 11).toByte() }
            if (n == 0) assertNull(decodeBase64(b64(bytes))) else assertArrayEquals(bytes, decodeBase64(b64(bytes)))
        }
        // Переносы строк не мешают, посторонние знаки — ошибка
        assertArrayEquals("привет".toByteArray(), decodeBase64(" 0L/RgNC4\n0LLQtdGC "))
        assertNull(decodeBase64("0L/RgNC4*0LLQtdGC"))
    }

    @Test
    fun builtInKeyIsValidEcKey() {
        val bytes = decodeBase64(BuildConfig.SERVER_KEY)!!
        assertEquals("EC", KeyFactory.getInstance("EC").generatePublic(X509EncodedKeySpec(bytes)).algorithm)
    }

    @Test
    fun onlyServerLineIsRead() {
        assertEquals("https://1.2.3.4", ServerAddress.parse("min_version=120\nserver=https://1.2.3.4\nnotice=Привет"))
        assertEquals(null, ServerAddress.parse("old_server=https://1.2.3.4"))
    }
}
