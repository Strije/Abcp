package com.example.myapplication.laximo

/**
 * Утилиты для картинок Laximo.
 * Laximo поддерживает size: 150,175,200,225,250 или source (оригинал).
 */
object LaximoImageSize {
    const val P150 = "150"
    const val P175 = "175"
    const val P200 = "200"
    const val P225 = "225"
    const val P250 = "250"
    const val SOURCE = "source"
}

fun resolveLaximoImageUrl(raw: String?, size: String = LaximoImageSize.P250): String? {
    val s = raw?.trim().orEmpty()
    if (s.isBlank()) return null
    if (s.contains("{image}")) return null

    var url = s.replace("%size%", size)

    // make absolute for ws.laximo.ru
    if (url.startsWith("//")) url = "https:$url"
    if (url.startsWith("/")) url = "https://ws.laximo.ru$url"

    return url
}
