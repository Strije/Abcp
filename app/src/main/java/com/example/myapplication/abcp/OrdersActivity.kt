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
import com.example.myapplication.server.AppServer
import com.example.myapplication.ui.theme.AvtodrugTheme
import com.example.myapplication.ui.EmptyState
import com.example.myapplication.ui.OnResume
import com.example.myapplication.ui.RefreshableContent
import androidx.compose.runtime.saveable.rememberSaveable
import kotlinx.coroutines.launch

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
    val scope = rememberCoroutineScope()
    val server = remember { AppServer(ctx) }
    // Прошлый список показываем сразу, свежий подгружаем тихо
    var orders by remember { mutableStateOf(MemoryCache.orders) }
    var finalIds by remember { mutableStateOf(MemoryCache.finalStatusIds) }
    var loading by remember { mutableStateOf(true) }
    var error by remember { mutableStateOf<String?>(null) }
    var tab by rememberSaveable { mutableIntStateOf(0) }
    var reload by remember { mutableIntStateOf(0) }

    LaunchedEffect(reload) {
        val session = SessionManager(ctx)
        loading = true
        error = null
        try {
            val resp = performRequestWithRetry {
                ApiClient.create().orders(userlogin = session.login(), userpsw = session.passMd5())
            }
            if (resp.isSuccessful) {
                orders = resp.body()?.itemsList().orEmpty().also { MemoryCache.orders = it }
            } else {
                error = prettifyAbcpError(resp.errorBody()?.string())
            }
            // Если справочник статусов не загрузился — все заказы окажутся в «Активных», это безопасно
            finalIds = runCatching { AbcpShop(session).finalStatusIds() }.getOrDefault(finalIds)
            MemoryCache.finalStatusIds = finalIds
        } catch (_: Exception) {
            error = "Не удалось загрузить заказы. Проверьте интернет."
        } finally {
            loading = false
        }
    }
    // Вернулись с оплаты или из заказа — статусы и долг могли измениться
    OnResume { reload++ }

    val list = orders.orEmpty()
    // Заказ завершён, когда у него общий статус и он конечный. Позиции в разных статусах — заказ ещё в работе.
    val (done, active) = list.partition { o -> o.statusId != null && o.statusId in finalIds }
    val shown = if (tab == 0) active else done

    Scaffold(topBar = { TopAppBar(title = { Text("Заказы") }) }) { padding ->
        Column(Modifier.padding(padding).fillMaxSize()) {
            TabRow(selectedTabIndex = tab) {
                Tab(tab == 0, { tab = 0 }, text = { Text("Активные (${active.size})") })
                Tab(tab == 1, { tab = 1 }, text = { Text("Завершённые (${done.size})") })
            }
            RefreshableContent(hasData = orders != null, loading = loading, error = error, onRefresh = { reload++ }) {
                if (shown.isEmpty()) EmptyState(if (tab == 0) "Активных заказов нет" else "Завершённых заказов нет")
                else LazyColumn(
                    modifier = Modifier.fillMaxSize(),
                    contentPadding = PaddingValues(12.dp),
                    verticalArrangement = Arrangement.spacedBy(10.dp)
                ) {
                    // Список есть, но обновить не вышло — говорим об этом, не пряча заказы
                    error?.let { e -> item { Text(e, color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.bodySmall) } }
                    items(shown, key = { it.number ?: it.hashCode().toString() }) { order ->
                        val payNumber = order.number
                        OrderRow(
                            order,
                            onPay = if (server.enabled && payNumber != null) {
                                { payOrder(ctx, scope, server, payNumber) }
                            } else null
                        ) {
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

@Composable
private fun OrderRow(order: OrderDto, onPay: (() -> Unit)? = null, onClick: () -> Unit) {
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
                    Text(order.sum?.let { formatRub(parseAbcpNumber(it)) }.orEmpty(), fontWeight = FontWeight.Bold)
                }
                val debt = parseAbcpNumber(order.debt)
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

/** Ссылку на оплату даёт наш сервер (cp/payment/token доступен только API-админу), открываем в браузере. */
fun payOrder(ctx: android.content.Context, scope: kotlinx.coroutines.CoroutineScope, server: AppServer, number: String) {
    android.widget.Toast.makeText(ctx, "Открываем оплату…", android.widget.Toast.LENGTH_SHORT).show()
    scope.launch {
        try {
            val url = server.payLink(number)
            ctx.startActivity(Intent(Intent.ACTION_VIEW, android.net.Uri.parse(url)))
        } catch (e: Exception) {
            android.widget.Toast.makeText(ctx, e.message ?: "Не удалось получить ссылку на оплату", android.widget.Toast.LENGTH_LONG).show()
        }
    }
}

/**
 * Плашка статуса: цвет из ABCP — только кружком. Текстом его не красим: в ABCP бывают жёлтые и
 * светло-зелёные статусы (#FFFF00, #E0F7AE), на светлом фоне такой текст не читается.
 */
@Composable
fun StatusChip(text: String, hex: String?) {
    val c = parseAbcpColor(hex) ?: MaterialTheme.colorScheme.primary
    Row(
        Modifier
            .background(MaterialTheme.colorScheme.surfaceVariant, RoundedCornerShape(8.dp))
            .padding(horizontal = 8.dp, vertical = 4.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        Box(Modifier.size(10.dp).background(c, androidx.compose.foundation.shape.CircleShape))
        Spacer(Modifier.width(6.dp))
        Text(text, style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurface)
    }
}

fun parseAbcpColor(hex: String?): Color? {
    val h = hex?.trim()?.removePrefix("#") ?: return null
    if (h.length != 6) return null
    return h.toLongOrNull(16)?.let { Color(0xFF000000 or it) }
}
