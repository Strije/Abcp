package com.example.myapplication

import android.app.Application
import android.util.Log
import io.appmetrica.analytics.AppMetrica
import io.appmetrica.analytics.AppMetricaConfig
import io.appmetrica.analytics.Revenue
import io.appmetrica.analytics.ecommerce.ECommerceAmount
import io.appmetrica.analytics.ecommerce.ECommerceCartItem
import io.appmetrica.analytics.ecommerce.ECommerceEvent
import io.appmetrica.analytics.ecommerce.ECommerceOrder
import io.appmetrica.analytics.ecommerce.ECommercePrice
import io.appmetrica.analytics.ecommerce.ECommerceProduct
import io.appmetrica.analytics.ecommerce.ECommerceScreen

/**
 * AppMetrica (Яндекс): вылеты приложения и ошибки, которые видят клиенты, + несколько событий.
 * Только технические данные: экран, действие, текст ошибки, версия, модель телефона, номер клиента в ABCP и состав
 * заказа (бренд, номер, цена). Телефоны, имена, логины и пароли сюда не отправляем. Без ключа (локальная отладка) ничего не шлётся.
 */
object Analytics {
    @Volatile private var active = false

    fun init(app: Application) {
        val key = BuildConfig.APPMETRICA_KEY
        if (key.isBlank() || BuildConfig.DEBUG) return
        runCatching {
            AppMetrica.activate(app, AppMetricaConfig.newConfigBuilder(key).withCrashReporting(true).build())
            active = true
        }.onFailure { Log.w("ANALYTICS", "AppMetrica не запущена", it) }
    }

    /** Ошибка, которую увидел клиент. where — «Экран → действие», по нему ошибки группируются в отчёте. */
    fun error(where: String, e: Throwable) {
        if (!active) return
        // Клиент ушёл с экрана, пока шёл запрос, — это не ошибка (иначе засоряет отчёт)
        if (e is kotlinx.coroutines.CancellationException) return
        runCatching { AppMetrica.reportError(where, e.message ?: e.javaClass.simpleName, e) }
    }

    /** Событие без персональных данных: поиск, в корзину, заказ оформлен и т.п. */
    fun event(name: String, params: Map<String, Any?> = emptyMap()) {
        if (!active) return
        runCatching { AppMetrica.reportEvent(name, params.filterValues { it != null }) }
    }

    /**
     * Номер клиента в ABCP (внутренний ID, без телефона и имени) — чтобы в «Профилях» видеть клиента, а не устройство:
     * как часто заходит и что заказывает. null — вышел из аккаунта.
     */
    fun client(id: Long?) {
        if (!active) return
        runCatching { AppMetrica.setUserProfileID(id?.toString()) }
    }

    /** Открыт экран — карта путей: что открывают и где бросают. */
    fun screen(name: String) = event("screen", mapOf("name" to name))

    // ---------- E-commerce: стандартные отчёты AppMetrica (воронка, доход, средний чек) ----------

    data class Item(val brand: String, val number: String, val title: String, val price: Double, val qty: Int)

    private fun product(i: Item) = ECommerceProduct("${i.brand}|${i.number}")
        .setName("${i.brand} ${i.number}".trim() + i.title.takeIf { it.isNotBlank() }?.let { " — $it" }.orEmpty())
        .setActualPrice(ECommercePrice(ECommerceAmount(i.price, "RUB")))

    private fun cartItem(i: Item) = ECommerceCartItem(product(i), ECommercePrice(ECommerceAmount(i.price * i.qty, "RUB")), i.qty.toDouble())

    private fun ecom(build: () -> ECommerceEvent) {
        if (!active) return
        runCatching { AppMetrica.reportECommerce(build()) }
    }

    fun ecomProductCard(i: Item) = ecom { ECommerceEvent.showProductCardEvent(product(i), ECommerceScreen().setName("Выдача")) }
    fun ecomAddToCart(i: Item) = ecom { ECommerceEvent.addCartItemEvent(cartItem(i)) }
    fun ecomBeginCheckout(items: List<Item>) = ecom { ECommerceEvent.beginCheckoutEvent(ECommerceOrder("checkout", items.map(::cartItem))) }

    /** Заказ оформлен: покупка в e-commerce и выручка (In-app выручка, ARPU) — сумма заказа в рублях. */
    fun purchase(orderId: String, items: List<Item>) {
        ecom { ECommerceEvent.purchaseEvent(ECommerceOrder(orderId, items.map(::cartItem))) }
        if (!active) return
        val total = items.sumOf { it.price * it.qty }
        runCatching {
            AppMetrica.reportRevenue(
                Revenue.newBuilder((total * 1_000_000).toLong(), java.util.Currency.getInstance("RUB"))
                    .withProductID("order").withQuantity(1).build()
            )
        }
    }
}
