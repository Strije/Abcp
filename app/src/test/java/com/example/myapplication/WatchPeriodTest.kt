package com.example.myapplication

import com.example.myapplication.abcp.hasActiveOrders
import com.example.myapplication.abcp.isClosedStatus
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** Редкая фоновая проверка, когда незавершённых заказов нет, и размер фото для распознавания. */
class WatchPeriodTest {

    @Test
    fun closedStatuses() {
        assertTrue(isClosedStatus("Выдано"))
        assertTrue(isClosedStatus("Отказ поставщика"))
        assertTrue(isClosedStatus(" возврат "))
        assertTrue(isClosedStatus("Отменён"))
        assertFalse(isClosedStatus("Готово к выдаче"))
        assertFalse(isClosedStatus("В пути"))
        assertFalse(isClosedStatus("Новый статус магазина"))
    }

    @Test
    fun activeOrdersBySnapshot() {
        // Снимка ещё нет — не знаем, проверяем как обычно
        assertTrue(hasActiveOrders(null))
        assertFalse(hasActiveOrders(emptyMap()))
        assertFalse(hasActiveOrders(mapOf("1|a|b|0" to "Выдано", "2|a|b|0" to "Отказ")))
        assertTrue(hasActiveOrders(mapOf("1|a|b|0" to "Выдано", "2|a|b|0" to "В пути")))
    }

    @Test
    fun photoSampleSize() {
        assertEquals(1, scanSampleSize(1600, 1200))
        assertEquals(1, scanSampleSize(3999, 3000))
        assertEquals(2, scanSampleSize(4000, 3000))   // 12 Мп → 2000×1500
        assertEquals(2, scanSampleSize(3000, 4000))   // книжный снимок — по длинной стороне
        assertEquals(4, scanSampleSize(12000, 9000))  // 108 Мп → 3000×2250
        assertEquals(8, scanSampleSize(16320, 12240)) // 200 Мп → 2040×1530
        assertEquals(1, scanSampleSize(0, 0))
    }
}
