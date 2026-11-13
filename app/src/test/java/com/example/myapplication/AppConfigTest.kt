package com.example.myapplication

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Test

class AppConfigTest {
    private val full = """
        {
          "min_version": 120, "update_text": "Обновитесь, пожалуйста",
          "notice": {"text": "31 декабря работаем до 15:00", "link": "https://avtodrug92.ru/news1"},
          "hours": {"text": "Пн–Сб 8:00–20:00",
                    "week": [["08:00","20:00"],["08:00","20:00"],["08:00","20:00"],["08:00","20:00"],["08:00","20:00"],["08:00","20:00"],null],
                    "closed": ["2027-01-01", "2027-01-07"]},
          "arrivals": ["16:30", "11:00"],
          "stores": [{"name": "Центр", "address": "ул. Ленина, 1", "phone": "+79780000001", "phone_display": "+7 (978) 000-00-01", "office_id": "111"}],
          "city": "Симферополь",
          "chat_url": "https://chat.example.ru/a", "max_url": "https://max.example.ru/b",
          "flags": {"brand_autopick": false, "vpn_warning": true, "guest_prices": false, "new_thing": true}
        }
    """.trimIndent()

    @Test
    fun fullAnswer() {
        val c = parseAppConfig(full)!!
        assertEquals(120, c.minVersion)
        assertEquals("Обновитесь, пожалуйста", c.updateText)
        assertEquals("31 декабря работаем до 15:00", c.noticeText)
        assertEquals("https://avtodrug92.ru/news1", c.noticeLink)
        assertEquals("Пн–Сб 8:00–20:00", c.hoursText)
        assertEquals(7, c.schedule.week.size)
        assertEquals(DayHours(8 * 60, 20 * 60), c.schedule.week[0])
        assertNull(c.schedule.week[6])
        assertEquals(setOf("2027-01-01", "2027-01-07"), c.schedule.closed)
        assertEquals(listOf(11 * 60, 16 * 60 + 30), c.schedule.arrivals)
        assertEquals(listOf(Store("ул. Ленина, 1", "+79780000001", "+7 (978) 000-00-01", "111", "Центр")), c.stores)
        assertEquals("Симферополь", c.city)
        assertEquals("https://chat.example.ru/a", c.chatUrl)
        assertEquals("https://max.example.ru/b", c.maxUrl)
        assertEquals(false, c.flags["brand_autopick"])
        assertEquals(true, c.flags["vpn_warning"])
        assertEquals(false, c.flags["guest_prices"])
        assertEquals(true, c.flags["new_thing"])
    }

    @Test
    fun emptyObjectGivesDefaults() {
        val c = parseAppConfig("{}")
        assertEquals(AppConfigData(), c)
        assertEquals(0, c!!.minVersion)
        assertEquals(2, c.stores.size)
        assertEquals("27993", c.stores[0].abcpOfficeId)
        assertEquals("Севастополь", c.city)
        assertEquals(listOf(15 * 60), c.schedule.arrivals)
        assertEquals(DayHours(9 * 60, 17 * 60), c.schedule.week[5])
        assertEquals(true, c.flags["vpn_warning"])
    }

    @Test
    fun garbageIsRejected() {
        assertNull(parseAppConfig(""))
        assertNull(parseAppConfig("<html><body>502 Bad Gateway</body></html>"))
        assertNull(parseAppConfig("[1, 2, 3]"))
        assertNull(parseAppConfig("\"строка\""))
        assertNull(parseAppConfig("{\"min_version\": 5"))
        assertNull(parseAppConfig("null"))
    }

    @Test
    fun wrongFieldsFallBackOneByOne() {
        val c = parseAppConfig(
            """
            {
              "min_version": "сто", "update_text": 5,
              "notice": "нет",
              "hours": {"text": "Круглосуточно", "week": [["09:00","19:00"], 1], "closed": ["2027-01-01", "завтра", 7]},
              "arrivals": "15:00",
              "stores": [{"address": "", "phone": "+7"}, {"address": "ул. Новая, 5", "phone": "+79780000002"}, 3],
              "city": ["Севастополь"],
              "chat_url": "http://без-шифрования.ru", "max_url": null,
              "flags": {"vpn_warning": false, "guest_prices": "да"}
            }
            """.trimIndent()
        )
        assertNotNull(c)
        val def = AppConfigData()
        assertEquals(0, c!!.minVersion)
        assertEquals("", c.updateText)
        assertEquals("", c.noticeText)
        // Верные поля применились, хотя соседние — мусор
        assertEquals("Круглосуточно", c.hoursText)
        assertEquals(def.schedule.week, c.schedule.week)
        assertEquals(setOf("2027-01-01"), c.schedule.closed)
        assertEquals(def.schedule.arrivals, c.schedule.arrivals)
        // Магазин без адреса пропущен; телефон «для показа» и название взяты из того, что есть
        assertEquals(listOf(Store("ул. Новая, 5", "+79780000002", "+79780000002", "", "ул. Новая, 5")), c.stores)
        assertEquals(def.city, c.city)
        assertEquals(def.chatUrl, c.chatUrl)
        assertEquals(def.maxUrl, c.maxUrl)
        assertEquals(false, c.flags["vpn_warning"])
        assertEquals(true, c.flags["guest_prices"])
        assertEquals(true, c.flags["brand_autopick"])
    }

    @Test
    fun emptyArrivalsAndStores() {
        // Пустой список привозов — настройка, пустой список магазинов — ошибка
        val c = parseAppConfig("""{"arrivals": [], "stores": []}""")!!
        assertEquals(emptyList<Int>(), c.schedule.arrivals)
        assertEquals(2, c.stores.size)
        // Привозы из одного мусора — берём обычные
        assertEquals(listOf(15 * 60), parseAppConfig("""{"arrivals": ["после обеда"]}""")!!.schedule.arrivals)
    }

    @Test
    fun clockParsing() {
        assertEquals(9 * 60, parseClock("09:00"))
        assertEquals(9 * 60 + 5, parseClock("9:05"))
        assertEquals(24 * 60, parseClock("24:00"))
        assertNull(parseClock("25:00"))
        assertNull(parseClock("24:30"))
        assertNull(parseClock("9.00"))
        assertNull(parseClock(null))
    }
}
