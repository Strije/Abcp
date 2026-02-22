package com.example.myapplication

import androidx.compose.material3.ExperimentalMaterial3Api
import android.os.Bundle
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

        val session = SessionManager(this)

        val orderNumber = intent.getStringExtra(EXTRA_ORDER_NUMBER)
        if (orderNumber.isNullOrBlank()) {
            finish()
            return
        }

        setContent {
            MaterialTheme {
                var loading by remember { mutableStateOf(true) }
                var error by remember { mutableStateOf<String?>(null) }

                // ✅ тут будет OrderDetailsDto
                var details by remember { mutableStateOf<OrderDetailsDto?>(null) }

                LaunchedEffect(orderNumber) {
                    loading = true
                    error = null
                    details = null

                    try {
                        val resp = performRequestWithRetry {
                            api.orderDetails(
                                userlogin = session.login(),
                                userpsw = session.passMd5(),
                                number = orderNumber,
                                format = "p"
                            )
                        }

                        if (resp.isSuccessful) {
                            // ✅ ответ = Map<String, OrderDetailsDto>
                            details = resp.body()?.values?.firstOrNull()
                            if (details == null) {
                                error = "Пустой ответ от сервера."
                            }
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

                Scaffold(
                    topBar = { TopAppBar(title = { Text("Заказ №$orderNumber") }) }
                ) { padding ->
                    Box(
                        modifier = Modifier
                            .padding(padding)
                            .fillMaxSize()
                    ) {
                        when {
                            loading -> CircularProgressIndicator(Modifier.align(Alignment.Center))

                            !error.isNullOrBlank() -> Text(
                                text = error!!,
                                color = MaterialTheme.colorScheme.error,
                                modifier = Modifier
                                    .align(Alignment.Center)
                                    .padding(16.dp)
                            )

                            details == null -> Text(
                                "Данные заказа не найдены",
                                modifier = Modifier.align(Alignment.Center)
                            )

                            else -> OrderDetailsContent(details!!)
                        }
                    }
                }
            }
        }
    }

    companion object {
        const val EXTRA_ORDER_NUMBER = "extra_order_number"
    }
}

@Composable
private fun OrderDetailsContent(details: OrderDetailsDto) {
    val positions = details.positions.orEmpty()

    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(16.dp)
    ) {
        Text("Статус: ${details.status ?: "-"}", style = MaterialTheme.typography.titleMedium)
        Spacer(Modifier.height(6.dp))
        Text("Дата: ${details.date ?: "-"}")
        Spacer(Modifier.height(6.dp))
        Text("Сумма: ${details.sum ?: "-"}")
        Spacer(Modifier.height(6.dp))
        Text("Доставка: ${details.deliveryAddress ?: "-"} / ${details.deliveryOffice ?: "-"}")
        Spacer(Modifier.height(12.dp))
        Divider()
        Spacer(Modifier.height(12.dp))

        Text("Позиции (${positions.size})", style = MaterialTheme.typography.titleSmall)
        Spacer(Modifier.height(8.dp))

        if (positions.isEmpty()) {
            Text("Позиции отсутствуют")
            return
        }

        LazyColumn(
            modifier = Modifier.fillMaxSize(),
            verticalArrangement = Arrangement.spacedBy(10.dp)
        ) {
            items(positions) { p ->
                Card(Modifier.fillMaxWidth()) {
                    Column(Modifier.padding(12.dp)) {
                        Text(
                            text = "${p.brand ?: ""} ${p.number ?: ""}".trim().ifEmpty { "-" },
                            style = MaterialTheme.typography.titleSmall
                        )
                        Spacer(Modifier.height(6.dp))
                        Text(p.description ?: "-", style = MaterialTheme.typography.bodyMedium)
                        Spacer(Modifier.height(6.dp))
                        Text("Кол-во: ${p.quantity ?: p.quantityOrdered ?: "-"}  Цена: ${p.price ?: "-"}")
                        Spacer(Modifier.height(4.dp))
                        Text("Статус: ${p.status ?: "-"}")
                    }
                }
            }
        }
    }
}