package com.example.myapplication.abcp
import com.example.myapplication.ui.theme.AvtodrugTheme

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
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.launch
import com.example.myapplication.OrderPositionDto
import com.example.myapplication.OrderDetailsDto
import com.example.myapplication.SessionManager
import com.example.myapplication.performRequestWithRetry
import com.google.gson.Gson
import com.google.gson.JsonElement
import com.google.gson.reflect.TypeToken

@OptIn(ExperimentalMaterial3Api::class)
class OrderDetailsActivity : ComponentActivity() {

    private val api = ApiClient.create()
    private val gson = Gson()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        val session = SessionManager(this)

        val orderNumber = intent.getStringExtra(EXTRA_ORDER_NUMBER)
        if (orderNumber.isNullOrBlank()) {
            finish()
            return
        }

        val shop = AbcpShop(session)
        val server = com.example.myapplication.server.AppServer(this)

        setContent {
            AvtodrugTheme {
                val scope = rememberCoroutineScope()
                val ctx = androidx.compose.ui.platform.LocalContext.current
                var loading by remember { mutableStateOf(true) }
                var error by remember { mutableStateOf<String?>(null) }
                var details by remember { mutableStateOf<OrderDetailsDto?>(null) }
                var finalIds by remember { mutableStateOf<Set<String>>(emptySet()) }
                var reload by remember { mutableIntStateOf(0) }
                var toCancel by remember { mutableStateOf<OrderPositionDto?>(null) }

                LaunchedEffect(orderNumber, reload) {
                    loading = true
                    error = null
                    try {
                        val resp = performRequestWithRetry {
                            api.orderDetails(userlogin = session.login(), userpsw = session.passMd5(), number = orderNumber)
                        }
                        if (resp.isSuccessful) {
                            details = parseOrderDetailsResponse(resp.body(), gson)
                            if (details == null) error = "Заказ не найден."
                        } else {
                            error = prettifyAbcpError(resp.errorBody()?.string())
                        }
                        finalIds = runCatching { shop.finalStatusIds() }.getOrDefault(emptySet())
                    } catch (_: Exception) {
                        error = "Не удалось подключиться к серверу."
                    } finally {
                        loading = false
                    }
                }

                Scaffold(topBar = { TopAppBar(title = { Text("Заказ № $orderNumber") }) }) { padding ->
                    Box(Modifier.padding(padding).fillMaxSize()) {
                        when {
                            loading -> CircularProgressIndicator(Modifier.align(Alignment.Center))
                            !error.isNullOrBlank() -> Text(
                                error!!, color = MaterialTheme.colorScheme.error,
                                modifier = Modifier.align(Alignment.Center).padding(16.dp)
                            )
                            else -> OrderDetailsContent(
                                details = details!!,
                                canCancel = { p -> !p.positionId.isNullOrBlank() && p.statusId !in finalIds },
                                onCancel = { toCancel = it },
                                onPay = if (server.enabled) {
                                    { payOrder(ctx, scope, server, orderNumber) }
                                } else null
                            )
                        }
                    }
                }

                toCancel?.let { p ->
                    AlertDialog(
                        onDismissRequest = { toCancel = null },
                        title = { Text("Отменить позицию?") },
                        text = {
                            Text(
                                "${p.brand.orEmpty()} ${p.number.orEmpty()}\n${p.description.orEmpty()}\n\n" +
                                    "Менеджер получит запрос на отмену и подтвердит его."
                            )
                        },
                        confirmButton = {
                            Button(onClick = {
                                toCancel = null
                                scope.launch {
                                    try {
                                        val msg = shop.cancelPosition(p.positionId!!)
                                        android.widget.Toast.makeText(ctx, msg, android.widget.Toast.LENGTH_LONG).show()
                                        reload++
                                    } catch (e: Exception) {
                                        android.widget.Toast.makeText(ctx, e.message ?: "Не удалось", android.widget.Toast.LENGTH_LONG).show()
                                    }
                                }
                            }) { Text("Отменить позицию") }
                        },
                        dismissButton = { TextButton(onClick = { toCancel = null }) { Text("Не отменять") } }
                    )
                }
            }
        }
    }

    companion object {
        const val EXTRA_ORDER_NUMBER = "extra_order_number"
    }
}

/** orders/list: массив, {"items": {...}} или объект по номеру; positions — массив или объект {"0": {...}}. */
private fun parseOrderDetailsResponse(body: JsonElement?, gson: Gson): OrderDetailsDto? {
    body ?: return null
    val order = when {
        body.isJsonArray -> body.asJsonArray.firstOrNull()
        body.isJsonObject && body.asJsonObject.get("items")?.isJsonObject == true ->
            body.asJsonObject.getAsJsonObject("items").entrySet().firstOrNull()?.value
        body.isJsonObject && body.asJsonObject.has("number") -> body
        body.isJsonObject -> body.asJsonObject.entrySet().firstOrNull()?.value
        else -> null
    }?.takeIf { it.isJsonObject }?.asJsonObject?.deepCopy() ?: return null
    order.get("positions")?.takeIf { it.isJsonObject }?.let { obj ->
        order.add("positions", com.google.gson.JsonArray().apply { items(obj).forEach { add(it) } })
    }
    return runCatching { gson.fromJson(order, OrderDetailsDto::class.java) }.getOrNull()
}

@Composable
private fun OrderDetailsContent(
    details: OrderDetailsDto,
    canCancel: (OrderPositionDto) -> Boolean,
    onCancel: (OrderPositionDto) -> Unit,
    onPay: (() -> Unit)?
) {
    val positions = details.positions.orEmpty()
    LazyColumn(
        modifier = Modifier.fillMaxSize(),
        contentPadding = PaddingValues(12.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp)
    ) {
        item {
            ElevatedCard(Modifier.fillMaxWidth()) {
                Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                    details.status?.let { StatusChip(it, details.statusColor) }
                    Text("от ${details.date.orEmpty()}", style = MaterialTheme.typography.bodySmall)
                    val office = details.deliveryOffice?.takeIf { it.isNotBlank() }
                    val addr = details.deliveryAddress?.takeIf { it.isNotBlank() }
                    (office ?: addr)?.let { Text(if (office != null) "Самовывоз: $it" else "Доставка: $it") }
                    Row(verticalAlignment = Alignment.Bottom) {
                        Column(Modifier.weight(1f)) {
                            Text("Сумма", style = MaterialTheme.typography.bodySmall)
                            Text(details.sum?.toDoubleOrNull()?.let(::formatRub) ?: details.sum.orEmpty(), fontWeight = FontWeight.Bold)
                        }
                        val debt = details.debt?.replace(',', '.')?.toDoubleOrNull() ?: 0.0
                        if (debt > 0) {
                            Column(horizontalAlignment = Alignment.End) {
                                Text("Долг", style = MaterialTheme.typography.bodySmall)
                                Text(formatRub(debt), color = MaterialTheme.colorScheme.error, fontWeight = FontWeight.Bold)
                            }
                            if (onPay != null) {
                                Spacer(Modifier.width(12.dp))
                                Button(onClick = onPay) { Text("Оплатить") }
                            }
                        }
                    }
                }
            }
        }
        item { Text("Позиции (${positions.size})", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold) }
        if (positions.isEmpty()) item { Text("Позиции отсутствуют") }
        items(positions) { p ->
            ElevatedCard(Modifier.fillMaxWidth()) {
                Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                    Text("${p.brand.orEmpty()} ${p.number.orEmpty()}".trim().ifEmpty { "-" }, style = MaterialTheme.typography.titleSmall)
                    p.description?.takeIf { it.isNotBlank() }?.let { Text(it, style = MaterialTheme.typography.bodyMedium) }
                    val qty = p.quantity ?: p.quantityOrdered ?: "-"
                    val price = (p.priceInSiteCurrency ?: p.price)?.replace(',', '.')?.toDoubleOrNull()
                    Text("$qty шт." + (price?.let { " × ${formatRub(it)}" } ?: ""))
                    p.status?.let { StatusChip(it, p.statusColor) }
                    p.commentAnswer?.takeIf { it.isNotBlank() }?.let {
                        Text("Ответ менеджера: $it", style = MaterialTheme.typography.bodySmall)
                    }
                    if (canCancel(p)) {
                        TextButton(onClick = { onCancel(p) }, contentPadding = PaddingValues(0.dp)) {
                            Text("Отменить позицию", color = MaterialTheme.colorScheme.error)
                        }
                    }
                }
            }
        }
    }
}
