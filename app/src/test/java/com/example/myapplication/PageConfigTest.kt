package com.example.myapplication

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class PageConfigTest {
    private val base = AppConfigData()

    @Test
    fun readsKnownLinesFromHtmlAndIgnoresTheRest() {
        val page = listOf(
            "<html><head><script>var a=1;",
            "min_version=999</script></head><body>",
            "<p>server=https://109.73.199.217</p>",
            "<p>notice&nbsp;=&nbsp;9 мая работаем до 15:00</p>",
            "<div>stores.1.phone = 8 (978) 000-11-22<br>hours.week.7=выходной</div>",
            "password=secret<br/>MIN_VERSION = 114",
            "</body></html>"
        ).joinToString("\n")
        val map = parsePageConfig(page)
        assertEquals("9 мая работаем до 15:00", map["notice"])
        assertEquals("8 (978) 000-11-22", map["stores.1.phone"])
        assertEquals("выходной", map["hours.week.7"])
        assertEquals("114", map["min_version"])       // из скрипта не берём, регистр ключа не важен
        assertFalse("password" in map)                  // незнакомые ключи пропускаем
        assertFalse("server" in map)                    // адрес сервера разбирает ServerAddress
    }

    @Test
    fun pageOverridesServerValues() {
        val cfg = applyPageConfig(
            base,
            mapOf(
                "min_version" to "114", "notice" to "Акция", "notice.link" to "https://avtodrug92.ru/news",
                "stores.1.phone" to "8 (978) 000-11-22", "hours.week.7" to "выходной", "hours.week.6" to "10:00–16:00",
                "arrivals" to "11:00, 15:00", "hours.closed" to "2027-01-01,2027-01-02", "flags.brand_autopick" to "off",
                "hours.text" to "Пн–Сб 9:00–19:00"
            )
        )
        assertEquals(114, cfg.minVersion)
        assertEquals("Акция", cfg.noticeText)
        assertEquals("https://avtodrug92.ru/news", cfg.noticeLink)
        assertEquals("+79780001122", cfg.stores[0].phone)
        assertEquals("+7 (978) 000-11-22", cfg.stores[0].phoneDisplay)
        assertEquals(base.stores[0].address, cfg.stores[0].address)    // адрес не трогали
        assertEquals(base.stores[1], cfg.stores[1])                    // второй магазин не трогали
        assertNull(cfg.schedule.week[6])
        assertEquals(DayHours(10 * 60, 16 * 60), cfg.schedule.week[5])
        assertEquals(listOf(11 * 60, 15 * 60), cfg.schedule.arrivals)
        assertEquals(setOf("2027-01-01", "2027-01-02"), cfg.schedule.closed)
        assertFalse(cfg.flags.getValue("brand_autopick"))
        assertTrue(cfg.flags.getValue("vpn_warning"))
        assertEquals("Пн–Сб 9:00–19:00", cfg.hoursText)
    }

    @Test
    fun wrongValuesAreNotApplied() {
        val cfg = applyPageConfig(
            base,
            mapOf(
                "min_version" to "много", "stores.1.phone" to "12345", "hours.week.1" to "с утра", "arrivals" to "15:00, потом",
                "chat_url" to "http://evil.example", "notice.link" to "javascript:alert(1)", "flags.vpn_warning" to "может быть",
                "stores.3.name" to "Третий"       // магазина нет, а адреса и телефона не дали — не заводим
            )
        )
        assertEquals(base, cfg)
    }

    @Test
    fun newStoreNeedsAddressAndPhone() {
        val cfg = applyPageConfig(base, mapOf("stores.3.address" to "ул. Новая, 1", "stores.3.phone" to "+7 978 111-22-33"))
        assertEquals(3, cfg.stores.size)
        assertEquals("ул. Новая, 1", cfg.stores[2].name)
        assertEquals("+79781112233", cfg.stores[2].phone)
    }

    @Test
    fun emptyPageKeepsServerValues() {
        assertEquals(base, applyPageConfig(base, emptyMap()))
        assertEquals(emptyMap<String, String>(), parsePageConfig("<html><body>Страница не найдена</body></html>"))
    }

    @Test
    fun noArrivalsAndNoClosedDays() {
        val cfg = applyPageConfig(base, mapOf("arrivals" to "нет", "hours.closed" to "нет"))
        assertEquals(emptyList<Int>(), cfg.schedule.arrivals)
        assertEquals(emptySet<String>(), cfg.schedule.closed)
    }
}
