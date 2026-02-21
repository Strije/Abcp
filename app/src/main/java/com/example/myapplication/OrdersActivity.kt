package com.example.myapplication

import android.content.Intent
import android.os.Bundle
import android.util.Log
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

@OptIn(ExperimentalMaterial3Api::class)
class OrdersActivity : ComponentActivity() {

    private val api = ApiClient.create()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        val session = SessionManager(this)

        setContent {
            MaterialTheme {
                var loading by remember { mutableStateOf(true) }
                var error by remember { mutableStateOf<String?>(null) }
                var orders by remember { mutableStateOf<List<OrderDto>>(emptyList()) }

                LaunchedEffect(Unit) {
                    try {
                        val resp = api.orders(
                            userlogin = session.login(),
                            userpsw = session.passMd5(),
                            skip = 0,
                            limit = 100
                        )

                        Log.d("ORDERS_API", "HTTP=${resp.code()}")

                        if (resp.isSuccessful) {
                            val body = resp.body()
                            Log.d("ORDERS_API", "count=${body?.count}, items=${body?.items?.size}")

                            val list = body?.itemsList().orEmpty()
                            Log.d("ORDERS_API", "list.size=${list.size}")

                            orders = list
                        } else {
                            val err = resp.errorBody()?.string()
                            Log.d("ORDERS_API", "ERROR=$err")
                            error = prettifyAbcpError(err)
                        }
                    } catch (e: Exception) {
                        Log.d("ORDERS_API", "EX=${e.message}", e)
                        error = "Не удалось подключиться к серверу."
                    } finally {
                        loading = false
                    }
                }

                Scaffold(topBar = { TopAppBar(title = { Text("Мои заказы") }) }) { padding ->
                    Box(Modifier.fillMaxSize().padding(padding)) {
                        when {
                            loading -> CircularProgressIndicator(Modifier.align(Alignment.Center))
                            error != null -> Text(error!!, Modifier.padding(24.dp))
                            orders.isEmpty() -> Text("Заказов нет", Modifier.padding(24.dp))
                            else -> LazyColumn(
                                modifier = Modifier.fillMaxSize(),
                                contentPadding = PaddingValues(16.dp),
                                verticalArrangement = Arrangement.spacedBy(12.dp)
                            ) {
                                items(orders) { o: OrderDto ->
                                    val number = o.number?.trim().orEmpty()

                                    ElevatedCard(
                                        modifier = Modifier
                                            .fillMaxWidth()
                                            .clickable {
                                                Log.d("NAV", "open details for number='$number'")
                                                startActivity(
                                                    Intent(this@OrdersActivity, OrderDetailsActivity::class.java)
                                                        .putExtra("order_number", number)
                                                )
                                            }
                                    ) {
                                        Column(Modifier.padding(16.dp)) {
                                            Text(
                                                "Заказ № ${o.number ?: "—"}",
                                                style = MaterialTheme.typography.titleMedium
                                            )
                                            Spacer(Modifier.height(6.dp))
                                            Text("Статус: ${o.status ?: "—"}")
                                            Text("Сумма: ${o.sum ?: "—"}")
                                            Text("Дата: ${o.date ?: "—"}")
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