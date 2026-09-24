package com.example.myapplication

import com.example.myapplication.abcp.cleanNumber
import com.example.myapplication.abcp.formatDelivery
import com.example.myapplication.abcp.items
import com.google.gson.JsonParser
import org.junit.Assert.assertEquals
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
    fun deliveryInDays() {
        assertEquals("сегодня", formatDelivery(0))
        assertEquals("1 дн.", formatDelivery(24))
        assertEquals("2 дн.", formatDelivery(25))
        assertEquals("3–5 дн.", formatDelivery(72, 120))
    }
}
