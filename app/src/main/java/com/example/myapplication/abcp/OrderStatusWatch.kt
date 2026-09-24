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

        changedStatuses(previous, now).groupBy { it.order }.forEach { (order, list) -> notify(order, list) }
        return Result.success()
    }

    private fun notify(order: String, changed: List<PositionStatus>) {
        val ctx = applicationContext
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
            ContextCompat.checkSelfPermission(ctx, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
        ) return
        OrderStatusWatch.ensureChannel(ctx)

        val open = PendingIntent.getActivity(
            ctx, order.hashCode(),
            Intent(ctx, OrderDetailsActivity::class.java)
                .putExtra(OrderDetailsActivity.EXTRA_ORDER_NUMBER, order)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )
        val first = changed.first()
        val title = if (changed.size == 1) "Заказ № $order: ${first.status}" else "Заказ № $order: изменились статусы"
        val lines = changed.joinToString("\n") { "${it.title} — ${it.status}" }

        val n = NotificationCompat.Builder(ctx, OrderStatusWatch.CHANNEL)
            .setSmallIcon(R.mipmap.ic_launcher)
            .setContentTitle(title)
            .setContentText(if (changed.size == 1) first.title else "${changed.size} поз.")
            .setStyle(NotificationCompat.BigTextStyle().bigText(lines))
            .setContentIntent(open)
            .setAutoCancel(true)
            .build()
        runCatching { NotificationManagerCompat.from(ctx).notify(order.hashCode(), n) }
    }
}
