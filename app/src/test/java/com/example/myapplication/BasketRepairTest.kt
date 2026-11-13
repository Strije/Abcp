package com.example.myapplication

import com.example.myapplication.abcp.BasketItem
import com.example.myapplication.abcp.Offer
import com.example.myapplication.abcp.RepeatSource
import com.example.myapplication.abcp.basketQuantity
import com.example.myapplication.abcp.basketsToRebuild
import com.example.myapplication.abcp.isParseError
import com.example.myapplication.abcp.repeatLine
import com.google.gson.Gson
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class BasketRepairTest {

    private fun item(number: String, basket: String? = null, qty: Int = 1, key: String = "k-$number", brand: String = "MANN") =
        BasketItem(brand, number, "", 100.0, qty, 24, 48, "s-$number", key, "", null, basketId = basket)

    @Test
    fun rebuildsOnlyBasketsWithDroppedItems() {
        val a1 = item("A1", "1")
        val a2 = item("A2", "1")
        val b1 = item("B1", "2")
        val plan = basketsToRebuild(listOf(a1, a2, b1), setOf(a2))
        // Вторая корзина не тронута: в ней нет удаляемых позиций
        assertEquals(setOf<String?>("1"), plan.keys)
        assertEquals(listOf(a1, a2), plan["1"])
        assertTrue(basketsToRebuild(listOf(a1, b1), emptySet()).isEmpty())
    }

    @Test
    fun rebuildsMainBasketWithoutId() {
        val a = item("A1")
        val b = item("B1")
        assertEquals(mapOf<String?, List<BasketItem>>(null to listOf(a, b)), basketsToRebuild(listOf(a, b), setOf(b)))
    }

    @Test
    fun countsPositionInBasket() {
        val a = item("OC-90", "1", qty = 2)
        // Тот же товар, записанный иначе, — та же позиция
        val same = item("oc90", "1", qty = 3, key = a.itemKey, brand = "MANN-FILTER").copy(supplierCode = a.supplierCode)
        val otherBasket = a.copy(basketId = "2")
        val otherSupplier = a.copy(supplierCode = "zzz")
        assertEquals(0, basketQuantity(emptyList(), a))
        assertEquals(5, basketQuantity(listOf(a, same, otherBasket, otherSupplier), a))
        assertEquals(0, basketQuantity(listOf(otherBasket, otherSupplier), a))
    }

    private fun offer(brand: String, number: String, price: Double, hours: Int = 48, packing: Int = 1) =
        Offer(brand, number, number, "", price, 1, packing, hours, 0, "s", "k", "", false)

    @Test
    fun repeatComparesBrandsNormalized() {
        val src = RepeatSource("MANN-FILTER", "W 712/95", 1, 500.0)
        val line = repeatLine(src, listOf(offer("Mann", "W71295", 700.0), offer("Redskin", "W71295", 100.0)))
        assertEquals("Mann", line.offer?.brand)
        assertFalse(line.checkFailed)
    }

    @Test
    fun repeatPicksFastestThenCheapestAndRoundsToPacking() {
        val src = RepeatSource("MANN", "OC90", 3, 0.0)
        val line = repeatLine(src, listOf(offer("MANN", "OC90", 300.0, hours = 72), offer("MANN", "OC90", 400.0, hours = 0, packing = 2)))
        assertEquals(400.0, line.offer!!.price, 0.001)
        assertEquals(4, line.qty)
    }

    @Test
    fun repeatTellsNetworkFailureFromNoOffers() {
        val src = RepeatSource("MANN", "OC90", 2, 0.0)
        val empty = repeatLine(src, emptyList())
        assertNull(empty.offer)
        assertFalse(empty.checkFailed)
        val failed = repeatLine(src, null)
        assertNull(failed.offer)
        assertTrue(failed.checkFailed)
        assertEquals(2, failed.qty)
    }

    @Test
    fun parseErrorIsNotNetworkError() {
        // Вместо объекта заказов пришёл массив — Gson падает на разборе
        val e = runCatching { Gson().fromJson("[]", OrdersResponseDto::class.java) }.exceptionOrNull()
        assertTrue(e != null && isParseError(e))
        assertFalse(isParseError(java.io.IOException("timeout")))
        assertFalse(isParseError(java.net.SocketTimeoutException()))
    }
}
