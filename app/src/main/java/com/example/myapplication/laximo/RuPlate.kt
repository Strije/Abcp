package com.example.myapplication.laximo

// Латинские буквы, которые на номере выглядят как кириллица: люди часто набирают их
private val latinToCyrillic = mapOf(
    'A' to 'А', 'B' to 'В', 'E' to 'Е', 'K' to 'К', 'M' to 'М', 'H' to 'Н',
    'O' to 'О', 'P' to 'Р', 'C' to 'С', 'T' to 'Т', 'Y' to 'У', 'X' to 'Х'
)

// Обычный номер легкового авто: Н207ВН154 (буква, 3 цифры, 2 буквы, регион 2–3 цифры)
private val ruPlate = Regex("^[АВЕКМНОРСТУХ]\\d{3}[АВЕКМНОРСТУХ]{2}\\d{2,3}$")

/** Возвращает номер в виде «Н207ВН154», если строка похожа на госномер РФ, иначе null. */
fun normalizeRuPlate(input: String): String? {
    val s = input.uppercase()
        .filterNot { it.isWhitespace() || it == '-' }
        .map { latinToCyrillic[it] ?: it }
        .joinToString("")
    return s.takeIf { ruPlate.matches(it) }
}
