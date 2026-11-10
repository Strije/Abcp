package com.example.myapplication

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class DeepLinkTest {
    private fun p(s: String?) = DeepLink.parse(s)

    @Test fun search() {
        assertEquals(DeepLink.Search("OC90"), p("avtodrug92://search?q=OC90"))
        assertEquals(DeepLink.Search("масло 5w30"), p("avtodrug92://search?q=%D0%BC%D0%B0%D1%81%D0%BB%D0%BE+5w30"))
        assertEquals(DeepLink.Search("OC 90"), p("avtodrug92://search?q=OC 90"))
        assertEquals(DeepLink.Search("OC90"), p("  AVTODRUG92://Search?Q=OC90  "))
        assertNull(p("avtodrug92://search"))
        assertNull(p("avtodrug92://search?q="))
    }

    @Test fun offers() {
        assertEquals(DeepLink.Offers("MAHLE", "OC90"), p("avtodrug92://offers?brand=MAHLE&number=OC90"))
        // без бренда — обычный поиск по номеру (выберет бренд сам)
        assertEquals(DeepLink.Search("OC90"), p("avtodrug92://offers?number=OC90"))
        assertNull(p("avtodrug92://offers?brand=MAHLE"))
    }

    @Test fun orderChatNewsVin() {
        assertEquals(DeepLink.Order("12345"), p("avtodrug92://order/12345"))
        assertEquals(DeepLink.Order("12345"), p("avtodrug92:order/12345"))
        assertNull(p("avtodrug92://order/"))
        assertNull(p("avtodrug92://order/1;drop"))
        assertEquals(DeepLink.Chat, p("avtodrug92://chat"))
        assertEquals(DeepLink.News(2), p("avtodrug92://news/2"))
        assertNull(p("avtodrug92://news/0"))
        assertNull(p("avtodrug92://news/abc"))
        assertEquals(DeepLink.Vin("XTA21099"), p("avtodrug92://vin?q=XTA21099"))
        assertEquals(DeepLink.Vin(""), p("avtodrug92://vin"))
    }

    @Test fun web() {
        assertEquals(DeepLink.Web("https://example.com/a?b=1"), p("https://example.com/a?b=1"))
        assertEquals(DeepLink.News(3), p("https://avtodrug92.ru/news3"))
        assertEquals(DeepLink.Web("https://avtodrug92.ru/catalog"), p("https://avtodrug92.ru/catalog"))
    }

    @Test fun junk() {
        assertNull(p(null))
        assertNull(p(""))
        assertNull(p("http://example.com"))       // только https
        assertNull(p("javascript:alert(1)"))
        assertNull(p("intent://x#Intent;end"))
        assertNull(p("avtodrug92://unknown"))
        assertNull(p("avtodrug92://search?q=%zz"))  // битая кодировка не роняет разбор
    }
}
