package com.example.myapplication

import com.example.myapplication.abcp.DEFAULT_STAGES
import com.example.myapplication.abcp.KIND_ACTION
import com.example.myapplication.abcp.KIND_DONE
import com.example.myapplication.abcp.KIND_NORMAL
import com.example.myapplication.abcp.KIND_STOP
import com.example.myapplication.abcp.KIND_WARN
import com.example.myapplication.abcp.StatusInfo
import com.example.myapplication.abcp.Step
import com.example.myapplication.abcp.expectedRange
import com.example.myapplication.abcp.formatUnixTime
import com.example.myapplication.abcp.orderStage
import com.example.myapplication.abcp.orderWarnings
import com.example.myapplication.abcp.parseOrderHistory
import com.example.myapplication.abcp.statusInfo
import com.example.myapplication.abcp.statusNote
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.GregorianCalendar
import java.util.TimeZone

class OrderTimelineTest {

    private val full = """
        {
          "created": 1763030571,
          "stages": ["Принят", "Заказан", "В пути", "Готов к выдаче", "Выдан"],
          "positions": [
            {"id": "824818198", "brand": "Mahle/Knecht", "number": "OC90",
             "steps": [{"status": "В пути (товар заказан)", "ts": 1763040000},
                       {"status": "Ожидает обработки", "ts": 1763030571}]},
            {"id": 824818199, "brand": "NGK", "number": "BKR6E", "steps": []}
          ],
          "statuses": {
            "Ожидает обработки": {"stage": 1, "hint": "Заказ принят.", "kind": "normal"},
            "Отказ складом": {"stage": 0, "hint": "Поставщик не подтвердил.", "kind": "stop"}
          },
          "future": {"x": 1}
        }
    """

    @Test
    fun parsesFullHistory() {
        val h = parseOrderHistory(full)!!
        assertEquals(1763030571L, h.created)
        assertEquals(5, h.stages.size)
        assertEquals("В пути", h.stages[2])
        // Шаги — от старых к новым, как бы их ни прислал сервер
        assertEquals(
            listOf(Step("Ожидает обработки", 1763030571L), Step("В пути (товар заказан)", 1763040000L)),
            h.steps["824818198"]
        )
        // id числом — тоже годится
        assertEquals(emptyList<Step>(), h.steps["824818199"])
        assertEquals("Mahle/Knecht OC90", h.titles["824818198"])
        assertEquals(StatusInfo(1, "Заказ принят.", KIND_NORMAL), h.statuses["Ожидает обработки"])
        assertEquals(KIND_STOP, h.statuses["Отказ складом"]?.kind)
    }

    @Test
    fun parsesEmptyObjectWithDefaults() {
        val h = parseOrderHistory("{}")
        assertNotNull(h)
        assertEquals(0L, h!!.created)
        assertEquals(DEFAULT_STAGES, h.stages)
        assertTrue(h.steps.isEmpty())
        assertTrue(h.statuses.isEmpty())
    }

    @Test
    fun garbageIsNotHistory() {
        assertNull(parseOrderHistory(""))
        assertNull(parseOrderHistory("<html>404 Not Found</html>"))
        assertNull(parseOrderHistory("[1, 2]"))
        assertNull(parseOrderHistory("null"))
        assertNull(parseOrderHistory("{\"created\": "))
    }

    @Test
    fun brokenPartsAreSkipped() {
        val h = parseOrderHistory(
            """{"created": "abc", "stages": ["Один"], "positions": [5, {"brand": "X"},
               {"id": "7", "steps": [{"ts": 5}, "x", {"status": "Выдано"}]}],
               "statuses": {"Выдано": 3, "К заказу": {"stage": 99, "kind": "new"}}}"""
        )!!
        assertEquals(0L, h.created)
        assertEquals(DEFAULT_STAGES, h.stages)
        assertEquals(listOf(Step("Выдано", 0L)), h.steps["7"])
        assertEquals(1, h.steps.size)
        assertEquals(StatusInfo(5, "", KIND_NORMAL), h.statuses["К заказу"])
        assertNull(h.statuses["Выдано"])
    }

    @Test
    fun builtInStatusTable() {
        fun check(name: String, stage: Int, kind: String) {
            val i = statusInfo(name)
            assertEquals(name, stage, i.stage)
            assertEquals(name, kind, i.kind)
            assertTrue(name, i.hint.isNotBlank())
        }
        check("Ожидает обработки", 1, KIND_NORMAL)
        check("К проверке менеджером", 1, KIND_NORMAL)
        check("Ожидает оплаты", 1, KIND_ACTION)
        check("К заказу", 2, KIND_NORMAL)
        check("Подготовил предложение", 1, KIND_ACTION)
        check("В пути (товар заказан)", 3, KIND_NORMAL)
        check("Задерживается", 3, KIND_WARN)
        check("Готово к выдаче", 4, KIND_NORMAL)
        check("Выдано", 5, KIND_DONE)
        check("Отказ клиентом", 0, KIND_STOP)
        check("Отказ складом (Не подтверждён поставщиком)", 0, KIND_STOP)
        check("Возврат", 0, KIND_STOP)
        check("Не забрано", 0, KIND_STOP)

        assertEquals(StatusInfo(1, "", KIND_NORMAL), statusInfo("Какой-то новый статус"))
        assertEquals(StatusInfo(1, "", KIND_NORMAL), statusInfo(null))
        // Разные отказы — разные подсказки
        assertTrue("по вашей просьбе" in statusInfo("Отказ клиентом").hint)
        assertTrue("не подтвердил" in statusInfo("Отказ складом (Не подтверждён поставщиком)").hint)
        assertTrue("не забрали" in statusInfo("Не забрано").hint)
    }

    @Test
    fun shortNotesForOrderList() {
        assertEquals("Ждёт оплаты", statusNote("Ожидает оплаты"))
        assertEquals("Нужен ваш ответ", statusNote("Подготовил предложение"))
        assertEquals("Задерживается", statusNote("Задерживается"))
        assertEquals("Не подтверждён поставщиком", statusNote("Отказ складом (Не подтверждён поставщиком)"))
        assertEquals("Не забран", statusNote("Не забрано"))
        assertNull(statusNote("В пути (товар заказан)"))
        assertNull(statusNote("Готово к выдаче"))
        assertNull(statusNote("Выдано"))
        assertNull(statusNote(null))
    }

    @Test
    fun serverTableWinsAndMatchesBySubstring() {
        val server = mapOf(
            "Отказ" to StatusInfo(0, "Общий отказ.", KIND_STOP),
            "Отказ складом" to StatusInfo(0, "С сервера: нет у поставщика.", KIND_STOP),
            "В пути (товар заказан)" to StatusInfo(3, "С сервера: едет.", KIND_NORMAL),
            "Готово к выдаче" to StatusInfo(4, "", KIND_ACTION)
        )
        // точное название
        assertEquals("С сервера: едет.", statusInfo("В пути (товар заказан)", server).hint)
        // без учёта регистра
        assertEquals("С сервера: едет.", statusInfo("в пути (ТОВАР заказан)", server).hint)
        // ключ сервера содержится в названии — берём самый длинный подходящий
        assertEquals("С сервера: нет у поставщика.", statusInfo("Отказ складом (Не подтверждён поставщиком)", server).hint)
        // название содержится в ключе сервера
        assertEquals("С сервера: едет.", statusInfo("В пути", server).hint)
        // у сервера нет подсказки — берём свою, а вид и этап серверные
        val ready = statusInfo("Готово к выдаче", server)
        assertEquals(KIND_ACTION, ready.kind)
        assertTrue(ready.hint.isNotBlank())
        // на сервере такого нет — запасная таблица
        assertEquals(KIND_DONE, statusInfo("Выдано", server).kind)
        assertEquals(KIND_WARN, statusInfo("Задерживается", emptyMap()).kind)
    }

    @Test
    fun orderStageOfMixedPositions() {
        // Заказ готов, когда готово всё: одна в пути, другая готова — заказ «в пути»
        val mixed = orderStage(listOf("Готово к выдаче", "В пути (товар заказан)", "Выдано"))
        assertEquals(3, mixed.stage)
        assertEquals("В пути (товар заказан)", mixed.lead)
        assertEquals(2, mixed.active)
        assertEquals(3, mixed.total)
        assertTrue(orderWarnings(mixed).isEmpty())

        // Отказанная позиция этап не тянет назад, но о ней предупреждаем
        val partly = orderStage(
            listOf("Готово к выдаче", "Отказ складом (Не подтверждён поставщиком)", "Задерживается")
        )
        assertEquals(3, partly.stage)
        assertEquals(1, partly.delayed)
        assertEquals(1, partly.refused)
        assertEquals(
            listOf("Одна позиция задерживается", "1 позиция из 3 не подтверждена поставщиком"),
            orderWarnings(partly)
        )

        val pay = orderStage(listOf("Ожидает оплаты", "Ожидает оплаты", "К заказу"))
        assertEquals(1, pay.stage)
        assertEquals(listOf("Ожидает оплаты"), pay.actions)
        assertEquals(listOf("Ждём оплату"), orderWarnings(pay))
    }

    @Test
    fun orderStageWhenNothingIsInWork() {
        val done = orderStage(listOf("Выдано", "Выдано"))
        assertEquals(5, done.stage)
        assertEquals(0, done.active)

        // Часть выдана, часть отказана — заказ для клиента завершён
        val doneWithRefusal = orderStage(listOf("Отказ клиентом", "Выдано"))
        assertEquals(5, doneWithRefusal.stage)
        assertEquals(0, doneWithRefusal.refused)

        val refused = orderStage(listOf("Отказ складом (Не подтверждён поставщиком)", "Возврат"))
        assertEquals(0, refused.stage)
        assertEquals("Отказ складом (Не подтверждён поставщиком)", refused.lead)
        // Весь заказ в отказе — отдельной строки-предупреждения нет, об этом говорит шапка
        assertTrue(orderWarnings(refused).isEmpty())

        // Позиций нет — заказ только что принят
        assertEquals(1, orderStage(emptyList()).stage)
        // Незнакомый статус — считаем, что в работе
        assertEquals(1, orderStage(listOf("Какой-то новый статус")).stage)
    }

    @Test
    fun orderStageUsesServerTable() {
        // Магазин на сервере решил, что «К заказу» — уже третий этап
        val server = mapOf("К заказу" to StatusInfo(3, "", KIND_NORMAL))
        assertEquals(3, orderStage(listOf("К заказу", "Готово к выдаче"), server).stage)
        assertEquals(2, orderStage(listOf("К заказу", "Готово к выдаче")).stage)
    }

    @Test
    fun warningsCountPositions() {
        val many = orderStage(listOf("Задерживается", "Задерживается", "В пути"))
        assertEquals(listOf("2 позиции задерживаются"), orderWarnings(many))
        val single = orderStage(listOf("Задерживается"))
        assertEquals(listOf("Деталь задерживается у поставщика"), orderWarnings(single))
        val refusedTwo = orderStage(listOf("Отказ складом", "Отказ складом", "В пути"))
        assertEquals(listOf("2 позиции из 3 не подтверждены поставщиком"), orderWarnings(refusedTwo))
    }

    @Test
    fun stepTimeIsMoscowTime() {
        fun msk(y: Int, mo: Int, d: Int) =
            GregorianCalendar(TimeZone.getTimeZone("Europe/Moscow")).apply { clear(); set(y, mo - 1, d, 12, 0) }.timeInMillis
        // 1763030571 — 13 ноября 2025, 10:42:51 UTC, в Москве 13:42
        assertEquals("13 нояб., 13:42", formatUnixTime(1763030571L, msk(2025, 11, 20)))
        // Год — если не текущий
        assertEquals("13 нояб. 2025, 13:42", formatUnixTime(1763030571L, msk(2026, 6, 15)))
        // 31 декабря 2025, 22:30 UTC — в Москве уже 1 января, 01:30
        assertEquals("1 янв., 01:30", formatUnixTime(1767220200L, msk(2026, 6, 15)))
        assertEquals("", formatUnixTime(0L))
    }

    @Test
    fun expectedMomentOfOrderPosition() {
        val r = expectedRange("2026-11-13 13:42:10", "76", "120")!!
        assertEquals(76 * 3_600_000L, r.from - r.start)
        assertEquals(120 * 3_600_000L, r.to - r.start)
        // Без верхней границы срок — одна дата
        val one = expectedRange("2026-11-13 13:42:10", "24", null)!!
        assertEquals(one.from, one.to)
        // В наличии, без срока или без даты — ждать нечего
        assertNull(expectedRange("2026-11-13 13:42:10", "0", null))
        assertNull(expectedRange("2026-11-13 13:42:10", null, null))
        assertNull(expectedRange("вчера", "24", null))
    }
}
