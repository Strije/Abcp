package com.example.myapplication

import com.example.myapplication.abcp.cleanNumber
import com.example.myapplication.abcp.formatDelivery
import com.example.myapplication.abcp.items
import com.google.gson.JsonParser
import org.junit.Assert.assertEquals
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
}
