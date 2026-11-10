package com.example.myapplication

import com.example.myapplication.abcp.BrandHit
import com.example.myapplication.abcp.brandCandidates
import com.example.myapplication.abcp.pickDominant
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class BrandPickTest {
    // Как ABCP отдал OC90 10.11: десятки брендов по алфавиту, все «в наличии»
    private val oc90 = listOf(
        BrandHit("A1 stock", "OC90", "", true),
        BrandHit("ABSEL", "OC90", "Фильтр масляный ан. MAHLE OC90 (OC71275)", true),
        BrandHit("ADDINOL", "OC90", "", true),
        BrandHit("AKNUK", "OC90", "Фильтр масл. НЕКСИЯ ЭСПЕРО", true),
        BrandHit("AM ENG", "OC90", "Фильтр масляный", true),
        BrandHit("Bosch", "OC90", "", true),
        BrandHit("Knecht", "OC90", "Фильтр масляный", true),
        BrandHit("MAHLE", "OC90", "Фильтр масляный", true),
        BrandHit("ZEKKERT", "OC90", "аналог MAHLE OC90", true),
    )

    @Test
    fun ownerOfNumberIsCheckedFirstAndAnalogsAreNot() {
        val c = brandCandidates(oc90)
        assertEquals("MAHLE", c.first().brand)            // на него ссылаются ABSEL и ZEKKERT
        assertTrue(c.none { it.brand == "ABSEL" || it.brand == "ZEKKERT" })
        assertEquals(4, c.size)
    }

    @Test
    fun ownerWinsWhenItHasSuppliers() {
        val m = oc90.first { it.brand == "MAHLE" }
        val k = oc90.first { it.brand == "Knecht" }
        assertEquals("MAHLE", pickDominant(oc90, listOf(m to 3, k to 4))?.brand)
        // У «хозяина» один поставщик — решает обычное правило «вдвое больше второго»
        assertEquals("Knecht", pickDominant(oc90, listOf(m to 1, k to 5))?.brand)
        assertNull(pickDominant(oc90, listOf(m to 1, k to 1)))
    }
}
