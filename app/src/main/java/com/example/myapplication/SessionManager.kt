package com.example.myapplication

import android.content.Context

class SessionManager(val context: Context) {
    private val prefs = context.getSharedPreferences("abcp_session", Context.MODE_PRIVATE)

    fun save(login: String, passMd5: String) {
        prefs.edit().putString("login", login).putString("pass_md5", passMd5).apply()
    }

    fun login(): String = prefs.getString("login", "") ?: ""
    fun passMd5(): String = prefs.getString("pass_md5", "") ?: ""
    fun isLoggedIn(): Boolean = login().isNotBlank() && passMd5().isNotBlank()

    /** Профиль с прошлого входа — чтобы приложение открывалось сразу, без ожидания ABCP. */
    fun saveUser(json: String) { prefs.edit().putString("user_json", json).apply() }
    fun userJson(): String? = prefs.getString("user_json", null)

    fun clear() {
        prefs.edit().clear().apply()
    }
}