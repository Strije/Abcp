package com.example.myapplication.push

import android.content.Context
import android.util.Log
import com.example.myapplication.BuildConfig
import com.google.firebase.FirebaseApp
import com.google.firebase.FirebaseOptions
import com.google.firebase.messaging.FirebaseMessaging
import com.google.firebase.messaging.FirebaseMessagingService
import com.google.firebase.messaging.RemoteMessage

/**
 * Второй канал push — Google FCM (10.11.2026). На Xiaomi/POCO прошивка усыпляет RuStore, и push лежит у него, пока
 * человек не откроет телефон; сервисы Google не усыпляются, а сообщение с высоким приоритетом приходит сразу.
 * Сервер шлёт одно и то же по обоим каналам с одним «mid» — показываем первое пришедшее (Push.onMessage).
 * Нет сервисов Google на телефоне — работает только RuStore, как раньше.
 */
object Fcm {
    private const val TAG = "FCM"

    /** Без плагина google-services (AGP 9): настройки проекта — из BuildConfig. */
    fun init(ctx: Context) {
        runCatching {
            if (FirebaseApp.getApps(ctx).isEmpty()) FirebaseApp.initializeApp(
                ctx,
                FirebaseOptions.Builder()
                    .setProjectId(BuildConfig.FCM_PROJECT_ID)
                    .setApplicationId(BuildConfig.FCM_APP_ID)
                    .setGcmSenderId(BuildConfig.FCM_SENDER_ID)
                    .setApiKey(BuildConfig.FCM_API_KEY)
                    .build()
            )
        }.onFailure { Log.w(TAG, "Firebase не запущен", it) }
    }

    /** Токен Google или null (нет сервисов Google, нет сети) — тогда только RuStore. */
    fun token(onToken: (String?) -> Unit) {
        runCatching {
            FirebaseMessaging.getInstance().token
                .addOnSuccessListener { onToken(it?.takeIf { t -> t.isNotBlank() }) }
                .addOnFailureListener { Log.i(TAG, "токена нет: ${it.message}"); onToken(null) }
        }.onFailure { onToken(null) }
    }
}

class FcmService : FirebaseMessagingService() {
    override fun onNewToken(token: String) {
        Push.register(applicationContext)   // сервер должен знать новый токен вместе с токеном RuStore
    }

    override fun onMessageReceived(message: RemoteMessage) {
        // Сервер шлёт через Google только данные — уведомление рисуем сами (и не рисуем второе, если RuStore успел раньше)
        Push.measure(message.data, "fcm")
        Push.onMessage(applicationContext, message.data)
    }
}
