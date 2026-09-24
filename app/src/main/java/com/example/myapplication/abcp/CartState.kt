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
    var count by mutableIntStateOf(0)

    suspend fun refresh(shop: AbcpShop) {
        runCatching { shop.basket() }.onSuccess { count = it.size }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun CartIconButton() {
    val ctx = LocalContext.current
    LaunchedEffect(Unit) { CartState.refresh(AbcpShop(SessionManager(ctx))) }
    IconButton(onClick = { ctx.startActivity(Intent(ctx, CartActivity::class.java)) }) {
        BadgedBox(badge = { if (CartState.count > 0) Badge { Text(CartState.count.toString()) } }) {
            Icon(Icons.Default.ShoppingCart, contentDescription = "Корзина")
        }
    }
}
