package com.example.myapplication.abcp

import android.content.Intent
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.example.myapplication.OrderDto
import com.example.myapplication.SessionManager
import com.example.myapplication.performRequestWithRetry

class OrdersActivity : ComponentActivity() {

    private val api = ApiClient.create()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        val session = SessionManager(this)

        setContent {
            MaterialTheme {
                var loading by remember { mutableStateOf(true) }
                var error by remember { mutableStateOf<String?>(null) }

                // ✅ единственная переменная со списком заказов
                var orders by remember { mutableStateOf<List<OrderDto>>(emptyList()) }

                LaunchedEffect(Unit) {
                    loading = true
                    error = null
                    orders = emptyList()

                    try {
                        val resp = performRequestWithRetry {
                            api.orders(
                                userlogin = session.login(),
                                userpsw = session.passMd5()
                            )
                        }

                        if (resp.isSuccessful) {
                            val body = resp.body()
                            orders = body?.itemsList().orEmpty()
                        } else {
                            val raw = resp.errorBody()?.string()
                            error = prettifyAbcpError(raw)
                        }
                    } catch (_: Exception) {
                        error = "Не удалось подключиться к серверу."
                    } finally {
                        loading = false
                    }
                }
                @OptIn(ExperimentalMaterial3Api::class)
                Scaffold(
                    topBar = { TopAppBar(title = { Text("Заказы") }) }
                ) { padding ->
                    Box(
                        modifier = Modifier
                            .padding(padding)
                            .fillMaxSize()
                    ) {
                        when {
                            loading -> {
                                CircularProgressIndicator(Modifier.align(Alignment.Center))
                            }

                            !error.isNullOrBlank() -> {
                                Text(
                                    text = error!!,
                                    color = MaterialTheme.colorScheme.error,
                                    modifier = Modifier
                                        .align(Alignment.Center)
                                        .padding(16.dp)
                                )
                            }

                            orders.isEmpty() -> {
                                Text("Заказы не найдены", Modifier.align(Alignment.Center))
                            }

                            else -> {
                                LazyColumn(
                                    modifier = Modifier.fillMaxSize(),
                                    contentPadding = PaddingValues(12.dp),
                                    verticalArrangement = Arrangement.spacedBy(10.dp)
                                ) {
                                    items(orders) { order ->
                                        OrderRow(
                                            order = order,
                                            onClick = {
                                                order.number?.let { num ->
                                                    startActivity(
                                                        Intent(
                                                            this@OrdersActivity,
                                                            OrderDetailsActivity::class.java
                                                        ).putExtra(OrderDetailsActivity.EXTRA_ORDER_NUMBER, num)
                                                    )
                                                }
                                            }
                                        )
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

@Composable
private fun OrderRow(order: OrderDto, onClick: () -> Unit) {
    Card(
        modifier = Modifier
            .fillMaxWidth()
            .clickable(onClick = onClick)
    ) {
        Column(Modifier.padding(14.dp)) {
            Text(
                text = "Заказ №${order.number ?: "-"}",
                style = MaterialTheme.typography.titleMedium
            )
            Spacer(Modifier.height(6.dp))
            Text(text = "Статус: ${order.status ?: "-"}")
            Spacer(Modifier.height(4.dp))
            Text(text = "Сумма: ${order.sum ?: "-"}")
            Spacer(Modifier.height(4.dp))
            Text(text = "Дата: ${order.date ?: "-"}")
        }
    }
}
