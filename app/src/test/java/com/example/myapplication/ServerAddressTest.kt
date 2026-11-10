package com.example.myapplication

import com.example.myapplication.server.ServerAddress
import org.junit.Assert.assertEquals
import org.junit.Test

class ServerAddressTest {
    @Test
    fun parsesAddressFromSitePage() {
        assertEquals("https://109.73.199.217", ServerAddress.parse("server=https://109.73.199.217"))
        // ABCP может обернуть строку в разметку и добавить «/» — не мешает
        assertEquals("https://api.avtodrug92.ru", ServerAddress.parse("<p>server = https://api.avtodrug92.ru/</p>"))
        assertEquals("https://1.2.3.4:8443", ServerAddress.parse("<div>server=https://1.2.3.4:8443</div>"))
        // Без https или без строки — не трогаем адрес
        assertEquals(null, ServerAddress.parse("server=http://1.2.3.4"))
        assertEquals(null, ServerAddress.parse("<html><body class=\"x\">сайт целиком</body></html>"))
    }
}
