package com.example.myapplication

import com.example.myapplication.laximo.LaximoApiException
import com.example.myapplication.laximo.laximoErrorText
import com.example.myapplication.laximo.prettifyLaximoError
import com.example.myapplication.server.ServerException
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Test

class LaximoErrorsTest {

    private val general = "Каталог временно недоступен. Попробуйте позже или напишите менеджеру в чате."
    private val notFound = "Не нашли такую машину. Проверьте номер — в VIN 17 знаков, без букв I, O и Q."
    private val noCatalog = "Каталог для этой машины сейчас недоступен. Напишите менеджеру — подберём вручную."

    // Так исключение создаёт LaximoClient
    private fun api(raw: String): LaximoApiException {
        val parts = raw.split(":", limit = 2)
        return LaximoApiException(code = parts[0], extra = parts.getOrNull(1), rawMessage = raw)
    }

    @Test
    fun knownCodes() {
        assertEquals(notFound, laximoErrorText(api("E_INVALIDPARAMETER:identString")))
        assertEquals(notFound, laximoErrorText(api("E_INVALIDPARAMETER:plateNumber")))
        assertEquals(noCatalog, laximoErrorText(api("E_CATALOGNOTEXISTS:XX123")))
        assertEquals(noCatalog, laximoErrorText(api("E_ACCESSDENIED")))
        assertEquals(noCatalog, laximoErrorText(api("E_NOTSUPPORTED:quickgroups")))
        assertEquals("Слишком много запросов подряд. Подождите минуту и попробуйте ещё раз.", laximoErrorText(api("E_TOO_MANY_REQUESTS")))
        assertEquals("Каталог отвечает слишком долго. Попробуйте ещё раз.", laximoErrorText(api("E_TIMEOUT")))
        assertEquals(notFound, prettifyLaximoError("E_INVALIDPARAMETER: identString"))
    }

    @Test
    fun unknownCodesGetGeneralText() {
        assertEquals(general, laximoErrorText(api("E_SOMETHING_NEW:details")))
        assertEquals(general, laximoErrorText(api("E_UNEXPECTED_PROBLEM")))
        // ошибка не в номере машины, а в служебном поле — тоже общая фраза
        assertEquals(general, laximoErrorText(api("E_INVALIDPARAMETER:ssd")))
        assertEquals(general, laximoErrorText(LaximoApiException()))
        assertEquals(general, laximoErrorText(IllegalStateException("HTTP 500: {...}")))
        assertEquals(general, laximoErrorText(null))
    }

    @Test
    fun neverShowsRawCode() {
        listOf("E_INVALIDPARAMETER:identString", "E_INVALIDPARAMETER:ssd", "E_CATALOGNOTEXISTS:AU1", "E_WHATEVER:x").forEach { raw ->
            val text = laximoErrorText(api(raw))
            assertFalse(text, text.contains("E_"))
            assertFalse(text, text.contains("identString"))
            assertFalse(text, text.contains("Laximo"))
        }
    }

    @Test
    fun networkFailureKeepsServerText() {
        // AppServer заворачивает обрыв связи в ServerException с code = 0 и своим текстом
        val offline = "Нет связи с сервером. Проверьте интернет и попробуйте ещё раз."
        assertEquals(offline, laximoErrorText(ServerException(offline)))
        assertEquals("Сервер не ответил. Проверьте интернет и попробуйте ещё раз.",
            laximoErrorText(ServerException("Сервер не ответил. Проверьте интернет и попробуйте ещё раз.")))
        // лимит запросов — текст сервера; прочие ответы сервера — общая фраза
        assertEquals("Слишком часто", laximoErrorText(ServerException("Слишком часто", 429)))
        assertEquals(general, laximoErrorText(ServerException("Каталог временно недоступен (500)", 500)))
        assertEquals("Нет связи с каталогом. Проверьте интернет и попробуйте ещё раз.", laximoErrorText(java.net.UnknownHostException("x")))
    }
}
