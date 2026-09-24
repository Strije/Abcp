package com.example.myapplication

import com.example.myapplication.abcp.changedStatuses
import com.example.myapplication.abcp.collectPositionStatuses
import com.google.gson.JsonParser
import org.junit.Assert.assertEquals
import org.junit.Test

class OrderStatusTest {

    // orders?format=p: items — объект по номеру заказа, positions — массив или объект
    private fun response(status1: String, extra: String = "") = JsonParser.parseString(
        """
        {"count":"2","items":{
          "1001":{"number":"1001","positions":[
             {"brand":"VAG","number":"04E115561H","description":"Фильтр масляный","status":"$status1"},
             {"brand":"NGK","number":"LZKR6B10E","description":"Свеча","status":"В пути"}$extra]},
          "1002":{"number":"1002","positions":{"0":{"brand":"KYB","number":"333305","status":"Выдано"}}}
        }}
        """
    )

    @Test
    fun readsBothFormats() {
        val all = collectPositionStatuses(response("В пути"))
        assertEquals(3, all.size)
        assertEquals("Фильтр масляный", all.first().title)
        assertEquals("KYB 333305", all.last().title) // нет описания — бренд и номер
    }

    @Test
    fun reportsOnlyChangedPositions() {
        val before = collectPositionStatuses(response("В пути")).associate { it.key to it.status }
        val after = collectPositionStatuses(response("Готово к выдаче"))
        val changed = changedStatuses(before, after)
        assertEquals(1, changed.size)
        assertEquals("1001", changed[0].order)
        assertEquals("Готово к выдаче", changed[0].status)
    }

    @Test
    fun newPositionsAreNotNotifications() {
        val before = collectPositionStatuses(response("В пути")).associate { it.key to it.status }
        val after = collectPositionStatuses(
            response("В пути", """,{"brand":"X","number":"1","status":"Оформлен"}""")
        )
        assertEquals(0, changedStatuses(before, after).size)
    }
}
