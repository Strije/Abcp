package com.example.myapplication.abcp

import android.content.Intent
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ShoppingCart
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.platform.LocalContext
import com.example.myapplication.SessionManager

/** Количество позиций в корзине — общее для всех экранов, чтобы счётчик обновлялся сразу. */
object CartState {
    private val shown = mutableIntStateOf(0)
    /** Когда счётчик последний раз стал известен (0 — ещё ни разу) */
    @Volatile private var knownAt = 0L

    var count: Int
        get() = shown.intValue
        set(value) { shown.intValue = value; knownAt = System.currentTimeMillis() }

    /** Перечитать корзину сейчас — после того, как её изменили. */
    suspend fun refresh(shop: AbcpShop) {
        runCatching { shop.basket() }.onSuccess { count = it.size }
    }

    /** Для значка в шапке: счётчик свежий (моложе 20 секунд) — корзину на каждом экране заново не читаем. */
    suspend fun refreshIfStale(shop: AbcpShop) {
        if (knownAt != 0L && System.currentTimeMillis() - knownAt in 0 until 20_000L) return
        refresh(shop)
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun CartIconButton() {
    val ctx = LocalContext.current
    LaunchedEffect(Unit) { CartState.refreshIfStale(AbcpShop(SessionManager(ctx))) }
    IconButton(onClick = { ctx.startActivity(Intent(ctx, CartActivity::class.java)) }) {
        BadgedBox(badge = { if (CartState.count > 0) Badge { Text(CartState.count.toString()) } }) {
            Icon(Icons.Default.ShoppingCart, contentDescription = "Корзина")
        }
    }
}
