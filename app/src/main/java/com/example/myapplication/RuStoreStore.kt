package com.example.myapplication

import android.app.Activity
import android.content.Context
import android.util.Log
import ru.rustore.sdk.appupdate.manager.factory.RuStoreAppUpdateManagerFactory
import ru.rustore.sdk.appupdate.model.AppUpdateOptions
import ru.rustore.sdk.appupdate.model.AppUpdateType
import ru.rustore.sdk.appupdate.model.UpdateAvailability
import ru.rustore.sdk.review.RuStoreReviewManagerFactory

/**
 * Только для версии из RuStore (BuildConfig.SELF_UPDATE = false):
 * обновление через RuStore и просьба оценить приложение. Без RuStore на телефоне — тихо ничего не делаем.
 */
object RuStoreStore {
    private const val TAG = "RUSTORE"
    private const val PREFS = "rustore_store"
    private const val FIRST_RUN = "first_run"
    private const val ORDERS = "orders_placed"
    private const val LAST_REVIEW = "last_review_ask"
    private const val DAY = 24 * 3600_000L

    private val fromRuStore get() = !BuildConfig.SELF_UPDATE

    /** При запуске: есть новая версия в RuStore — RuStore сам покажет окно, скачает и установит. */
    fun checkUpdate(activity: Activity) {
        if (!fromRuStore) return
        prefs(activity).let { if (!it.contains(FIRST_RUN)) it.edit().putLong(FIRST_RUN, System.currentTimeMillis()).apply() }
        runCatching {
            val manager = RuStoreAppUpdateManagerFactory.create(activity)
            manager.getAppUpdateInfo()
                .addOnSuccessListener { info ->
                    if (info.updateAvailability == UpdateAvailability.UPDATE_AVAILABLE) {
                        manager.startUpdateFlow(info, AppUpdateOptions.Builder().appUpdateType(AppUpdateType.IMMEDIATE).build())
                            .addOnFailureListener { Log.i(TAG, "update flow: ${it.message}") }
                    }
                }
                .addOnFailureListener { Log.i(TAG, "update info: ${it.message}") }
        }
    }

    /**
     * После оформленного заказа: со второго заказа, не раньше 3 дней после установки и не чаще раза в 4 месяца
     * просим оценку. Окно рисует RuStore; ответ нам не сообщается — так и задумано магазином.
     */
    fun onOrderPlaced(activity: Activity) {
        if (!fromRuStore) return
        val p = prefs(activity)
        val orders = p.getInt(ORDERS, 0) + 1
        p.edit().putInt(ORDERS, orders).apply()
        val now = System.currentTimeMillis()
        val installed = p.getLong(FIRST_RUN, now)
        if (orders < 2 || now - installed < 3 * DAY || now - p.getLong(LAST_REVIEW, 0) < 120 * DAY) return
        p.edit().putLong(LAST_REVIEW, now).apply()
        runCatching {
            val manager = RuStoreReviewManagerFactory.create(activity)
            manager.requestReviewFlow()
                .addOnSuccessListener { info ->
                    manager.launchReviewFlow(info)
                    Analytics.event("review_asked")
                }
                .addOnFailureListener { Log.i(TAG, "review: ${it.message}") }
        }
    }

    private fun prefs(ctx: Context) = ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
}
