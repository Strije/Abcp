package com.example.myapplication

import com.example.myapplication.abcp.BrandHit
import com.example.myapplication.abcp.brandCandidates
import com.example.myapplication.abcp.pickDominant
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/** Выбор бренда — те же правила и случаи, что у Прайсера (search.choose_brand, запись 02.10.2026). */
class BrandPickTest {
    private fun providers(n: Int, from: Int = 0) = (from until from + n).map { "p$it" }.toSet()

    @Test
    fun mannIsPickedForW71295() {
        val found = listOf(BrandHit("MANN", "W71295", "Фильтр масляный", true), BrandHit("Redskin", "W71295", "Фильтр", true))
        // MANN 7 поставщиков против Redskin 4: 7 ≥ половины ответивших и в 1,75 раза впереди
        assertEquals("MANN", pickDominant(found, listOf(found[0] to providers(7), found[1] to providers(4, 3)))?.brand)
    }

    @Test
    fun oc90AsksBecauseMahleLeadsTooLittle() {
        val found = listOf(
            BrandHit("AM POINT", "OC90", "Фильтр масляный", true),
            BrandHit("ABSEL", "OC90", "Фильтр масляный ан. MAHLE OC90 (OC71275)", true),
            BrandHit("ZEKKERT", "OC90", "аналог MAHLE OC90", true),
            BrandHit("MAHLE", "OC90", "Фильтр масляный", true),
        )
        // MAHLE 7 + 2 упоминания = 9 против AM POINT 7: меньше чем в 1,5 раза — спрашиваем, как Прайсер
        assertNull(pickDominant(found, listOf(found[3] to providers(7), found[0] to providers(7, 2))))
        // Упоминаемый бренд проверяется первым, даже если по алфавиту он в конце
        assertEquals("MAHLE", brandCandidates(found).first().brand)
    }

    @Test
    fun compositeBrandIsMentionedByAnyPart() {
        // Как ABCP отдал OC90 10.11: бренд «Mahle/Knecht», а в описаниях — «MAHLE» и «KNECHT» по отдельности
        val found = listOf(
            BrandHit("A1 stock", "OC90", "", true),
            BrandHit("ABSEL", "OC90", "Фильтр масляный ан. MAHLE OC90 (OC71275)", true),
            BrandHit("Filtron", "OC90", "OC90_ KNECHT ,Фильтр масляный", true),
            BrandHit("Mahle/Knecht", "OC90", "Фильтр масляный", true),
        )
        assertEquals("Mahle/Knecht", brandCandidates(found).first().brand)
    }

    @Test
    fun leaderMustBeNamedByHalfOfSuppliers() {
        val found = listOf(BrandHit("ZIC", "162622", "", true), BrandHit("X", "162622", "", true), BrandHit("Y", "162622", "", true))
        // ZIC у 6 из 12 при 1–2 у остальных — берём
        assertEquals("ZIC", pickDominant(found, listOf(found[0] to providers(6), found[1] to providers(2, 6), found[2] to providers(4, 8)))?.brand)
        // Голоса разбросаны: ZIC 3, X 3, Y 2 из 8 — не берём никого
        assertNull(pickDominant(found, listOf(found[0] to providers(3), found[1] to providers(3, 3), found[2] to providers(2, 6))))
    }
}
