package com.example.myapplication

import java.security.MessageDigest

fun md5(input: String): String {
    val md = MessageDigest.getInstance("MD5")
    return md.digest(input.toByteArray()).joinToString("") { "%02x".format(it) }
}