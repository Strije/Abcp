package com.example.myapplication

import com.example.myapplication.abcp.abcpMethodName
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Test

/** Счётчик запросов к ABCP: имя метода — только путь, без параметров запроса. */
class AbcpCallCounterTest {

    @Test
    fun methodByPath() {
        assertEquals("user/garage", abcpMethodName("/user/garage"))
        assertEquals("search/history", abcpMethodName("/search/history"))
        assertEquals("advices", abcpMethodName("/advices"))
        assertEquals("basket/content", abcpMethodName("/basket/content"))
        assertEquals("orders", abcpMethodName("/orders"))
        assertEquals("orders/statuses", abcpMethodName("orders/statuses/"))
        // Глубже двух сегментов не различаем: добавление и удаление машины — тот же user/garage
        assertEquals("user/garage", abcpMethodName("/user/garage/add"))
        assertEquals("other", abcpMethodName("/"))
        assertEquals("other", abcpMethodName(""))
    }

    @Test
    fun noQueryInName() {
        val name = abcpMethodName("/search/articles?userlogin=ivan@mail.ru&userpsw=abc&number=OC90")
        assertEquals("search/articles", name)
        assertFalse(name.contains("ivan"))
        // Полный адрес — тоже только путь: хост и схема отбрасываются
        assertEquals("search/brands", abcpMethodName("https://id25202.public.api.abcp.ru/search/brands?number=OC90"))
    }
}
