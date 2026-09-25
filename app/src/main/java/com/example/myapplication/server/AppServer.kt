package com.example.myapplication.server

import android.content.Context
import com.example.myapplication.BuildConfig
import com.example.myapplication.SessionManager
import com.google.gson.Gson
import com.google.gson.JsonObject
import com.google.gson.JsonParser
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import java.util.concurrent.TimeUnit

data class Finance(
    val balance: Double,
    val debt: Double,
    val saldo: Double,
    val creditLimit: Double,
    val overdueSaldo: Double,
    val inStopList: Boolean,
    val profile: String?
)

class ServerException(message: String, val code: Int = 0) : Exception(message)

/**
 * Наш сервер (server/ в репозитории): то, что ABCP отдаёт только API-администратору —
 * баланс, ссылки на оплату, картинки. Вход — тем же логином клиента, сервер выдаёт токен.
 */
class AppServer(ctx: Context) {

    private val session = SessionManager(ctx)
    private val prefs = ctx.getSharedPreferences("abcp_session", Context.MODE_PRIVATE) // очищается при выходе
    private val base = BuildConfig.SERVER_URL.trimEnd('/')

    val enabled: Boolean get() = base.isNotBlank()

    suspend fun finance(): Finance {
        financeCache?.let { (at, f) -> if (System.currentTimeMillis() - at < 60_000L) return f }
        return loadFinance().also { financeCache = System.currentTimeMillis() to it }
    }

    private suspend fun loadFinance(): Finance {
        val o = get("/v1/me/finance").asJsonObject
        fun d(k: String) = o.get(k)?.takeIf { !it.isJsonNull }?.asDouble ?: 0.0
        return Finance(
            balance = d("balance"), debt = d("debt"), saldo = d("saldo"),
            creditLimit = d("creditLimit"), overdueSaldo = d("overdueSaldo"),
            inStopList = o.get("inStopList")?.asBoolean == true,
            profile = o.get("profile")?.takeIf { !it.isJsonNull }?.asString
        )
    }

    /** Ссылка на оплату заказа (сервер проверит, что заказ свой и не оплачен). */
    suspend fun payLink(order: String): String = get("/v1/orders/$order/pay").asJsonObject["url"].asString

    suspend fun topupLink(amount: Double): String =
        get("/v1/topup?amount=${"%.2f".format(java.util.Locale.US, amount)}").asJsonObject["url"].asString

    /** Картинки для списка «бренд|номер» → URL. Ошибки не критичны: без картинок выдача всё равно работает. */
    suspend fun images(items: List<Pair<String, String>>): Map<String, List<String>> {
        if (!enabled || items.isEmpty()) return emptyMap()
        val body = JsonObject().apply {
            add("items", com.google.gson.JsonArray().apply {
                items.take(40).forEach { (b, n) -> add(JsonObject().apply { addProperty("brand", b); addProperty("number", n) }) }
            })
        }
        val o = post("/v1/images", body.toString()).asJsonObject
        return o.entrySet().associate { (k, v) -> k to v.asJsonArray.map { it.asString } }
    }

    /** Достоверные аналоги артикула: множество «БРЕНД|НОМЕР» (numberFix, верхний регистр). */
    suspend fun reliable(brand: String, number: String): Set<String> {
        if (!enabled) return emptySet()
        val body = JsonObject().apply { addProperty("brand", brand); addProperty("number", number) }
        return post("/v1/reliable", body.toString()).asJsonObject["reliable"].asJsonArray.map { it.asString }.toSet()
    }

    /** Служебная заметка к заказу «оформлен через приложение» — для статистики магазина, клиент её не видит. */
    suspend fun markOrderFromApp(number: String) {
        call { token ->
            Request.Builder().url("$base/v1/orders/$number/app-note")
                .post("{}".toRequestBody(JSON)).auth(token)
                .header("X-App-Version", BuildConfig.VERSION_NAME).build()
        }
    }

    // ---------- чат через Битрикс24 ----------

    suspend fun chatEnabled(): Boolean = publicGet("/v1/chat/status").asJsonObject["enabled"]?.asBoolean == true

    suspend fun chatMessages(after: Long): List<com.example.myapplication.ChatMessage> =
        get("/v1/chat/messages?after=$after").asJsonObject.getAsJsonArray("messages").map {
            val o = it.asJsonObject
            com.example.myapplication.ChatMessage(
                o["id"].asLong, o["dir"].asString, o["text"].asString, o["author"]?.asString.orEmpty(), o["ts"].asLong
            )
        }

    suspend fun chatSend(text: String): Long =
        post("/v1/chat/send", Gson().toJson(mapOf("text" to text))).asJsonObject["id"].asLong

    // ---------- push RuStore ----------

    /** Возвращает, включён ли push на сервере. */
    suspend fun pushToken(token: String): Boolean =
        post("/v1/push/token", Gson().toJson(mapOf("token" to token))).asJsonObject["push"]?.asBoolean == true

    suspend fun pushTokenDelete(token: String) = withContext(Dispatchers.IO) {
        execute(Request.Builder().url("$base/v1/push/token")
            .delete(Gson().toJson(mapOf("token" to token)).toRequestBody(JSON)).build())
    }

    // ---------- права на API ABCP (включает менеджер вручную) ----------

    /** Заявку можно слать повторно (сервер не дублирует) — поэтому при обрыве связи пробуем ещё раз. */
    suspend fun accessRequest(missing: List<String>) {
        val body = Gson().toJson(mapOf("missing" to missing))
        try {
            post("/v1/access-request", body)
        } catch (e: ServerException) {
            if (e.code != 0) throw e
            post("/v1/access-request", body)
        }
    }

    suspend fun accessDone() {
        call { token -> Request.Builder().url("$base/v1/access-request").delete().auth(token).build() }
    }

    // ---------- без входа: регистрация и восстановление пароля (ABCP пускает их только с IP сервера) ----------

    suspend fun register(name: String, surname: String, mobile: String, email: String, password: String, office: String): Boolean {
        val body = Gson().toJson(mapOf("name" to name, "surname" to surname, "mobile" to mobile,
            "email" to email, "password" to password, "office" to office))
        return publicPost("/v1/register", body).asJsonObject["needsActivation"]?.asBoolean == true
    }

    /** Шаг 1 (code пустой): отправить код. Шаг 2: код из SMS + новый пароль. */
    suspend fun restore(emailOrMobile: String, code: String = "", passwordNew: String = ""): String? {
        val body = Gson().toJson(mapOf("emailOrMobile" to emailOrMobile, "code" to code, "passwordNew" to passwordNew))
        return publicPost("/v1/restore", body).asJsonObject["message"]?.takeIf { !it.isJsonNull }?.asString
    }

    /** Поиск без входа: цены профиля гостя, закупочных цен сервер не отдаёт. */
    suspend fun guestBrands(number: String) =
        publicGet("/v1/guest/brands?number=${java.net.URLEncoder.encode(number, "UTF-8")}")

    suspend fun guestOffers(number: String, brand: String, all: Boolean) = publicGet(
        "/v1/guest/offers?number=${java.net.URLEncoder.encode(number, "UTF-8")}" +
            "&brand=${java.net.URLEncoder.encode(brand, "UTF-8")}&all=${if (all) 1 else 0}"
    )

    private suspend fun publicGet(path: String) = withContext(Dispatchers.IO) {
        if (!enabled) throw ServerException("Сервер не настроен")
        val (code, text) = execute(Request.Builder().url(base + path).get().build())
        if (code !in 200..299) throw ServerException(detail(text) ?: "Ошибка сервера ($code)", code)
        JsonParser.parseString(text)
    }

    private suspend fun publicPost(path: String, json: String) = withContext(Dispatchers.IO) {
        if (!enabled) throw ServerException("Сервер не настроен")
        val (code, text) = execute(Request.Builder().url(base + path).post(json.toRequestBody(JSON)).build())
        if (code !in 200..299) throw ServerException(detail(text) ?: "Ошибка сервера ($code)", code)
        JsonParser.parseString(text)
    }

    /**
     * Laximo через наш сервер: пароль Laximo только там. Блокирующий — вызывается из Dispatchers.IO.
     * Возвращает код и ответ Laximo как есть. Вошедший идёт со своим токеном (лимит щедрее), гость — без.
     */
    fun laximo(method: String, params: Map<String, String>): Pair<Int, String> {
        if (!enabled) throw ServerException("Сервер не настроен")
        val json = Gson().toJson(params)
        fun req(token: String?) = Request.Builder().url("$base/v1/laximo/$method")
            .post(json.toRequestBody(JSON)).apply { if (token != null) auth(token) }.build()
        // Не получилось войти на сервер — всё равно ищем, как гость
        val token = if (session.isLoggedIn()) prefs.getString(KEY, null) ?: runCatching { login() }.getOrNull() else null
        var resp = execute(req(token))
        if (resp.first == 401) resp = execute(req(runCatching { login() }.getOrNull()))
        return resp
    }

    // ---------- транспорт ----------

    private suspend fun get(path: String) = call { token -> Request.Builder().url(base + path).get().auth(token).build() }

    private suspend fun post(path: String, json: String) =
        call { token -> Request.Builder().url(base + path).post(json.toRequestBody(JSON)).auth(token).build() }

    private fun Request.Builder.auth(token: String) = header("Authorization", "Bearer $token")

    /** Токен протух или сервер перезапущен с новым секретом — получаем новый и повторяем один раз. */
    private suspend fun call(build: (String) -> Request) = withContext(Dispatchers.IO) {
        if (!enabled) throw ServerException("Сервер не настроен")
        var token = prefs.getString(KEY, null) ?: login()
        var resp = execute(build(token))
        if (resp.first == 401) {
            token = login()
            resp = execute(build(token))
        }
        val (code, text) = resp
        if (code !in 200..299) throw ServerException(detail(text) ?: "Ошибка сервера ($code)", code)
        JsonParser.parseString(text)
    }

    private fun login(): String {
        val body = Gson().toJson(mapOf("login" to session.login(), "passwordMd5" to session.passMd5()))
        val (code, text) = execute(Request.Builder().url("$base/v1/session").post(body.toRequestBody(JSON)).build())
        if (code != 200) throw ServerException(detail(text) ?: "Не удалось войти на сервер", code)
        val token = JsonParser.parseString(text).asJsonObject["token"].asString
        prefs.edit().putString(KEY, token).apply()
        return token
    }

    private fun execute(req: Request): Pair<Int, String> = try {
        http.newCall(req).execute().use { it.code to (it.body?.string().orEmpty()) }
    } catch (e: java.io.InterruptedIOException) {
        throw ServerException("Сервер не ответил. Проверьте интернет и попробуйте ещё раз.")
    } catch (e: java.io.IOException) {
        throw ServerException("Нет связи с сервером. Проверьте интернет и попробуйте ещё раз.")
    }

    private fun detail(text: String): String? =
        runCatching { JsonParser.parseString(text).asJsonObject["detail"].asString }.getOrNull()

    companion object {
        private const val KEY = "server_token"
        @Volatile private var financeCache: Pair<Long, Finance>? = null

        /** При выходе из аккаунта — чтобы следующий клиент не увидел чужой баланс */
        fun clearCache() { financeCache = null }
        private val JSON = "application/json".toMediaType()
        // Простаивающие соединения держим недолго: мобильная сеть молча рвёт их через минуту-другую,
        // и запрос по такому «мёртвому» соединению висел до Read timed out
        private val http = OkHttpClient.Builder()
            .connectTimeout(10, TimeUnit.SECONDS)
            .readTimeout(30, TimeUnit.SECONDS)
            .callTimeout(40, TimeUnit.SECONDS)
            .connectionPool(okhttp3.ConnectionPool(4, 30, TimeUnit.SECONDS))
            .build()
    }
}
