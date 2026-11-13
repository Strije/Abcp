package com.example.myapplication

import android.content.Context
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import com.google.gson.JsonArray
import com.google.gson.JsonElement
import com.google.gson.JsonObject
import com.google.gson.JsonParser
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch
import java.util.concurrent.atomic.AtomicBoolean

/** Настройки как они пришли с сервера; значения по умолчанию — то, с чем приложение живёт без сервера. */
internal data class AppConfigData(
    val minVersion: Int = 0,
    val updateText: String = "",
    val noticeText: String = "",
    val noticeLink: String = "",
    val hoursText: String = DEFAULT_HOURS_TEXT,
    val schedule: Schedule = DEFAULT_SCHEDULE,
    val stores: List<Store> = DEFAULT_STORES,
    val city: String = DEFAULT_CITY,
    val chatUrl: String = DEFAULT_CHAT_URL,
    val maxUrl: String = DEFAULT_MAX_URL,
    val flags: Map<String, Boolean> = DEFAULT_FLAGS
)

private const val DEFAULT_HOURS_TEXT = "Пн–Пт 9:00–19:00 · Сб–Вс 9:00–17:00"
private const val DEFAULT_CITY = "Севастополь"
/** Онлайн-чат открытой линии Битрикс24 (тот же, что виджет на сайте) — сообщения идут в CRM */
private const val DEFAULT_CHAT_URL = "https://bitrix.freno.ru/online/avtodrug"
/** Запасной канал — MAX (ссылка с сайта) */
private const val DEFAULT_MAX_URL = "https://max.ru/u/f9LHodD0cOLBKIaHZWQOl7LRIia77YiAfQxW7VJqyO8pcPEmbXQwXlarPNY"

/** abcpOfficeId — офис в ABCP (cp/offices): «Первый офис» = Хрусталёва, «Второй офис» = Окт. Революции */
private val DEFAULT_STORES = listOf(
    Store("ул. Хрусталёва, 111", "+79782195625", "+7 (978) 219-56-25", "27993", "Хрусталёва, 111"),
    Store("пр. Октябрьской Революции, 20", "+79785407888", "+7 (978) 540-78-88", "60602", "Октябрьской Революции, 20")
)

private val WEEKDAY = DayHours(9 * 60, 19 * 60)
private val WEEKEND = DayHours(9 * 60, 17 * 60)
internal val DEFAULT_SCHEDULE = Schedule(
    week = listOf(WEEKDAY, WEEKDAY, WEEKDAY, WEEKDAY, WEEKDAY, WEEKEND, WEEKEND),
    closed = emptySet(),
    arrivals = listOf(15 * 60)
)

private val DEFAULT_FLAGS = mapOf("brand_autopick" to true, "vpn_warning" to true, "guest_prices" to true)

/**
 * Настройки, которые меняются без выпуска версии: контакты и часы магазинов, объявление на главной,
 * минимальная версия, переключатели функций. Сервер отдаёт их в GET /v1/app-config; нет сети или метода —
 * живём на последней удачной копии, а до неё — на значениях по умолчанию.
 */
object AppConfig {
    private const val PREFS = "app_config" // не abcp_session: выход из аккаунта настройки не сбрасывает
    private const val KEY = "json"
    private const val EVERY = 30 * 60_000L

    private var app: Context? = null
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val running = AtomicBoolean(false)
    @Volatile private var lastTry = 0L

    // Состояние Compose: экраны, которые читают настройки, перерисуются сами, когда придёт свежая копия
    private var data by mutableStateOf(AppConfigData())

    fun init(ctx: Context) {
        val c = ctx.applicationContext
        app = c
        runCatching { c.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getString(KEY, null) }.getOrNull()
            ?.let { parseAppConfig(it) }?.let { data = it }
        refresh()
    }

    /** Обновить в фоне, не чаще раза в 30 минут. Любая ошибка — молча остаёмся на прежних настройках. */
    fun refresh() {
        val ctx = app ?: return
        val now = System.currentTimeMillis()
        if (lastTry != 0L && now - lastTry < EVERY) return
        if (!running.compareAndSet(false, true)) return
        lastTry = now
        scope.launch {
            try {
                val text = com.example.myapplication.server.AppServer(ctx).appConfig()
                val fresh = parseAppConfig(text) ?: return@launch
                data = fresh
                ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().putString(KEY, text).apply()
            } catch (_: Exception) {
                // нет сети, 404 (метода ещё нет), мусор в ответе — не страшно
            } finally {
                running.set(false)
            }
        }
    }

    /** Переключатель функции; о незнакомом имени сервер не знает — берём default. */
    fun flag(name: String, default: Boolean = true): Boolean = data.flags[name] ?: default

    /** Версии ниже этой показываем просьбу обновиться */
    val minVersion: Int get() = data.minVersion
    val updateText: String get() = data.updateText
    /** Объявление на главной (пусто — нет) и ссылка по нажатию (пусто — без перехода) */
    val noticeText: String get() = data.noticeText
    val noticeLink: String get() = data.noticeLink
    val hoursText: String get() = data.hoursText
    val stores: List<Store> get() = data.stores
    val city: String get() = data.city
    val chatUrl: String get() = data.chatUrl
    val maxUrl: String get() = data.maxUrl
    /** Часы работы, закрытые дни и время привоза — для расчёта, когда можно забрать заказ ([pickupEstimate]) */
    val schedule: Schedule get() = data.schedule

    /** Адрес магазина по номеру офиса ABCP; null — такого офиса в настройках нет. */
    fun officeName(officeId: String): String? =
        data.stores.firstOrNull { it.abcpOfficeId.isNotBlank() && it.abcpOfficeId == officeId }?.address
}

// ---------- разбор ответа сервера ----------

private fun JsonElement?.cfgString(): String? =
    this?.takeIf { it.isJsonPrimitive && it.asJsonPrimitive.isString }?.asString

private fun JsonObject.cfgObj(key: String): JsonObject? = get(key)?.takeIf { it.isJsonObject }?.asJsonObject
private fun JsonObject.cfgArr(key: String): JsonArray? = get(key)?.takeIf { it.isJsonArray }?.asJsonArray

/** «09:00» → минуты от полуночи; null — не время. */
internal fun parseClock(s: String?): Int? {
    val m = Regex("^(\\d{1,2}):(\\d{2})$").find(s?.trim() ?: return null) ?: return null
    val h = m.groupValues[1].toInt()
    val min = m.groupValues[2].toInt()
    return if (h <= 24 && min <= 59 && h * 60 + min <= 24 * 60) h * 60 + min else null
}

/** Неделя целиком или никак: семь дней, каждый — null (выходной) или [«с», «до»]. */
private fun parseWeek(arr: JsonArray?): List<DayHours?>? {
    if (arr == null || arr.size() != 7) return null
    return arr.map { e ->
        if (e.isJsonNull) return@map null
        if (!e.isJsonArray || e.asJsonArray.size() != 2) return null
        val open = parseClock(e.asJsonArray[0].cfgString()) ?: return null
        val close = parseClock(e.asJsonArray[1].cfgString()) ?: return null
        if (close <= open) return null
        DayHours(open, close)
    }
}

private fun parseStores(arr: JsonArray?): List<Store>? = arr?.mapNotNull { e ->
    val o = e.takeIf { it.isJsonObject }?.asJsonObject ?: return@mapNotNull null
    val address = o.get("address").cfgString()?.trim()?.takeIf { it.isNotEmpty() } ?: return@mapNotNull null
    val phone = o.get("phone").cfgString()?.trim()?.takeIf { it.isNotEmpty() } ?: return@mapNotNull null
    Store(
        address = address,
        phone = phone,
        phoneDisplay = o.get("phone_display").cfgString()?.takeIf { it.isNotBlank() } ?: phone,
        abcpOfficeId = o.get("office_id").cfgString().orEmpty(),
        name = o.get("name").cfgString()?.takeIf { it.isNotBlank() } ?: address
    )
}?.takeIf { it.isNotEmpty() }

/**
 * Ответ /v1/app-config → настройки. Каждое поле разбирается отдельно: нет поля или оно не того вида —
 * для него остаётся значение по умолчанию, остальные применяются. null — это вообще не объект JSON
 * (страница ошибки, обрывок): такой ответ не должен затереть прежние настройки.
 */
internal fun parseAppConfig(json: String): AppConfigData? {
    val root = runCatching { JsonParser.parseString(json) }.getOrNull()?.takeIf { it.isJsonObject }?.asJsonObject ?: return null
    val def = AppConfigData()
    val notice = root.cfgObj("notice")
    val hours = root.cfgObj("hours")
    val closed = hours?.cfgArr("closed")
        ?.mapNotNull { it.cfgString()?.trim()?.takeIf { d -> Regex("^\\d{4}-\\d{2}-\\d{2}$").matches(d) } }?.toSet()
    // Пустой список привозов — тоже настройка («привозов нет»); а список из одного мусора — ошибка, берём обычный
    val arrivalsRaw = root.cfgArr("arrivals")
    val arrivals: List<Int>? = if (arrivalsRaw == null) null else {
        val times = arrivalsRaw.mapNotNull { parseClock(it.cfgString()) }.sorted()
        if (times.isNotEmpty() || arrivalsRaw.size() == 0) times else null
    }
    val flags = root.cfgObj("flags")?.entrySet()
        ?.filter { it.value.isJsonPrimitive && it.value.asJsonPrimitive.isBoolean }
        ?.associate { it.key to it.value.asBoolean }
    fun url(key: String) = root.get(key).cfgString()?.trim()?.takeIf { it.startsWith("https://") }
    return AppConfigData(
        minVersion = root.get("min_version")?.takeIf { it.isJsonPrimitive && it.asJsonPrimitive.isNumber }
            ?.let { runCatching { it.asInt }.getOrNull() } ?: def.minVersion,
        updateText = root.get("update_text").cfgString()?.trim() ?: def.updateText,
        noticeText = notice?.get("text").cfgString()?.trim() ?: def.noticeText,
        noticeLink = notice?.get("link").cfgString()?.trim() ?: def.noticeLink,
        hoursText = hours?.get("text").cfgString()?.takeIf { it.isNotBlank() } ?: def.hoursText,
        schedule = Schedule(
            week = parseWeek(hours?.cfgArr("week")) ?: def.schedule.week,
            closed = closed ?: def.schedule.closed,
            arrivals = arrivals ?: def.schedule.arrivals
        ),
        stores = parseStores(root.cfgArr("stores")) ?: def.stores,
        city = root.get("city").cfgString()?.trim()?.takeIf { it.isNotEmpty() } ?: def.city,
        chatUrl = url("chat_url") ?: def.chatUrl,
        maxUrl = url("max_url") ?: def.maxUrl,
        flags = def.flags + flags.orEmpty()
    )
}
