package com.example.myapplication.abcp

import android.Manifest
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat
import androidx.work.*
import com.example.myapplication.R
import com.example.myapplication.SessionManager
import com.google.gson.Gson
import com.google.gson.JsonElement
import com.google.gson.reflect.TypeToken
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import java.util.concurrent.TimeUnit

/** Статус одной позиции заказа. key — стабильный ключ позиции между проверками. */
data class PositionStatus(
    val key: String,
    val order: String,
    val title: String,
    val status: String,
    /** «Бренд номер» — так позицию называет сервер в push; по нему push и своя проверка узнают одно изменение */
    val code: String = title
)

/** Позиции всех заказов из ответа orders?format=p (items и positions — массив или объект). */
fun collectPositionStatuses(root: JsonElement): List<PositionStatus> {
    val ordersEl = if (root.isJsonObject && root.asJsonObject.has("items")) root.asJsonObject.get("items") else root
    return items(ordersEl).flatMap { oe ->
        val o = oe.takeIf { it.isJsonObject }?.asJsonObject ?: return@flatMap emptyList()
        val number = o.get("number")?.takeIf { it.isJsonPrimitive }?.asString ?: return@flatMap emptyList()
        val positions = o.get("positions")?.let { items(it) }.orEmpty()
        positions.mapIndexedNotNull { i, pe ->
            val p = pe.takeIf { it.isJsonObject }?.asJsonObject ?: return@mapIndexedNotNull null
            fun s(k: String) = p.get(k)?.takeIf { it.isJsonPrimitive }?.asString?.trim().orEmpty()
            val status = s("status").ifBlank { return@mapIndexedNotNull null }
            val brand = s("brand"); val num = s("number")
            PositionStatus(
                key = "$number|$brand|$num|$i",
                order = number,
                title = s("description").ifBlank { "$brand $num".trim() },
                status = status,
                code = "$brand $num".trim().ifBlank { s("description") }
            )
        }
    }
}

/** Изменившиеся позиции. Новые (которых не было в прошлый раз) не считаются — их заказал сам человек. */
fun changedStatuses(previous: Map<String, String>, now: List<PositionStatus>): List<PositionStatus> =
    now.filter { p -> previous[p.key].let { it != null && it != p.status } }

/** Одно и то же изменение для push и для своей проверки: заказ + «бренд номер» + новый статус. */
fun changeKey(p: PositionStatus): String =
    listOf(p.order, p.code, p.status).joinToString("|") { it.trim().lowercase().replace(Regex("\\s+"), " ") }

/** Сколько помним показанное изменение: дольше — статус мог честно вернуться (В пути → Отказ → В пути). */
const val SHOWN_TTL_MS = 14L * 24 * 3600 * 1000
private const val SHOWN_MAX = 500

/**
 * Отбирает изменения, которые ещё не показывали (ни push, ни своя проверка), и отмечает их.
 * Возвращает (новые, обновлённые отметки ключ → время). Отметки старше [SHOWN_TTL_MS] забываются.
 */
fun takeUnshown(shown: Map<String, Long>, changed: List<PositionStatus>, now: Long): Pair<List<PositionStatus>, Map<String, Long>> {
    val marks = shown.filterValues { now - it < SHOWN_TTL_MS }.toMutableMap()
    val fresh = changed.filter { p ->
        val k = changeKey(p)
        if (k in marks) false else { marks[k] = now; true }
    }
    val kept = if (marks.size <= SHOWN_MAX) marks
        else marks.entries.sortedByDescending { it.value }.take(SHOWN_MAX).associate { it.key to it.value }
    return fresh to kept
}

/** Запись ленты: когда, какой заказ, какая позиция, новый статус. */
data class FeedItem(val time: Long, val order: String, val title: String, val status: String)

// ---------------- Фоновая проверка ----------------

object OrderStatusWatch {
    private const val WORK = "order-status-watch"
    private const val PREFS = "order_status_snapshot"
    private const val KEY = "snapshot"
    private const val SHOWN = "shown"
    private const val LAST_CHECK = "last_check"
    /** Снимок сделан этой версией (раньше при работающем push снимок не обновлялся и мог устареть на недели) */
    private const val FRESH = "snapshot_v2"
    const val CHANNEL = "order_status"
    /** Не чаще раза в 5 минут — бережём ABCP (открыли-закрыли приложение десять раз подряд — один запрос) */
    const val MIN_INTERVAL_MS = 5L * 60 * 1000
    private const val NEWS_ID = 7003

    private val mutex = Mutex()   // один запрос orders?format=p за раз
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    /**
     * Своя проверка по расписанию. Push работает — раз в 30 минут, страховка на случай, если push не дошёл
     * (Xiaomi усыпляет RuStore); push нет — раз в 20 минут. UPDATE — поменять период у уже запланированной.
     */
    fun schedule(ctx: Context, pushWorks: Boolean = false) {
        val req = PeriodicWorkRequestBuilder<OrderStatusWorker>(if (pushWorks) 30L else 20L, TimeUnit.MINUTES)
            .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
            .build()
        WorkManager.getInstance(ctx).enqueueUniquePeriodicWork(WORK, ExistingPeriodicWorkPolicy.UPDATE, req)
    }

    /** Отмечает изменения как показанные; возвращает те, что ещё не показывали. Общий для push и проверки. */
    @Synchronized
    fun markShown(ctx: Context, changed: List<PositionStatus>): List<PositionStatus> {
        val p = ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        val shown = runCatching {
            Gson().fromJson<Map<String, Long>>(p.getString(SHOWN, null) ?: "{}", object : TypeToken<Map<String, Long>>() {}.type)
        }.getOrNull().orEmpty()
        val (fresh, kept) = takeUnshown(shown, changed, System.currentTimeMillis())
        p.edit().putString(SHOWN, Gson().toJson(kept)).apply()
        return fresh
    }

    /**
     * Сверка с ABCP: изменения с прошлой проверки, которых ещё не показывали (уже записаны в ленту).
     * null — не проверяли (рано, не вошли, нет сети). Первый раз — только запоминаем снимок.
     */
    suspend fun check(ctx: Context): Map<String, List<PositionStatus>>? = mutex.withLock {
        val app = ctx.applicationContext
        val session = SessionManager(app)
        if (!session.isLoggedIn()) return@withLock null
        val p = app.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        val t = System.currentTimeMillis()
        if (t - p.getLong(LAST_CHECK, 0) in 0 until MIN_INTERVAL_MS) return@withLock null
        p.edit().putLong(LAST_CHECK, t).apply()  // и при ошибке: не долбить ABCP повторами

        val resp = runCatching { ApiClient.create().ordersWithPositions(session.login(), session.passMd5()) }.getOrNull()
        val body = resp?.takeIf { it.isSuccessful }?.body() ?: return@withLock null
        val now = collectPositionStatuses(body)
        val previous = load(app).takeIf { p.getBoolean(FRESH, false) }
        save(app, now)
        p.edit().putBoolean(FRESH, true).apply()
        // Первый раз — только запоминаем, чтобы не завалить уведомлениями по старым заказам
        if (previous == null) return@withLock emptyMap()

        val fresh = markShown(app, changedStatuses(previous, now))
        fresh.groupBy { it.order }.onEach { (order, list) -> addToFeed(app, order, list) }
    }

    /** Открыли приложение: тихо подтянуть, что пропустили (push не дошёл), и показать одно уведомление. */
    fun catchUpOnOpen(ctx: Context, onDone: (Int) -> Unit = {}) {
        val app = ctx.applicationContext
        scope.launch {
            val news = runCatching { check(app) }.getOrNull()
            if (!news.isNullOrEmpty()) showNewsNotification(app, news.values.flatten())
            onDone(news?.values?.sumOf { it.size } ?: 0)
        }
    }

    /** При выходе из аккаунта: не следить и забыть статусы прошлого клиента. */
    fun stop(ctx: Context) {
        WorkManager.getInstance(ctx).cancelUniqueWork(WORK)
        // Новости прошлого клиента новому не нужны
        runCatching { NotificationManagerCompat.from(ctx).cancel(NEWS_ID) }
        ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().clear().apply()
    }

    internal fun load(ctx: Context): Map<String, String>? {
        val json = ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getString(KEY, null) ?: return null
        return runCatching {
            Gson().fromJson<Map<String, String>>(json, object : TypeToken<Map<String, String>>() {}.type)
        }.getOrNull()
    }

    internal fun save(ctx: Context, now: List<PositionStatus>) {
        val json = Gson().toJson(now.associate { it.key to it.status })
        ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().putString(KEY, json).apply()
    }

    // ---- Лента уведомлений: история изменений, чтобы смахнутое уведомление не терялось ----

    private const val FEED = "feed"
    private const val SEEN = "feed_seen"
    private const val FEED_MAX = 100

    fun feed(ctx: Context): List<FeedItem> {
        val json = ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getString(FEED, null) ?: return emptyList()
        return runCatching {
            Gson().fromJson<List<FeedItem>>(json, object : TypeToken<List<FeedItem>>() {}.type)
        }.getOrNull().orEmpty()
    }

    /** «Есть новости по заказам» — одно на все изменения, нажатие открывает ленту. */
    private fun showNewsNotification(ctx: Context, changed: List<PositionStatus>) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
            ContextCompat.checkSelfPermission(ctx, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
        ) return
        ensureChannel(ctx)
        val open = PendingIntent.getActivity(
            ctx, NEWS_ID,
            Intent(ctx, NotificationsActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )
        val lines = changed.take(8).joinToString("\n") { "№ ${it.order}: ${it.title} — ${it.status}" } +
            (if (changed.size > 8) "\n…и ещё ${changed.size - 8}" else "")
        val n = NotificationCompat.Builder(ctx, CHANNEL)
            .setSmallIcon(R.mipmap.ic_launcher)
            .setContentTitle("Есть новости по заказам")
            .setContentText(if (changed.size == 1) "№ ${changed[0].order}: ${changed[0].status}" else "Изменились статусы: ${changed.size} поз.")
            .setStyle(NotificationCompat.BigTextStyle().bigText(lines))
            .setContentIntent(open)
            .setAutoCancel(true)
            .build()
        runCatching { NotificationManagerCompat.from(ctx).notify(NEWS_ID, n) }
    }

    internal fun addToFeed(ctx: Context, order: String, changed: List<PositionStatus>) {
        val now = System.currentTimeMillis()
        val added = changed.map { FeedItem(now, order, it.title, it.status) }
        val all = (added + feed(ctx)).take(FEED_MAX)
        ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().putString(FEED, Gson().toJson(all)).apply()
    }

    fun unreadCount(ctx: Context): Int {
        val seen = ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getLong(SEEN, 0)
        return feed(ctx).count { it.time > seen }
    }

    fun markSeen(ctx: Context) {
        ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().putLong(SEEN, System.currentTimeMillis()).apply()
    }

    fun ensureChannel(ctx: Context) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val ch = NotificationChannel(CHANNEL, "Статусы заказов", NotificationManager.IMPORTANCE_DEFAULT)
            ch.description = "Когда меняется статус позиции в заказе"
            ctx.getSystemService(NotificationManager::class.java).createNotificationChannel(ch)
        }
    }
}

class OrderStatusWorker(ctx: Context, params: WorkerParameters) : CoroutineWorker(ctx, params) {

    // Без retry: не дошли до ABCP — следующая проверка по расписанию, лишних запросов не надо
    override suspend fun doWork(): Result {
        OrderStatusWatch.check(applicationContext)?.forEach { (order, list) -> notify(order, list) }
        return Result.success()
    }
    private fun notify(order: String, changed: List<PositionStatus>) = showOrderNotification(applicationContext, order, changed)
}

/** Уведомление о заказе: из фоновой проверки или из push. Нажатие — открыть заказ. */
fun showOrderNotification(
    ctx: Context, order: String?, changed: List<PositionStatus>,
    customTitle: String? = null, customText: String? = null,
    /** Адрес магазина — кнопка «Маршрут» в уведомлении «готово к выдаче» */
    routeAddress: String? = null,
    /** Ссылка из push (avtodrug92://… или https://) — нажатие открывает её вместо заказа */
    link: String? = null
) {
    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
        ContextCompat.checkSelfPermission(ctx, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
    ) return
    OrderStatusWatch.ensureChannel(ctx)

    val intent = linkIntent(ctx, link) ?: if (order != null)
        Intent(ctx, OrderDetailsActivity::class.java).putExtra(OrderDetailsActivity.EXTRA_ORDER_NUMBER, order)
    else Intent(ctx, com.example.myapplication.MainActivity::class.java)
    val open = PendingIntent.getActivity(
        ctx, (order ?: "push").hashCode() + (link?.hashCode() ?: 0),
        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
        PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
    )
    val first = changed.firstOrNull()
    val title = customTitle ?: when {
        first == null -> "Заказ № $order"
        changed.size == 1 || changed.all { it.status == first.status } -> "Заказ № $order: ${first.status}"
        else -> "Заказ № $order: изменились статусы"
    }
    val lines = customText ?: changed.joinToString("\n") { "${it.title} — ${it.status}" }

    val n = NotificationCompat.Builder(ctx, OrderStatusWatch.CHANNEL)
        .setSmallIcon(R.mipmap.ic_launcher)
        .setContentTitle(title)
        .setContentText(customText ?: if (changed.size == 1) first?.title else "${changed.size} поз.")
        .setStyle(NotificationCompat.BigTextStyle().bigText(lines))
        .setContentIntent(open)
        .setAutoCancel(true)
        .apply {
            if (routeAddress != null) {
                val route = PendingIntent.getActivity(
                    ctx, ("route" + order).hashCode(),
                    Intent(Intent.ACTION_VIEW, android.net.Uri.parse("geo:0,0?q=" + android.net.Uri.encode("Севастополь, $routeAddress")))
                        .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
                    PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
                )
                addAction(0, "Маршрут", route)
            }
        }
        .build()
    runCatching { NotificationManagerCompat.from(ctx).notify((order ?: "push").hashCode(), n) }
}

/** Акции из рассылки — отдельный канал «Акции и новости»: его можно выключить, не теряя статусы заказов. */
fun showPromoNotification(ctx: Context, title: String, text: String, link: String? = null) {
    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
        ContextCompat.checkSelfPermission(ctx, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
    ) return
    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
        val ch = NotificationChannel(PROMO_CHANNEL, "Акции и новости", NotificationManager.IMPORTANCE_DEFAULT)
        ch.description = "Скидки и новости магазина"
        ctx.getSystemService(NotificationManager::class.java).createNotificationChannel(ch)
    }
    // Своя ссылка у каждой рассылки: requestCode от ссылки, иначе новая рассылка подменит ссылку у прошлой
    val open = PendingIntent.getActivity(
        ctx, 7001 + (link?.hashCode() ?: 0),
        linkIntent(ctx, link) ?: Intent(ctx, com.example.myapplication.MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
        PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
    )
    val n = NotificationCompat.Builder(ctx, PROMO_CHANNEL)
        .setSmallIcon(R.mipmap.ic_launcher)
        .setContentTitle(title)
        .setContentText(text)
        .setStyle(NotificationCompat.BigTextStyle().bigText(text))
        .setContentIntent(open)
        .setAutoCancel(true)
        .build()
    runCatching { NotificationManagerCompat.from(ctx).notify(("promo" + text).hashCode(), n) }
}

private const val PROMO_CHANNEL = "promo"

/** Нажатие на уведомление со ссылкой из push; ссылка не наша / битая — null (откроется экран по умолчанию). */
private fun linkIntent(ctx: Context, link: String?): Intent? =
    link?.takeIf { com.example.myapplication.DeepLink.parse(it) != null }?.let { com.example.myapplication.DeepLink.intent(ctx, it) }

/** Каналы создаём при запуске: готовые уведомления от RuStore (рассылки, чат) приходят сразу в них. */
fun ensureNotificationChannels(ctx: Context) {
    if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return
    OrderStatusWatch.ensureChannel(ctx)
    val nm = ctx.getSystemService(NotificationManager::class.java)
    nm.createNotificationChannel(NotificationChannel(PROMO_CHANNEL, "Акции и новости", NotificationManager.IMPORTANCE_DEFAULT)
        .apply { description = "Скидки и новости магазина" })
    nm.createNotificationChannel(NotificationChannel("chat", "Чат с магазином", NotificationManager.IMPORTANCE_HIGH)
        .apply { description = "Ответы менеджера" })
}

/** Ответ менеджера в чате — отдельный канал «Чат с магазином», нажатие открывает чат. */
fun showChatNotification(ctx: Context, title: String, text: String, link: String? = null) {
    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
        ContextCompat.checkSelfPermission(ctx, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
    ) return
    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
        val ch = NotificationChannel("chat", "Чат с магазином", NotificationManager.IMPORTANCE_HIGH)
        ch.description = "Ответы менеджера"
        ctx.getSystemService(NotificationManager::class.java).createNotificationChannel(ch)
    }
    val open = PendingIntent.getActivity(
        ctx, 7002 + (link?.hashCode() ?: 0),
        linkIntent(ctx, link) ?: Intent(ctx, com.example.myapplication.ChatActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
        PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
    )
    val n = NotificationCompat.Builder(ctx, "chat")
        .setSmallIcon(R.mipmap.ic_launcher)
        .setContentTitle(title)
        .setContentText(text)
        .setStyle(NotificationCompat.BigTextStyle().bigText(text))
        .setContentIntent(open)
        .setAutoCancel(true)
        .build()
    runCatching { NotificationManagerCompat.from(ctx).notify(7002, n) }
}
