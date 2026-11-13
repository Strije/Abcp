package com.example.myapplication.laximo

// Латинские буквы, которые на номере выглядят как кириллица: люди часто набирают их
private val latinToCyrillic = mapOf(
    'A' to 'А', 'B' to 'В', 'E' to 'Е', 'K' to 'К', 'M' to 'М', 'H' to 'Н',
    'O' to 'О', 'P' to 'Р', 'C' to 'С', 'T' to 'Т', 'Y' to 'У', 'X' to 'Х'
)

// И наоборот: VIN и номер кузова — только латиница, а набирают их часто на русской раскладке
private val cyrillicToLatin = latinToCyrillic.entries.associate { it.value to it.key }

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

/** Что отправлять в поиск машины. hint — подсказка вместо поиска: в VIN не хватает знаков или есть лишние. */
internal data class VehicleQuery(val value: String, val hint: String? = null)

/**
 * Приводит ввод к виду для поиска: госномер — «Н207ВН154»; VIN и номер кузова — заглавными, без пробелов,
 * русские буквы-двойники заменены латинскими. Дефис убираем, только если без него ровно 17 знаков (VIN):
 * в номере кузова («GX100-1234567») он нужен.
 */
internal fun normalizeVehicleQuery(input: String): VehicleQuery {
    normalizeRuPlate(input)?.let { return VehicleQuery(it) }

    val noSpaces = input.uppercase()
        .filterNot { it.isWhitespace() }
        .map { cyrillicToLatin[it] ?: it }
        .joinToString("")
    val noDashes = noSpaces.filterNot { it == '-' }
    val value = if (noDashes.length == 17) noDashes else noSpaces

    val vinLike = value.isNotEmpty() && value.all { it in 'A'..'Z' || it in '0'..'9' }
    if (vinLike && value.length in 15..19 && value.length != 17) {
        return VehicleQuery(value, "В VIN 17 знаков, у вас ${value.length}. Проверьте номер.")
    }
    return VehicleQuery(value)
}
