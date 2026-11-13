package com.example.myapplication.abcp

import com.example.myapplication.OrderDto
import java.util.concurrent.ConcurrentHashMap

/**
 * Значение с временем загрузки и логином владельца. Владелец нужен, чтобы ответ, пришедший уже после выхода
 * из аккаунта, не достался следующему клиенту.
 */
class Timed<T : Any>(val value: T, val owner: String = "", val at: Long = System.currentTimeMillis()) {
    /** Значение, пока оно моложе [ttlMs] и принадлежит [forOwner]; иначе null — пора читать заново. */
    fun fresh(ttlMs: Long, forOwner: String = ""): T? =
        if (owner == forOwner && System.currentTimeMillis() - at in 0 until ttlMs) value else null
}

/** Справочники оформления, которые меняются редко (даты отгрузки сюда не входят — они зависят от корзины). */
data class CheckoutLists(
    val payments: List<IdName>,
    val shipmentMethods: List<IdName>,
    val offices: List<IdName>,
    val addresses: List<IdName>
)

/**
 * Последние загруженные списки — пока приложение открыто. Возврат на вкладку показывает их сразу,
 * а свежие данные подгружаются тихо, без спиннера на весь экран. При выходе из аккаунта — очистить.
 *
 * Редко меняющееся (история поиска, «с этим покупают», справочники оформления) держим со сроком жизни:
 * лимит «прочих» запросов ABCP — 10 000 в сутки на весь магазин, и каждый показ экрана его тратил.
 */
object MemoryCache {
    const val HISTORY_TTL_MS = 30L * 60 * 1000
    const val ADVICES_TTL_MS = 30L * 60 * 1000
    const val CHECKOUT_TTL_MS = 30L * 60 * 1000
    private const val ADVICES_MAX = 200

    @Volatile var orders: List<OrderDto>? = null
    @Volatile var finalStatusIds: Set<String> = emptySet()
    @Volatile var basket: List<BasketItem>? = null
    /** Гараж: срок жизни и копию на диске ведёт [GarageCache] */
    @Volatile var garage: List<GarageCar>? = null
    @Volatile var history: Timed<List<BrandHit>>? = null
    /** «С этим покупают» по ключу бренд|номер */
    val advices = ConcurrentHashMap<String, Timed<List<BrandHit>>>()
    @Volatile var checkoutLists: Timed<CheckoutLists>? = null

    fun putAdvices(key: String, list: List<BrandHit>) {
        // Долгая сессия с сотнями выдач — не копим бесконечно
        if (advices.size >= ADVICES_MAX) advices.clear()
        advices[key] = Timed(list)
    }

    fun clear() {
        orders = null
        finalStatusIds = emptySet()
        basket = null
        garage = null
        history = null
        advices.clear()
        checkoutLists = null
    }
}
