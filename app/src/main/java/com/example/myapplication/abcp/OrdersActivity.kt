package com.example.myapplication.abcp

import android.content.Intent
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.example.myapplication.OrderDto
import com.example.myapplication.SessionManager
import com.example.myapplication.performRequestWithRetry
import com.example.myapplication.ui.theme.AvtodrugTheme

class OrdersActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent { AvtodrugTheme { OrdersScreen() } }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun OrdersScreen() {
    val ctx = LocalContext.current
    var loading by remember { mutableStateOf(true) }
    var error by remember { mutableStateOf<String?>(null) }
    var orders by remember { mutableStateOf<List<OrderDto>>(emptyList()) }
    var finalIds by remember { mutableStateOf<Set<String>>(emptySet()) }
    var tab by remember { mutableIntStateOf(0) }

    LaunchedEffect(Unit) {
        val session = SessionManager(ctx)
        try {
            val resp = performRequestWithRetry {
                ApiClient.create().orders(userlogin = session.login(), userpsw = session.passMd5())
            }
            if (resp.isSuccessful) {
                orders = resp.body()?.itemsList().orEmpty()
            } else {
                error = prettifyAbcpError(resp.errorBody()?.string())
            }
            // Если справочник статусов не загрузился — все заказы окажутся в «Активных», это безопасно
            finalIds = runCatching { AbcpShop(session).finalStatusIds() }.getOrDefault(emptySet())
        } catch (_: Exception) {
            error = "Не удалось подключиться к серверу."
        } finally {
            loading = false
        }
    }

    // Заказ завершён, когда у него общий статус и он конечный. Позиции в разных статусах — заказ ещё в работе.
    val (done, active) = orders.partition { o -> o.statusId != null && o.statusId in finalIds }
    val shown = if (tab == 0) active else done

    Scaffold(topBar = { TopAppBar(title = { Text("Заказы") }) }) { padding ->
        Column(Modifier.padding(padding).fillMaxSize()) {
            TabRow(selectedTabIndex = tab) {
                Tab(tab == 0, { tab = 0 }, text = { Text("Активные (${active.size})") })
                Tab(tab == 1, { tab = 1 }, text = { Text("Завершённые (${done.size})") })
            }
            Box(Modifier.fillMaxSize()) {
                when {
                    loading -> CircularProgressIndicator(Modifier.align(Alignment.Center))
                    !error.isNullOrBlank() -> Text(
                        error!!, color = MaterialTheme.colorScheme.error,
                        modifier = Modifier.align(Alignment.Center).padding(16.dp)
                    )
                    shown.isEmpty() -> Text(
                        if (tab == 0) "Активных заказов нет" else "Завершённых заказов нет",
                        Modifier.align(Alignment.Center)
                    )
                    else -> LazyColumn(
                        modifier = Modifier.fillMaxSize(),
                        contentPadding = PaddingValues(12.dp),
                        verticalArrangement = Arrangement.spacedBy(10.dp)
                    ) {
                        items(shown) { order ->
                            OrderRow(order) {
                                order.number?.let { num ->
                                    ctx.startActivity(
                                        Intent(ctx, OrderDetailsActivity::class.java)
                                            .putExtra(OrderDetailsActivity.EXTRA_ORDER_NUMBER, num)
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

@Composable
private fun OrderRow(order: OrderDto, onClick: () -> Unit) {
    ElevatedCard(Modifier.fillMaxWidth().clickable(onClick = onClick)) {
        Column(Modifier.padding(14.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text("№ ${order.number ?: "-"}", style = MaterialTheme.typography.titleMedium, modifier = Modifier.weight(1f))
                Text(order.date.orEmpty(), style = MaterialTheme.typography.bodySmall)
            }
            Spacer(Modifier.height(8.dp))
            order.status?.let { StatusChip(it, order.statusColor) }
            Spacer(Modifier.height(8.dp))
            Row(verticalAlignment = Alignment.Bottom) {
                Column(Modifier.weight(1f)) {
                    Text("Сумма", style = MaterialTheme.typography.bodySmall)
                    Text(order.sum?.toDoubleOrNull()?.let(::formatRub) ?: order.sum.orEmpty(), fontWeight = FontWeight.Bold)
                }
                val debt = order.debt?.toDoubleOrNull() ?: 0.0
                if (debt > 0) {
                    Column(horizontalAlignment = Alignment.End) {
                        Text("Долг", style = MaterialTheme.typography.bodySmall)
                        Text(formatRub(debt), color = MaterialTheme.colorScheme.error, fontWeight = FontWeight.Bold)
                    }
                }
            }
        }
    }
}

/** Плашка статуса в цвете, заданном в ABCP (statusColor), с мягким фоном. */
@Composable
fun StatusChip(text: String, hex: String?) {
    val c = parseAbcpColor(hex) ?: MaterialTheme.colorScheme.primary
    Text(
        text,
        style = MaterialTheme.typography.labelMedium,
        color = c,
        modifier = Modifier
            .background(c.copy(alpha = 0.14f), RoundedCornerShape(8.dp))
            .padding(horizontal = 8.dp, vertical = 3.dp)
    )
}

fun parseAbcpColor(hex: String?): Color? {
    val h = hex?.trim()?.removePrefix("#") ?: return null
    if (h.length != 6) return null
    return h.toLongOrNull(16)?.let { Color(0xFF000000 or it) }
}
