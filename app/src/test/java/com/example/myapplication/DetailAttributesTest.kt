package com.example.myapplication

import com.example.myapplication.laximo.attributeTitle
import com.example.myapplication.laximo.attributeValue
import com.example.myapplication.laximo.categoriesOfLevel
import com.example.myapplication.laximo.detailAttributeLines
import com.example.myapplication.laximo.model.LaximoAttribute
import com.example.myapplication.laximo.model.LaximoCategory
import com.example.myapplication.laximo.model.LaximoDetail
import org.junit.Assert.assertEquals
import org.junit.Test

/** Примечания детали в каталоге: английские названия атрибутов — по-русски; разделы каталога по уровням. */
class DetailAttributesTest {

    @Test
    fun knownTitlesTranslatedIgnoringCase() {
        assertEquals("Сторона", attributeTitle("side", "side"))
        assertEquals("Применимость", attributeTitle("usage", ""))
        assertEquals("Годы выпуска", attributeTitle("Range", "range"))
        assertEquals("Примечание", attributeTitle(null, "NOTE"))
        assertEquals("Примечание", attributeTitle("Notes", "x"))
        assertEquals("Количество", attributeTitle(null, "amount"))
        assertEquals("Количество", attributeTitle("QTY", ""))
        assertEquals("Коробка передач", attributeTitle("transmission", ""))
        assertEquals("Комплектация", attributeTitle(" Options ", ""))
    }

    @Test
    fun russianTitleFromCatalogStays() {
        // Каталог прислал своё название — словарь по ключу его не подменяет
        assertEquals("Количество", attributeTitle("Количество", "amount"))
        assertEquals("Тип кузова", attributeTitle("тип кузова", "body"))
    }

    @Test
    fun unknownTitleCapitalized() {
        assertEquals("Grade", attributeTitle("grade", "grade"))
        assertEquals("Destination", attributeTitle(null, "destination"))
        // Название незнакомое, а ключ известен — берём перевод ключа
        assertEquals("Сторона", attributeTitle("Side of car", "side"))
    }

    @Test
    fun sideValuesInWords() {
        assertEquals("левая", attributeValue("Сторона", "L"))
        assertEquals("левая", attributeValue("Сторона", "lh"))
        assertEquals("правая", attributeValue("Сторона", "R"))
        assertEquals("правая", attributeValue("Сторона", "RH"))
        assertEquals("обе", attributeValue("Сторона", "обе"))
        // У других атрибутов «L» — не сторона
        assertEquals("L", attributeValue("Размер", "L"))
    }

    @Test
    fun linesAsOnPhone() {
        val d = LaximoDetail(
            name = "Колодки", oem = "0446533471", codeOnImage = "5",
            attributes = listOf(
                LaximoAttribute("side", "side", "L"),
                LaximoAttribute("usage", "usage", "(SE,SX,CDX) (48,35,19)"),
                LaximoAttribute("range", "Range", "2003-2007"),
                LaximoAttribute("amount", "Количество", "1"),
                LaximoAttribute("note", null, " "),
                LaximoAttribute("oem", "OEM", "0446533471")
            )
        )
        assertEquals(
            listOf("Сторона: левая", "Применимость: (SE,SX,CDX) (48,35,19)", "Годы выпуска: 2003-2007", "Количество: 1"),
            detailAttributeLines(d)
        )
    }

    private fun cat(id: String, parent: String? = null, children: Boolean = false) =
        LaximoCategory(categoryId = id, name = "Раздел $id", ssd = "s", childrens = children, parentId = parent)

    @Test
    fun rootLevelWithoutNested() {
        // GM: на любой запрос приходит весь плоский список с родителями
        val flat = listOf(cat("10", children = true), cat("11", parent = "10"), cat("20", parent = "0"))
        assertEquals(listOf("10", "20"), categoriesOfLevel(flat, null).map { it.categoryId })
        assertEquals(listOf("11"), categoriesOfLevel(flat, "10").map { it.categoryId })
        // У раздела без детей в плоском списке подразделов нет (а не «все корневые»)
        assertEquals(emptyList<String>(), categoriesOfLevel(flat, "20").map { it.categoryId })
    }

    @Test
    fun childrenWithoutParentMark() {
        // Обычный каталог: на запрос раздела приходят только его дети, родитель у них не указан
        val kids = listOf(cat("31"), cat("32"))
        assertEquals(listOf("31", "32"), categoriesOfLevel(kids, "30").map { it.categoryId })
        // Родители указаны непонятно — в корне показываем всё, что пришло
        val odd = listOf(cat("1", parent = "x"), cat("2", parent = "x"))
        assertEquals(listOf("1", "2"), categoriesOfLevel(odd, null).map { it.categoryId })
    }
}
