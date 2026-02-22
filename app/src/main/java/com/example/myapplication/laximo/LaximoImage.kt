package com.example.myapplication.laximo

fun String?.resolveLaximoImage(size: Int = 240): String? {
    val s = this?.trim().orEmpty()
    if (s.isBlank()) return null
    return s.replace("%size%", size.toString())
}