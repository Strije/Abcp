package com.example.myapplication.abcp

import android.content.Context
import com.example.myapplication.SessionManager
import com.google.gson.JsonParser
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.OkHttpClient
import okhttp3.Request

data class GarageCar(
    val vin: String,
    val title: String = vin,
    /** id машины в гараже ABCP — для удаления */
    val id: String = ""
)

/** Избранная машина — только на телефоне (в ABCP такого признака нет); её показывает «Моя машина» на главной. */
object GarageFavorite {
    private fun prefs(ctx: android.content.Context) = ctx.getSharedPreferences("garage_fav", android.content.Context.MODE_PRIVATE)
    fun get(ctx: android.content.Context): String? = prefs(ctx).getString("vin", null)
    fun set(ctx: android.content.Context, vin: String?) { prefs(ctx).edit().putString("vin", vin).apply() }
    /** Избранная первой, остальные как были */
    fun sorted(ctx: android.content.Context, cars: List<GarageCar>): List<GarageCar> {
        val fav = get(ctx)
        return cars.sortedByDescending { it.vin == fav }
    }
}

/** VIN / кузов / госномер для сравнения: без пробелов и дефисов, заглавными */
fun garageKey(s: String) = s.uppercase().filter { it.isLetterOrDigit() }

/** Один клиент на все запросы гаража — со счётчиком запросов ABCP */
private val garageHttp by lazy { OkHttpClient.Builder().addInterceptor(AbcpCallCounter.interceptor).build() }

class AbcpGarageRepository(
    private val client: OkHttpClient = garageHttp
) {
    fun loadGarage(userLogin: String, userPsw: String): List<GarageCar> = parseGarage(loadGarageJson(userLogin, userPsw))

    /** Ответ user/garage как есть — его же кладём на диск ([GarageCache]) */
    fun loadGarageJson(userLogin: String, userPsw: String): String {
        val url = "https://id25202.public.api.abcp.ru/user/garage".toHttpUrl().newBuilder()
            .addQueryParameter("userlogin", userLogin)
            .addQueryParameter("userpsw", userPsw)
            .build()

        val req = Request.Builder().url(url).get().build()

        client.newCall(req).execute().use { resp ->
            val body = resp.body?.string().orEmpty()
            if (!resp.isSuccessful) error("ABCP HTTP ${resp.code}: $body")
            return body
        }
    }
}

/** Разбор ответа user/garage: массив машин или объект с массивом в data. */
internal fun parseGarage(body: String): List<GarageCar> {
    val root = JsonParser.parseString(body)

    val arr = when {
        root.isJsonArray -> root.asJsonArray
        root.isJsonObject && root.asJsonObject.get("data")?.isJsonArray == true ->
            root.asJsonObject.getAsJsonArray("data")
        else -> error("Неожиданный формат ABCP: $body")
    }

    return arr.mapNotNull { el ->
        val o = el.asJsonObject

        // Пустое поле пропускаем: у машины, сохранённой по кузову или госномеру, "vin": ""
        val vin = listOf("vin", "VIN", "frame", "body", "chassis", "vehicleRegPlate")
            .firstNotNullOfOrNull { key ->
                o.get(key)?.takeIf { it.isJsonPrimitive }?.asString?.takeIf { it.isNotBlank() }
            }
            ?.trim()
            .orEmpty()

        if (vin.isBlank()) return@mapNotNull null

        val title = listOf("name", "title", "model", "car", "comment")
            .firstNotNullOfOrNull { key ->
                o.get(key)?.takeIf { it.isJsonPrimitive }?.asString
            }
            ?.trim()
            ?.takeIf { it.isNotBlank() }
            ?: vin

        GarageCar(vin = vin, title = title, id = o.get("id")?.takeIf { it.isJsonPrimitive }?.asString.orEmpty())
    }
}

/**
 * Гараж меняется редко, а запрашивался при каждом показе главной, вкладки «Гараж» и подбора — это «прочие»
 * запросы ABCP с общим лимитом на магазин. Держим список 10 минут в памяти и копию ответа на диске: после запуска
 * гараж виден сразу, в сеть идём не чаще раза в 10 минут. Добавили или удалили машину — [invalidate].
 */
object GarageCache {
    private const val PREFS = "garage_cache"
    private const val JSON = "json"
    private const val AT = "at"
    private const val OWNER = "owner"
    private const val TTL_MS = 10L * 60 * 1000

    /** Когда загружен список в памяти (0 — пора перечитать) */
    @Volatile private var loadedAt = 0L
    private val mutex = Mutex() // главная и вкладка просят гараж одновременно — запрос один

    private fun prefs(ctx: Context) = ctx.applicationContext.getSharedPreferences(PREFS, Context.MODE_PRIVATE)

    /** Что показать сразу, без сети: список из памяти или с диска (любой давности). null — ещё не загружали. */
    fun peek(ctx: Context): List<GarageCar>? = MemoryCache.garage ?: fromDisk(ctx)

    /** Список гаража: свежий (моложе 10 минут) — из памяти или с диска, иначе запрос к ABCP. */
    suspend fun load(ctx: Context): List<GarageCar> = mutex.withLock {
        val app = ctx.applicationContext
        val session = SessionManager(app)
        val login = session.login()
        val now = System.currentTimeMillis()
        MemoryCache.garage?.let { if (now - loadedAt in 0 until TTL_MS) return@withLock it }
        // Приложение только что запущено: копия на диске ещё свежая — в сеть не идём
        if (now - prefs(app).getLong(AT, 0) in 0 until TTL_MS) fromDisk(app)?.let { return@withLock it }

        val body = withContext(Dispatchers.IO) { AbcpGarageRepository().loadGarageJson(login, session.passMd5()) }
        val cars = parseGarage(body)
        // Пока шёл запрос, вышли из аккаунта — гараж прежнего клиента не сохраняем
        if (login.isBlank() || SessionManager(app).login() != login) return@withLock cars
        val at = System.currentTimeMillis()
        MemoryCache.garage = cars
        loadedAt = at
        prefs(app).edit().putString(JSON, body).putLong(AT, at).putString(OWNER, login).apply()
        cars
    }

    /** Гараж изменили (добавили или удалили машину, «обновить» вручную): следующий [load] пойдёт в ABCP. */
    fun invalidate(ctx: Context) {
        loadedAt = 0L
        prefs(ctx).edit().putLong(AT, 0L).apply()
    }

    /** Выход из аккаунта: ни в памяти, ни на диске гаража прежнего клиента не остаётся. */
    fun clear(ctx: Context) {
        loadedAt = 0L
        MemoryCache.garage = null
        prefs(ctx).edit().clear().apply()
    }

    private fun fromDisk(ctx: Context): List<GarageCar>? {
        val p = prefs(ctx)
        val json = p.getString(JSON, null) ?: return null
        val owner = p.getString(OWNER, null)
        if (owner.isNullOrBlank() || owner != SessionManager(ctx.applicationContext).login()) return null
        val cars = runCatching { parseGarage(json) }.getOrNull() ?: return null
        MemoryCache.garage = cars
        loadedAt = p.getLong(AT, 0L)
        return cars
    }
}
