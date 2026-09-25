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
import java.util.concurrent.TimeUnit

/** Статус одной позиции заказа. key — стабильный ключ позиции между проверками. */
data class PositionStatus(
    val key: String,
    val order: String,
    val title: String,
    val status: String
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
                status = status
            )
        }
    }
}

/** Изменившиеся позиции. Новые (которых не было в прошлый раз) не считаются — их заказал сам человек. */
fun changedStatuses(previous: Map<String, String>, now: List<PositionStatus>): List<PositionStatus> =
    now.filter { p -> previous[p.key].let { it != null && it != p.status } }

/** Запись ленты: когда, какой заказ, какая позиция, новый статус. */
data class FeedItem(val time: Long, val order: String, val title: String, val status: String)

// ---------------- Фоновая проверка ----------------

object OrderStatusWatch {
    private const val WORK = "order-status-watch"
    private const val PREFS = "order_status_snapshot"
    private const val KEY = "snapshot"
    const val CHANNEL = "order_status"

    /** Раз в 20 минут при наличии сети. KEEP — не перезапускать, если уже запланировано. */
    fun schedule(ctx: Context) {
        val req = PeriodicWorkRequestBuilder<OrderStatusWorker>(20, TimeUnit.MINUTES)
            .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
            .build()
        WorkManager.getInstance(ctx).enqueueUniquePeriodicWork(WORK, ExistingPeriodicWorkPolicy.KEEP, req)
    }

    /** Пришли push с сервера — своя проверка больше не нужна (снимок статусов оставляем). */
    fun stopPolling(ctx: Context) {
        WorkManager.getInstance(ctx).cancelUniqueWork(WORK)
    }

    /** При выходе из аккаунта: не следить и забыть статусы прошлого клиента. */
    fun stop(ctx: Context) {
        WorkManager.getInstance(ctx).cancelUniqueWork(WORK)
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

    override suspend fun doWork(): Result {
        val session = SessionManager(applicationContext)
        if (!session.isLoggedIn()) return Result.success()

        val resp = runCatching {
            ApiClient.create().ordersWithPositions(session.login(), session.passMd5())
        }.getOrNull() ?: return Result.retry()
        if (!resp.isSuccessful) return Result.success() // неверный пароль и т.п. — повтор не поможет
        val body = resp.body() ?: return Result.success()

        val now = collectPositionStatuses(body)
        val previous = OrderStatusWatch.load(applicationContext)
        OrderStatusWatch.save(applicationContext, now)
        // Первый запуск — только запоминаем, чтобы не завалить уведомлениями по старым заказам
        if (previous == null) return Result.success()

        changedStatuses(previous, now).groupBy { it.order }.forEach { (order, list) ->
            OrderStatusWatch.addToFeed(applicationContext, order, list)
            notify(order, list)
        }
        return Result.success()
    }
    private fun notify(order: String, changed: List<PositionStatus>) = showOrderNotification(applicationContext, order, changed)
}

/** Уведомление о заказе: из фоновой проверки или из push. Нажатие — открыть заказ. */
fun showOrderNotification(
    ctx: Context, order: String?, changed: List<PositionStatus>,
    customTitle: String? = null, customText: String? = null,
    /** Адрес магазина — кнопка «Маршрут» в уведомлении «готово к выдаче» */
    routeAddress: String? = null
) {
    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
        ContextCompat.checkSelfPermission(ctx, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
    ) return
    OrderStatusWatch.ensureChannel(ctx)

    val intent = if (order != null)
        Intent(ctx, OrderDetailsActivity::class.java).putExtra(OrderDetailsActivity.EXTRA_ORDER_NUMBER, order)
    else Intent(ctx, com.example.myapplication.MainActivity::class.java)
    val open = PendingIntent.getActivity(
        ctx, (order ?: "push").hashCode(),
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
fun showPromoNotification(ctx: Context, title: String, text: String) {
    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
        ContextCompat.checkSelfPermission(ctx, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
    ) return
    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
        val ch = NotificationChannel(PROMO_CHANNEL, "Акции и новости", NotificationManager.IMPORTANCE_DEFAULT)
        ch.description = "Скидки и новости магазина"
        ctx.getSystemService(NotificationManager::class.java).createNotificationChannel(ch)
    }
    val open = PendingIntent.getActivity(
        ctx, 7001,
        Intent(ctx, com.example.myapplication.MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
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

/** Ответ менеджера в чате — отдельный канал «Чат с магазином», нажатие открывает чат. */
fun showChatNotification(ctx: Context, title: String, text: String) {
    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
        ContextCompat.checkSelfPermission(ctx, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
    ) return
    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
        val ch = NotificationChannel("chat", "Чат с магазином", NotificationManager.IMPORTANCE_HIGH)
        ch.description = "Ответы менеджера"
        ctx.getSystemService(NotificationManager::class.java).createNotificationChannel(ch)
    }
    val open = PendingIntent.getActivity(
        ctx, 7002,
        Intent(ctx, com.example.myapplication.ChatActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
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
