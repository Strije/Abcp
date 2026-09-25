package com.example.myapplication.abcp
import com.example.myapplication.ui.theme.AvtodrugTheme

import android.content.Intent
import android.os.Bundle
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.example.myapplication.SearchActivity
import com.example.myapplication.SessionManager
import androidx.compose.ui.platform.LocalContext
import kotlinx.coroutines.launch
import com.example.myapplication.ui.EmptyState
import com.example.myapplication.ui.RefreshableContent

class CartActivity : ComponentActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent { AvtodrugTheme { CartScreen(0) } }
    }
}

/** reloadKey меняется — корзина перечитывается (возврат на экран, смена вкладки). */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun CartScreen(reloadKey: Int) {
    val ctx = LocalContext.current
    val shop = remember { AbcpShop(SessionManager(ctx)) }
    var localReload by remember { mutableIntStateOf(0) }
    val scope = rememberCoroutineScope()
    var loading by remember { mutableStateOf(true) }
    var error by remember { mutableStateOf<String?>(null) }
    var basket by remember { mutableStateOf(MemoryCache.basket) }
    // Позиция, которую сейчас меняем, — её кнопки заблокированы, остальной экран живой
    var busy by remember { mutableStateOf<String?>(null) }
    var confirmDelete by remember { mutableStateOf<BasketItem?>(null) }

    LaunchedEffect(reloadKey, localReload) {
        loading = true
        error = null
        try {
            basket = shop.basket().also { MemoryCache.basket = it; CartState.count = it.size }
        } catch (e: Exception) {
            com.example.myapplication.Analytics.error("Корзина → загрузка", e)
            error = e.message ?: "Не удалось загрузить корзину"
        } finally {
            loading = false
        }
    }

    // Вернулись с оформления или из поиска — перечитать
    com.example.myapplication.ui.OnResume { localReload++ }

    fun mutate(b: BasketItem, action: suspend () -> Unit) {
        busy = b.itemKey
        scope.launch {
            try {
                action()
                localReload++
            } catch (e: Exception) {
                com.example.myapplication.Analytics.error("Корзина → изменение", e)
                Toast.makeText(ctx, e.message ?: "Ошибка", Toast.LENGTH_LONG).show()
            } finally {
                busy = null
            }
        }
    }

    val items = basket.orEmpty()
    val total = items.sumOf { it.price * it.quantity }

    Scaffold(
        topBar = { TopAppBar(title = { Text("Корзина") }) },
        bottomBar = {
            if (items.isNotEmpty()) {
                Surface(tonalElevation = 3.dp) {
                    Row(
                        Modifier.fillMaxWidth().padding(16.dp),
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        Column(Modifier.weight(1f)) {
                            Text("Итого · ${items.size} поз.", style = MaterialTheme.typography.bodySmall)
                            Text(formatRub(total), style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.Bold)
                        }
                        Button(
                            enabled = busy == null,
                            onClick = {
                                com.example.myapplication.Analytics.event("checkout_open", mapOf("positions" to items.size))
                                ctx.startActivity(Intent(ctx, CheckoutActivity::class.java))
                            }
                        ) { Text("Оформить") }
                    }
                }
            }
        }
    ) { padding ->
        RefreshableContent(
            hasData = basket != null, loading = loading, error = error,
            onRefresh = { localReload++ }, modifier = Modifier.padding(padding)
        ) {
            if (items.isEmpty()) EmptyState("Корзина пуста", "Найти запчасть") {
                ctx.startActivity(Intent(ctx, SearchActivity::class.java))
            } else LazyColumn(
                contentPadding = PaddingValues(12.dp),
                verticalArrangement = Arrangement.spacedBy(8.dp)
            ) {
                error?.let { e -> item { Text(e, color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.bodySmall) } }
                items(items, key = { it.basketId + "|" + it.itemKey }) { b ->
                    val isBusy = busy == b.itemKey
                    ElevatedCard(Modifier.fillMaxWidth()) {
                        Row(Modifier.padding(12.dp), verticalAlignment = Alignment.CenterVertically) {
                            Column(Modifier.weight(1f)) {
                                Text("${b.brand} ${b.number}", style = MaterialTheme.typography.titleSmall)
                                if (b.description.isNotBlank()) Text(b.description, style = MaterialTheme.typography.bodySmall)
                                Spacer(Modifier.height(4.dp))
                                Text(
                                    "${formatRub(b.price)} × ${b.quantity} = ${formatRub(b.price * b.quantity)}",
                                    fontWeight = FontWeight.Bold
                                )
                                Row(verticalAlignment = Alignment.CenterVertically) {
                                    OutlinedButton(
                                        onClick = { mutate(b) { shop.changeQuantity(b, b.quantity - b.packing) } },
                                        enabled = !isBusy && b.quantity > b.packing,
                                        contentPadding = PaddingValues(0.dp),
                                        modifier = Modifier.size(40.dp)
                                    ) { Text("−") }
                                    Box(Modifier.widthIn(min = 64.dp), contentAlignment = Alignment.Center) {
                                        if (isBusy) CircularProgressIndicator(Modifier.size(18.dp), strokeWidth = 2.dp)
                                        else Text("${b.quantity} шт.")
                                    }
                                    OutlinedButton(
                                        onClick = { mutate(b) { shop.changeQuantity(b, b.quantity + b.packing) } },
                                        enabled = !isBusy,
                                        contentPadding = PaddingValues(0.dp),
                                        modifier = Modifier.size(40.dp)
                                    ) { Text("+") }
                                }
                                Text("Срок: ${formatDelivery(b.deadlineHours, b.deadlineHoursMax)}", style = MaterialTheme.typography.bodySmall)
                                b.errorMessage?.let { Text(it, color = MaterialTheme.colorScheme.error) }
                            }
                            IconButton(enabled = !isBusy, onClick = { confirmDelete = b }) { Icon(Icons.Default.Delete, "Удалить") }
                        }
                    }
                }
            }
        }
    }

    confirmDelete?.let { b ->
        AlertDialog(
            onDismissRequest = { confirmDelete = null },
            title = { Text("Удалить из корзины?") },
            text = { Text("${b.brand} ${b.number}" + if (b.description.isNotBlank()) "\n${b.description}" else "") },
            confirmButton = {
                TextButton(onClick = { confirmDelete = null; mutate(b) { shop.removeFromBasket(b) } }) { Text("Удалить") }
            },
            dismissButton = { TextButton(onClick = { confirmDelete = null }) { Text("Отмена") } }
        )
    }
}
