package com.example.myapplication

import android.os.Bundle
import android.util.Log
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp

@OptIn(ExperimentalMaterial3Api::class)
class OrderDetailsActivity : ComponentActivity() {

    private val api = ApiClient.create()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        // ✅ Посмотрим, что реально пришло в intent
        intent.extras?.keySet()?.forEach { k ->
            Log.d("NAV", "OrderDetails extra: $k = ${intent.extras?.get(k)}")
        }

        val session = SessionManager(this)
        val orderNumber = intent.getStringExtra("order_number")?.trim().orEmpty()
        Log.d("NAV", "OrderDetails order_number='$orderNumber'")

        setContent {
            MaterialTheme {
                var loading by remember { mutableStateOf(true) }
                var error by remember { mutableStateOf<String?>(null) }
                var order by remember { mutableStateOf<OrderDetailsDto?>(null) }

                LaunchedEffect(orderNumber) {
                    // ✅ НЕ дергаем API если номер пустой
                    if (orderNumber.isBlank()) {
                        Log.d("ORDER_LIST_API", "orderNumber is blank - skip request")
                        error = "Не передан номер заказа."
                        loading = false
                        return@LaunchedEffect
                    }

                    try {
                        val params = mapOf(
                            "userlogin" to session.login(),
                            "userpsw" to session.passMd5(),
                            "orders[0]" to orderNumber
                        )

                        Log.d("ORDER_LIST_API", "orders[0]='${params["orders[0]"]}'")

                        val resp = api.ordersList(params)
                        Log.d("ORDER_LIST_API", "HTTP=${resp.code()} order=$orderNumber")

                        if (resp.isSuccessful) {
                            val body = resp.body()
                            Log.d("ORDER_LIST_API", "map.size=${body?.size}")

                            order = body?.values?.firstOrNull()

                            Log.d(
                                "ORDER_LIST_API",
                                "loaded: number=${order?.number}, positions=${order?.positions?.size}"
                            )
                        } else {
                            val err = resp.errorBody()?.string()
                            Log.d("ORDER_LIST_API", "ERROR=$err")
                            error = prettifyAbcpError(err)
                        }
                    } catch (e: Exception) {
                        Log.d("ORDER_LIST_API", "EX=${e.message}", e)
                        error = "Не удалось подключиться к серверу."
                    } finally {
                        loading = false
                    }
                }

                Scaffold(
                    topBar = { TopAppBar(title = { Text("Заказ № ${if (orderNumber.isBlank()) "—" else orderNumber}") }) }
                ) { padding ->
                    Box(Modifier.fillMaxSize().padding(padding)) {
                        when {
                            loading -> CircularProgressIndicator(Modifier.align(Alignment.Center))
                            error != null -> Text(error!!, Modifier.padding(24.dp))
                            order == null -> Text("Заказ не найден", Modifier.padding(24.dp))
                            else -> {
                                val positions = order?.positions.orEmpty()

                                LazyColumn(
                                    modifier = Modifier.fillMaxSize(),
                                    contentPadding = PaddingValues(16.dp),
                                    verticalArrangement = Arrangement.spacedBy(12.dp)
                                ) {
                                    item {
                                        ElevatedCard(Modifier.fillMaxWidth()) {
                                            Column(Modifier.padding(16.dp)) {
                                                Text("Статус: ${order?.status ?: "—"}")
                                                Text("Сумма: ${order?.sum ?: "—"}")
                                                Text("Дата: ${order?.date ?: "—"}")
                                            }
                                        }
                                    }

                                    item { Spacer(Modifier.height(8.dp)) }
                                    item { Text("Позиции", style = MaterialTheme.typography.titleMedium) }

                                    items(positions) { p: OrderPositionDto ->
                                        ElevatedCard(Modifier.fillMaxWidth()) {
                                            Column(Modifier.padding(16.dp)) {
                                                Text(p.description ?: "—", style = MaterialTheme.typography.titleSmall)
                                                Spacer(Modifier.height(6.dp))
                                                Text("Бренд: ${p.brand ?: "—"}")
                                                Text("Номер: ${p.number ?: "—"}")
                                                Text("Кол-во: ${p.quantity ?: p.quantityOrdered ?: "—"}")
                                                Text("Цена: ${p.priceInSiteCurrency ?: p.price ?: "—"}")
                                                Text("Статус: ${p.status ?: "—"}")
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
}