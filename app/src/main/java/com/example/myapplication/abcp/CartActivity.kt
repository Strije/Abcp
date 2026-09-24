package com.example.myapplication.abcp

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
import kotlinx.coroutines.launch

@OptIn(ExperimentalMaterial3Api::class)
class CartActivity : ComponentActivity() {

    private var reloadKey by mutableIntStateOf(0)

    override fun onResume() {
        super.onResume()
        reloadKey++ // вернулись с оформления или поиска — перечитать корзину
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val shop = AbcpShop(SessionManager(this))

        setContent {
            MaterialTheme {
                val scope = rememberCoroutineScope()
                var loading by remember { mutableStateOf(true) }
                var error by remember { mutableStateOf<String?>(null) }
                var basket by remember { mutableStateOf<List<BasketItem>>(emptyList()) }

                LaunchedEffect(reloadKey) {
                    loading = true
                    error = null
                    try {
                        basket = shop.basket()
                        CartState.count = basket.size
                    } catch (e: Exception) {
                        error = e.message ?: "Ошибка загрузки корзины"
                    } finally {
                        loading = false
                    }
                }

                val total = basket.sumOf { it.price * it.quantity }

                Scaffold(
                    topBar = { TopAppBar(title = { Text("Корзина") }) },
                    bottomBar = {
                        if (basket.isNotEmpty()) {
                            Surface(tonalElevation = 3.dp) {
                                Row(
                                    Modifier.fillMaxWidth().padding(16.dp),
                                    verticalAlignment = Alignment.CenterVertically
                                ) {
                                    Column(Modifier.weight(1f)) {
                                        Text("Итого", style = MaterialTheme.typography.bodySmall)
                                        Text(formatRub(total), style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.Bold)
                                    }
                                    Button(onClick = {
                                        startActivity(Intent(this@CartActivity, CheckoutActivity::class.java))
                                    }) { Text("Оформить") }
                                }
                            }
                        }
                    }
                ) { padding ->
                    Box(Modifier.padding(padding).fillMaxSize()) {
                        when {
                            loading -> CircularProgressIndicator(Modifier.align(Alignment.Center))
                            error != null -> Text(error!!, color = MaterialTheme.colorScheme.error, modifier = Modifier.padding(16.dp))
                            basket.isEmpty() -> Column(
                                Modifier.align(Alignment.Center),
                                horizontalAlignment = Alignment.CenterHorizontally
                            ) {
                                Text("Корзина пуста")
                                Spacer(Modifier.height(12.dp))
                                Button(onClick = {
                                    startActivity(Intent(this@CartActivity, SearchActivity::class.java))
                                }) { Text("Найти запчасть") }
                            }
                            else -> LazyColumn(
                                contentPadding = PaddingValues(12.dp),
                                verticalArrangement = Arrangement.spacedBy(8.dp)
                            ) {
                                items(basket) { b ->
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
                                                Text("Срок: ${formatDelivery(b.deadlineHours, b.deadlineHoursMax)}", style = MaterialTheme.typography.bodySmall)
                                                b.errorMessage?.let { Text(it, color = MaterialTheme.colorScheme.error) }
                                            }
                                            IconButton(onClick = {
                                                scope.launch {
                                                    try {
                                                        shop.removeFromBasket(b)
                                                        reloadKey++
                                                    } catch (e: Exception) {
                                                        Toast.makeText(this@CartActivity, e.message ?: "Ошибка", Toast.LENGTH_LONG).show()
                                                    }
                                                }
                                            }) { Icon(Icons.Default.Delete, "Удалить") }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}
