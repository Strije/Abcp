package com.example.myapplication

import com.example.myapplication.abcp.PositionStatus
import com.example.myapplication.abcp.SHOWN_TTL_MS
import com.example.myapplication.abcp.changedStatuses
import com.example.myapplication.abcp.collectPositionStatuses
import com.example.myapplication.abcp.takeUnshown
import com.google.gson.JsonParser
import org.junit.Assert.assertEquals
import org.junit.Test

/** Push и своя проверка видят одно изменение статуса — уведомление должно быть одно. */
class StatusShownOnceTest {

    private fun response(status: String) = JsonParser.parseString(
        """
        {"items":{"1001":{"number":"1001","positions":[
           {"brand":"VAG","number":"04E115561H","description":"Фильтр масляный","status":"$status"},
           {"brand":"NGK","number":"LZKR6B10E","description":"Свеча","status":"В пути"}]}}}
        """
    )

    /** Как Push.onMessage собирает позиции: сервер называет позицию «бренд номер» */
    private fun push(order: String, vararg items: Pair<String, String>) =
        items.mapIndexed { i, (title, status) -> PositionStatus("$order#$i", order, title, status) }

    private fun checkChanges(): List<PositionStatus> {
        val before = collectPositionStatuses(response("В пути")).associate { it.key to it.status }
        return changedStatuses(before, collectPositionStatuses(response("Готово к выдаче")))
    }

    @Test
    fun pushFirstThenCheckShowsNothing() {
        val (byPush, marks) = takeUnshown(emptyMap(), push("1001", "VAG 04E115561H" to "Готово к выдаче"), 1000)
        assertEquals(1, byPush.size)
        val (byCheck, _) = takeUnshown(marks, checkChanges(), 2000)
        assertEquals(0, byCheck.size)
    }

    @Test
    fun checkFirstThenPushShowsNothing() {
        val (byCheck, marks) = takeUnshown(emptyMap(), checkChanges(), 1000)
        assertEquals(1, byCheck.size)
        // Пробелы и регистр у сервера могут отличаться — изменение то же
        val (byPush, _) = takeUnshown(marks, push("1001", "vag  04E115561H" to "готово к выдаче"), 2000)
        assertEquals(0, byPush.size)
    }

    @Test
    fun sameCheckTwiceShowsOnce() {
        val (first, marks) = takeUnshown(emptyMap(), checkChanges(), 1000)
        val (second, _) = takeUnshown(marks, checkChanges(), 2000)
        assertEquals(1, first.size)
        assertEquals(0, second.size)
    }

    @Test
    fun otherStatusOrOrderIsNew() {
        val (_, marks) = takeUnshown(emptyMap(), push("1001", "VAG 04E115561H" to "В пути"), 1000)
        assertEquals(1, takeUnshown(marks, push("1001", "VAG 04E115561H" to "Готово к выдаче"), 2000).first.size)
        assertEquals(1, takeUnshown(marks, push("1002", "VAG 04E115561H" to "В пути"), 2000).first.size)
    }

    @Test
    fun oldMarkExpires() {
        val (_, marks) = takeUnshown(emptyMap(), checkChanges(), 1000)
        assertEquals(1, takeUnshown(marks, checkChanges(), 1000 + SHOWN_TTL_MS + 1).first.size)
    }
}
