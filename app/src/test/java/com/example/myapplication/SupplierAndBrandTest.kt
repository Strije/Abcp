package com.example.myapplication

import com.example.myapplication.abcp.BadgeKind
import com.example.myapplication.abcp.BrandHit
import com.example.myapplication.abcp.autoPickBrand
import com.example.myapplication.abcp.parseSupplierBadges
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class SupplierAndBrandTest {

    // Как в выдаче avtodrug92.ru по OC90
    private val trusted = """<div class="dostavka1"><i class="fa-solid fa-house-circle-check blue" aria-hidden="true" data-toggle="tooltip" data-placement="top" title="" data-original-title="Надежный поставщик"></i></div>"""
    private val thirdParty = """<div class="dostavka1"><i class="fa-solid fa-reply ikonka ikonka--reply red" aria-hidden="true" title="" data-original-title="Возврат не возможен"></i> <i class="fa-solid fa-house-circle-xmark ikonka red" title="" data-original-title="Сторонний склад"></i> <i class="fa-solid fa-ruble-sign ikonka" title="" data-original-title="Оплата при заказе"></i></div>"""

    @Test
    fun supplierHtmlBecomesBadges() {
        val t = parseSupplierBadges(trusted)
        assertEquals(1, t.size)
        assertEquals("Надежный поставщик", t[0].text)
        assertEquals(BadgeKind.Good, t[0].kind)

        val p = parseSupplierBadges(thirdParty)
        assertEquals(listOf("Возврат не возможен", "Сторонний склад", "Оплата при заказе"), p.map { it.text })
        assertEquals(listOf(BadgeKind.Bad, BadgeKind.Bad, BadgeKind.Info), p.map { it.kind })

        assertEquals("Склад Москва", parseSupplierBadges("<b>Склад</b> Москва").single().text)
        assertEquals(0, parseSupplierBadges(null).size)
    }

    private fun hit(b: String, avail: Boolean = false) = BrandHit(b, "X1", "", avail)

    @Test
    fun brandIsPickedWhenUnambiguous() {
        assertEquals("Knecht", autoPickBrand(listOf(hit("Knecht")), null)?.brand)
        // известный заранее бренд, записанный иначе
        assertEquals("Hyundai-KIA", autoPickBrand(listOf(hit("Hyundai-KIA"), hit("Mando")), "KIA")?.brand)
        assertEquals("Mahle/Knecht", autoPickBrand(listOf(hit("Mahle/Knecht"), hit("Zekkert")), "Knecht")?.brand)
        // единственный в наличии
        assertEquals("Zekkert", autoPickBrand(listOf(hit("Mahle"), hit("Zekkert", true)), null)?.brand)
        // неоднозначно — показать список
        assertNull(autoPickBrand(listOf(hit("A", true), hit("B", true)), null))
    }
}
