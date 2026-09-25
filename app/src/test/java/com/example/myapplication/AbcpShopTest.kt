package com.example.myapplication

import com.example.myapplication.abcp.cleanNumber
import com.example.myapplication.abcp.formatDelivery
import com.example.myapplication.abcp.items
import com.example.myapplication.abcp.parseAbcpNumber
import com.google.gson.JsonParser
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class AbcpShopTest {

    @Test
    fun listsComeAsArrayOrIndexedObject() {
        assertEquals(2, items(JsonParser.parseString("""[{"a":1},{"a":2}]""")).size)
        assertEquals(2, items(JsonParser.parseString("""{"0":{"a":1},"1":{"a":2}}""")).size)
        assertEquals(0, items(JsonParser.parseString("""[]""")).size)
    }

    @Test
    fun cleansNumbersLikeAbcp() {
        assertEquals("04E115561H", cleanNumber("04e 115-561.h"))
    }

    @Test
    fun deliveryAsDate() {
        // 24 сентября 2026, 12:00 — как в выдаче сайта: 21 ч → 25 сент., 262 ч → 5 окт.
        val now = java.util.Calendar.getInstance().apply { set(2026, 8, 24, 12, 0, 0) }.timeInMillis
        assertEquals("сегодня", formatDelivery(0, now = now))
        assertEquals("завтра", formatDelivery(21, now = now))
        assertTrue(formatDelivery(262, now = now).startsWith("5 "))
        assertTrue(formatDelivery(72, 120, now = now).startsWith("27–29 "))
    }

    @Test
    fun numbersWithThousandSeparators() {
        assertEquals(4630.0, parseAbcpNumber("4 630,00"), 0.001)
        assertEquals(-4630.0, parseAbcpNumber("-4 630,00"), 0.001)
        assertEquals(4630.0, parseAbcpNumber("4630.00"), 0.001)
        assertEquals(0.0, parseAbcpNumber(null), 0.001)
    }

    @Test
    fun abcpHtmlErrorBecomesReadable() {
        val raw = "The resource is blocked Внимание!<br> цена и/или наличие изменилось<br/><b>Knecht OC90</b>"
        val clean = com.example.myapplication.abcp.cleanAbcpMessage(raw)!!
        assertEquals("Внимание!\nцена и/или наличие изменилось\nKnecht OC90", clean)
        assertTrue(com.example.myapplication.abcp.isBasketChanged(clean))
        assertFalse(com.example.myapplication.abcp.isBasketChanged("Неверный пароль"))
    }

    @Test
    fun errorHints() {
        val e = com.example.myapplication.abcp.explainAbcpError("Превышен кредитный лимит клиента")
        assertTrue(e.contains("Оплатить"))
        assertTrue(com.example.myapplication.abcp.explainAbcpError("", 502).contains("временно недоступен"))
        assertEquals("Неизвестная ошибка X", com.example.myapplication.abcp.explainAbcpError("Неизвестная ошибка X"))
        assertTrue(com.example.myapplication.abcp.prettifyAbcpError("<html>502</html>", 502).contains("временно недоступен"))
    }
}
