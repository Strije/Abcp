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
 * в ABCP и присылает push, как только статус меняется. Если RuStore (или другого дистрибьютора VK)
 * на телефоне нет — остаётся проверка из приложения раз в 20 минут (OrderStatusWatch).
 */
object Push {
    private const val TAG = "PUSH"
    private const val PREFS = "push"
    private const val TOKEN = "token"
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    /** После входа: получить токен и отдать серверу. Получилось — фоновая проверка из приложения не нужна. */
    fun register(ctx: Context) {
        val app = ctx.applicationContext
        runCatching {
            RuStorePushClient.getToken()
                .addOnSuccessListener { token -> scope.launch { send(app, token) } }
                .addOnFailureListener { e ->
                    Log.i(TAG, "push недоступен: ${e.message}")
                    OrderStatusWatch.schedule(app)
                }
        }.onFailure { OrderStatusWatch.schedule(app) }
    }

    internal suspend fun send(ctx: Context, token: String) {
        if (!SessionManager(ctx).isLoggedIn()) return
        runCatching { AppServer(ctx).pushToken(token) }
            .onSuccess { enabled ->
                ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().putString(TOKEN, token).apply()
                // Сервер шлёт push — свою проверку выключаем, иначе одно изменение придёт дважды
                if (enabled) OrderStatusWatch.stopPolling(ctx) else OrderStatusWatch.schedule(ctx)
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
    internal fun onMessage(ctx: Context, data: Map<String, String>) {
        val order = data["order"]
        val items = data["items"]?.let {
            runCatching { Gson().fromJson<List<List<String>>>(it, object : TypeToken<List<List<String>>>() {}.type) }.getOrNull()
        }
        if (data["type"] == "chat") {
            com.example.myapplication.abcp.showChatNotification(ctx, data["title"] ?: "Менеджер ответил", data["body"].orEmpty())
            return
        }
        if (data["type"] == "promo") {
            showPromoNotification(ctx, data["title"] ?: "Автодруг92", data["body"].orEmpty())
            return
        }
        if (order != null && !items.isNullOrEmpty()) {
            val changed = items.mapIndexed { i, (title, status) -> PositionStatus("$order#$i", order, title, status) }
            OrderStatusWatch.addToFeed(ctx, order, changed)
            // «Готово к выдаче», «ждёт оплаты», «задерживается» — сервер присылает свой текст (и адрес для маршрута)
            showOrderNotification(
                ctx, order, changed,
                customTitle = data["title"].takeIf { data["kind"] != null },
                customText = data["body"].takeIf { data["kind"] != null },
                routeAddress = data["address"]?.takeIf { data["kind"] == "ready" && it.isNotBlank() }
            )
        } else {
            showOrderNotification(ctx, order, emptyList(), data["title"] ?: "Автодруг92", data["body"].orEmpty())
        }
    }
}

class PushService : RuStoreMessagingService() {
    override fun onNewToken(token: String) {
        CoroutineScope(Dispatchers.IO).launch { Push.send(applicationContext, token) }
    }

    override fun onMessageReceived(message: RemoteMessage) {
        Push.onMessage(applicationContext, message.data)
    }
}
