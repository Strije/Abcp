package com.example.myapplication.laximo

/**
 * Laximo image size options:
 * - Preview (keeps aspect ratio, fits into square): 150, 175, 200, 225, 250
 * - Original: source
 */
fun String?.resolveLaximoImage(size: String = LaximoImageSize.PREVIEW_250): String? {
    val s = this?.trim().orEmpty()
    if (s.isBlank()) return null

    // Иногда в проекте встречался плейсхолдер "{image}" — его нельзя грузить.
    if (s.contains("{image}", ignoreCase = true)) return null

    // В ответах Laximo картинки могут приходить:
    // - абсолютными URL
    // - protocol-relative //...
    // - относительными /...
    // - с плейсхолдером %size% (должен быть одним из: 150/175/200/225/250/source)
    val withSize = s.replace("%size%", size)

    return when {
        withSize.startsWith("http://") || withSize.startsWith("https://") -> withSize
        withSize.startsWith("//") -> "https:$withSize"
        withSize.startsWith("/") -> "https://ws.laximo.ru$withSize"
        else -> "https://ws.laximo.ru/$withSize"
    }
}

object LaximoImageSize {
    const val PREVIEW_150 = "150"
    const val PREVIEW_175 = "175"
    const val PREVIEW_200 = "200"
    const val PREVIEW_225 = "225"
    const val PREVIEW_250 = "250"
    const val SOURCE = "source"
}
