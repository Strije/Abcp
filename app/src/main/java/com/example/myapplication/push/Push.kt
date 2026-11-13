package com.example.myapplication.push

import android.content.Context
import android.util.Log
import com.example.myapplication.SessionManager
import com.example.myapplication.abcp.OrderStatusWatch
import com.example.myapplication.abcp.PositionStatus
import com.example.myapplication.abcp.showOrderNotification
import com.example.myapplication.abcp.showPromoNotification
import com.example.myapplication.server.AppServer
import com.google.gson.Gson
import com.google.gson.reflect.TypeToken
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch
import ru.rustore.sdk.pushclient.RuStorePushClient
import ru.rustore.sdk.pushclient.messaging.model.RemoteMessage
import ru.rustore.sdk.pushclient.messaging.service.RuStoreMessagingService

/**
 * Push о статусах заказов через RuStore: токен устройства → наш сервер, сервер следит за заказами
 * в ABCP и присылает push, как только статус меняется. Push нет — проверка из приложения раз в 20 минут
 * (OrderStatusWatch); есть — всё равно раз в 30 минут и при открытии: push может не дойти (Xiaomi
 * усыпляет RuStore). Одно изменение показываем один раз — OrderStatusWatch.markShown.
 */
object Push {
    private const val TAG = "PUSH"
    private const val PREFS = "push"
    private const val TOKEN = "token"
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    /**
     * После входа: токены RuStore и Google — серверу. Получилось — фоновая проверка из приложения не нужна.
     * Нет RuStore, но есть Google — устройство регистрируется только Google (ключ «fcm:…»).
     */
    fun register(ctx: Context) {
        val app = ctx.applicationContext
        val noRuStore = {
            Fcm.token { f -> if (f != null) scope.launch { send(app, "fcm:$f", null) } else OrderStatusWatch.schedule(app) }
        }
        runCatching {
            RuStorePushClient.getToken()
                .addOnSuccessListener { token -> Fcm.token { f -> scope.launch { send(app, token, f) } } }
                .addOnFailureListener { e ->
                    Log.i(TAG, "push RuStore недоступен: ${e.message}")
                    noRuStore()
                }
        }.onFailure { noRuStore() }
    }

    internal suspend fun send(ctx: Context, token: String, fcm: String?) {
        if (!SessionManager(ctx).isLoggedIn()) return
        runCatching { AppServer(ctx).pushToken(token, fcm) }
            .onSuccess { enabled ->
                ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().putString(TOKEN, token).apply()
                // Сервер шлёт push — своя проверка реже, как страховка; дублей нет: общие отметки показанного
                OrderStatusWatch.schedule(ctx, pushWorks = enabled)
            }
            .onFailure { OrderStatusWatch.schedule(ctx) }
    }

    /** При выходе: сервер забывает устройство, чтобы уведомления прошлого клиента сюда не шли. */
    fun unregister(ctx: Context) {
        val p = ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        val token = p.getString(TOKEN, null) ?: return
        p.edit().clear().apply()
        val app = ctx.applicationContext
        scope.launch { runCatching { AppServer(app).pushTokenDelete(token) } }
    }

    /** Push от сервера: показать уведомление и записать в ленту, как при обычной проверке. */
    internal fun onMessage(ctx: Context, data: Map<String, String>) = onMessage(ctx, data, show = true)

    /** Замер доставки: сервер кладёт время отправки; via — каким каналом пришло (fcm / rustore). */
    internal fun measure(data: Map<String, String>, via: String, withNotification: Boolean = false) {
        data["sent_at"]?.toLongOrNull()?.let { sent ->
            com.example.myapplication.Analytics.event("push_received", mapOf(
                "type" to data["type"], "delay_s" to ((System.currentTimeMillis() - sent) / 1000).coerceAtLeast(0),
                "via" to via, "with_notification" to withNotification
            ))
        }
    }

    /** Одно сообщение приходит двумя каналами (Google и RuStore) с одним mid — второе пропускаем. */
    @Synchronized
    private fun firstTime(ctx: Context, mid: String): Boolean {
        val p = ctx.getSharedPreferences("push_mids", Context.MODE_PRIVATE)
        val seen = p.getString("mids", "").orEmpty().split(',').filter { it.isNotBlank() }
        if (mid in seen) return false
        p.edit().putString("mids", (seen.takeLast(49) + mid).joinToString(",")).apply()
        return true
    }

    /** show=false — уведомление уже показал RuStore (push с готовым уведомлением): только запись в ленту. */
    internal fun onMessage(ctx: Context, data: Map<String, String>, show: Boolean) {
        data["mid"]?.let { if (!firstTime(ctx, it)) return }
        val order = data["order"]
        val items = data["items"]?.let {
            runCatching { Gson().fromJson<List<List<String?>?>>(it, object : TypeToken<List<List<String?>?>>() {}.type) }.getOrNull()
        }
        // Вышли из аккаунта, а push прежнего клиента ещё пришёл — заказы и чат не показываем
        val loggedIn = SessionManager(ctx).isLoggedIn()
        if (data["type"] == "chat") {
            if (!loggedIn) return
            com.example.myapplication.abcp.showChatNotification(ctx, data["title"] ?: "Менеджер ответил", data["body"].orEmpty(), data["link"])
            return
        }
        if (data["type"] == "promo") {
            showPromoNotification(ctx, data["title"] ?: "Автодруг92", data["body"].orEmpty(), data["link"])
            return
        }
        if (!loggedIn) return
        // Строка короче пары «название, статус» — пропускаем (номера остальных позиций не сдвигаются)
        val all = if (order == null) emptyList<PositionStatus>() else items.orEmpty().mapIndexedNotNull { i, row ->
            val title = row?.getOrNull(0) ?: return@mapIndexedNotNull null
            val status = row?.getOrNull(1) ?: return@mapIndexedNotNull null
            PositionStatus("$order#$i", order, title, status)
        }
        if (order != null && all.isNotEmpty()) {
            // Своя проверка уже показала это изменение (push опоздал) — второй раз не показываем
            val changed = OrderStatusWatch.markShown(ctx, all)
            if (changed.isEmpty()) return
            OrderStatusWatch.addToFeed(ctx, order, changed)
            if (!show) return
            // «Готово к выдаче», «ждёт оплаты», «задерживается» — сервер присылает свой текст (и адрес для маршрута)
            showOrderNotification(
                ctx, order, changed,
                customTitle = data["title"].takeIf { data["kind"] != null },
                customText = data["body"].takeIf { data["kind"] != null },
                routeAddress = data["address"]?.takeIf { data["kind"] == "ready" && it.isNotBlank() },
                link = data["link"]
            )
        } else if (show) {
            showOrderNotification(ctx, order, emptyList(), data["title"] ?: "Автодруг92", data["body"].orEmpty(), link = data["link"])
        }
    }
}

class PushService : RuStoreMessagingService() {
    /** Приложение на экране: уведомление с готовым текстом RuStore в этом случае не показывает. */
    private fun isForeground(): Boolean {
        val info = android.app.ActivityManager.RunningAppProcessInfo()
        android.app.ActivityManager.getMyMemoryState(info)
        return info.importance <= android.app.ActivityManager.RunningAppProcessInfo.IMPORTANCE_FOREGROUND
    }

    override fun onNewToken(token: String) {
        Push.register(applicationContext)   // вместе с токеном Google
    }

    override fun onMessageReceived(message: RemoteMessage) {
        // Замер доставки: сервер кладёт время отправки. Когда приложение закрыто, уведомление показывает сам
        // RuStore и сюда мы можем не попасть — тогда замер не придёт (в отчёте это нижняя граница доставки)
        Push.measure(message.data, "rustore", withNotification = message.notification != null)
        // Push с готовым уведомлением: когда приложение свёрнуто, его показывает сам RuStore (быстрее — не ждём, пока
        // система разбудит приложение), второе не рисуем. Когда приложение открыто, RuStore не показывает ничего и
        // отдаёт сообщение сюда — тогда рисуем сами (телефон 10.11: проверочный push при открытом экране «пропадал»)
        if (message.notification != null) {
            val shown = !isForeground()
            if (message.data["type"] == "order_status") Push.onMessage(applicationContext, message.data, show = !shown)
            else if (!shown) Push.onMessage(applicationContext, message.data)
            return
        }
        Push.onMessage(applicationContext, message.data)
    }
}
