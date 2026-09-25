package com.example.myapplication.abcp

import com.example.myapplication.OrderDto

/**
 * Последние загруженные списки — пока приложение открыто. Возврат на вкладку показывает их сразу,
 * а свежие данные подгружаются тихо, без спиннера на весь экран. При выходе из аккаунта — очистить.
 */
object MemoryCache {
    @Volatile var orders: List<OrderDto>? = null
    @Volatile var finalStatusIds: Set<String> = emptySet()
    @Volatile var basket: List<BasketItem>? = null
    @Volatile var garage: List<GarageCar>? = null

    fun clear() {
        orders = null
        finalStatusIds = emptySet()
        basket = null
        garage = null
    }
}
