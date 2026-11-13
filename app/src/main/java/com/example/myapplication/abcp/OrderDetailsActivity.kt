package com.example.myapplication.abcp
import com.example.myapplication.ui.theme.AvtodrugTheme

import androidx.compose.material3.ExperimentalMaterial3Api
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.Send
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.CancellationException
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
                var repeating by remember { mutableStateOf(false) }
                var repeatLines by remember { mutableStateOf<List<RepeatLine>?>(null) }
                // Выбранные позиции кладутся в корзину — второй раз «Повторить заказ» нажать нельзя (задвоит)
                var adding by remember { mutableStateOf(false) }
                // Запрос на отмену позиции уже ушёл — второй раз, пока нет ответа, не отправляем
                var cancelling by remember { mutableStateOf(false) }

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
                    } catch (e: CancellationException) {
                        throw e
                    } catch (e: Exception) {
                        com.example.myapplication.Analytics.error("Заказ → загрузка", e)
                        error = "Не удалось загрузить заказ. Проверьте интернет."
                    } finally {
                        loading = false
                    }
                }
                // Вернулись с оплаты — долг и статусы могли измениться
                com.example.myapplication.ui.OnResume { reload++ }

                Scaffold(topBar = { TopAppBar(navigationIcon = { com.example.myapplication.ui.BackButton() }, title = { Text("Заказ № $orderNumber") }, actions = {
                    // Вопрос по заказу — сразу менеджеру, не возвращаясь на главную
                    IconButton(onClick = { com.example.myapplication.ui.openChat(ctx) }) {
                        Icon(Icons.AutoMirrored.Filled.Send, "Написать менеджеру")
                    }
                    com.example.myapplication.ui.SearchAction()
                }) }) { padding ->
                    // Первая загрузка — спиннер, дальше заказ на экране, обновление тихое («потянуть вниз»)
                    com.example.myapplication.ui.RefreshableContent(
                        hasData = details != null, loading = loading, error = error,
                        onRefresh = { reload++ }, modifier = Modifier.padding(padding)
                    ) {
                            OrderDetailsContent(
                                details = details!!,
                                // Заказ на экране, а обновить не вышло — говорим об этом, не пряча его
                                error = error,
                                cancelling = cancelling,
                                // Отказаться можно, только пока позицию не взяли в работу (статус «Ожидает обработки»);
                                // дальше деталь уже заказана у поставщика — только через менеджера
                                canCancel = { p ->
                                    !p.positionId.isNullOrBlank() && p.statusId !in finalIds &&
                                        "ожидает обработки" in p.status?.lowercase().orEmpty()
                                },
                                onCancel = { toCancel = it },
                                onPay = if (server.enabled) {
                                    { payOrder(ctx, scope, server, orderNumber) }
                                } else null,
                                repeating = repeating,
                                adding = adding,
                                onRepeat = {
                                    if (!repeating && !adding) {
                                        repeating = true
                                        scope.launch {
                                            try {
                                                val list = details?.positions.orEmpty().filter { !it.number.isNullOrBlank() }.map {
                                                    RepeatSource(
                                                        it.brand.orEmpty(), it.number.orEmpty(),
                                                        (it.quantity ?: it.quantityOrdered)?.let(::parseAbcpNumber)?.toInt() ?: 1,
                                                        (it.priceInSiteCurrency ?: it.price)?.let(::parseAbcpNumber) ?: 0.0
                                                    )
                                                }
                                                repeatLines = shop.findRepeat(list)
                                            } finally {
                                                repeating = false
                                            }
                                        }
                                    }
                                },
                                onBuyAgain = { p ->
                                    ctx.startActivity(
                                        android.content.Intent(ctx, com.example.myapplication.SearchActivity::class.java)
                                            .putExtra(com.example.myapplication.SearchActivity.EXTRA_NUMBER, p.number.orEmpty())
                                            .putExtra(com.example.myapplication.SearchActivity.EXTRA_BRAND, p.brand.orEmpty())
                                    )
                                }
                            )
                    }
                }

                repeatLines?.let { lines ->
                    RepeatOrderDialog(
                        lines = lines,
                        onDismiss = { repeatLines = null },
                        onConfirm = { chosen ->
                            repeatLines = null
                            adding = true
                            scope.launch {
                                try {
                                    val failed = shop.addRepeat(chosen)
                                    CartState.refresh(shop)
                                    com.example.myapplication.Analytics.event("repeat_order", mapOf("positions" to chosen.size))
                                    android.widget.Toast.makeText(
                                        ctx,
                                        if (failed.isEmpty()) "Добавлено в корзину: ${chosen.size}" else "Не добавилось: ${failed.joinToString()}",
                                        android.widget.Toast.LENGTH_LONG
                                    ).show()
                                    if (failed.size < chosen.size) ctx.startActivity(android.content.Intent(ctx, CartActivity::class.java))
                                } finally {
                                    adding = false
                                }
                            }
                        }
                    )
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
                            Button(enabled = !cancelling, onClick = {
                                toCancel = null
                                if (cancelling) return@Button
                                cancelling = true
                                scope.launch {
                                    try {
                                        val msg = shop.cancelPosition(p.positionId!!)
                                        android.widget.Toast.makeText(ctx, msg, android.widget.Toast.LENGTH_LONG).show()
                                        reload++
                                    } catch (e: CancellationException) {
                                        throw e
                                    } catch (e: Exception) {
                                        com.example.myapplication.Analytics.error("Заказ → отмена позиции", e)
                                        android.widget.Toast.makeText(
                                            ctx,
                                            com.example.myapplication.ui.userMessage(e, "Не удалось отправить запрос на отмену. Попробуйте ещё раз или напишите менеджеру."),
                                            android.widget.Toast.LENGTH_LONG
                                        ).show()
                                    } finally {
                                        cancelling = false
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
    error: String?,
    cancelling: Boolean,
    canCancel: (OrderPositionDto) -> Boolean,
    onCancel: (OrderPositionDto) -> Unit,
    onPay: (() -> Unit)?,
    repeating: Boolean,
    adding: Boolean,
    onRepeat: () -> Unit,
    onBuyAgain: (OrderPositionDto) -> Unit
) {
    val positions = details.positions.orEmpty()
    LazyColumn(
        modifier = Modifier.fillMaxSize(),
        contentPadding = PaddingValues(12.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp)
    ) {
        error?.let { e -> item { Text(e, color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.bodySmall) } }
        item {
            ElevatedCard(Modifier.fillMaxWidth()) {
                Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                    details.status?.let { StatusChip(it, details.statusColor) }
                    Text("от ${formatAbcpDate(details.date)}", style = MaterialTheme.typography.bodySmall)
                    // Адрес магазина из настроек вместо «Первый офис» (как на оформлении)
                    // Номера офиса в ответе может не быть — тогда по названию ABCP: «Первый офис» — первый магазин в настройках
                    val officeRaw = details.deliveryOffice?.takeIf { it.isNotBlank() }
                    val stores = com.example.myapplication.AppConfig.stores
                    val office = details.deliveryOfficeId?.let { com.example.myapplication.AppConfig.officeName(it) }
                        ?: when {
                            officeRaw == null -> null
                            officeRaw.contains("Первый", ignoreCase = true) -> stores.getOrNull(0)?.address
                            officeRaw.contains("Второй", ignoreCase = true) -> stores.getOrNull(1)?.address
                            else -> null
                        } ?: officeRaw
                    val addr = details.deliveryAddress?.takeIf { it.isNotBlank() }
                    (office ?: addr)?.let { Text(if (office != null) "Самовывоз: $it" else "Доставка: $it") }
                    Row(verticalAlignment = Alignment.Bottom) {
                        Column(Modifier.weight(1f)) {
                            Text("Сумма", style = MaterialTheme.typography.bodySmall)
                            Text(details.sum?.let { formatRub(parseAbcpNumber(it)) }.orEmpty(), fontWeight = FontWeight.Bold)
                        }
                        val debt = parseAbcpNumber(details.debt)
                        if (debt > 0) {
                            Column(horizontalAlignment = Alignment.End) {
                                Text("Долг", style = MaterialTheme.typography.bodySmall)
                                Text(formatRub(debt), color = MaterialTheme.colorScheme.error, fontWeight = FontWeight.Bold)
                            }
                            if (onPay != null) {
                                Spacer(Modifier.width(12.dp))
                                Button(onClick = onPay, enabled = !payInProgress) { Text("Оплатить") }
                            }
                        }
                    }
                }
            }
        }
        item {
            // Для СТО и таксопарков: тот же набор — в корзину одним нажатием
            if (positions.isNotEmpty()) OutlinedButton(onClick = onRepeat, enabled = !repeating && !adding, modifier = Modifier.fillMaxWidth()) {
                if (repeating || adding) {
                    CircularProgressIndicator(Modifier.size(18.dp), strokeWidth = 2.dp)
                    Spacer(Modifier.width(8.dp))
                }
                Text(
                    when {
                        adding -> "Добавляем в корзину…"
                        repeating -> "Ищем актуальные предложения…"
                        else -> "Повторить заказ"
                    }
                )
            }
        }
        item { Text("Позиции (${positions.size})", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold) }
        if (positions.isEmpty()) item { Text("Позиции отсутствуют") }
        items(positions) { p ->
            ElevatedCard(Modifier.fillMaxWidth()) {
                Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                    // Нажатие на номер — цены и аналоги этой детали
                    Text(
                        "${p.brand.orEmpty()} ${p.number.orEmpty()}".trim().ifEmpty { "-" }, style = MaterialTheme.typography.titleSmall,
                        color = if (p.number.isNullOrBlank()) androidx.compose.ui.graphics.Color.Unspecified else MaterialTheme.colorScheme.primary,
                        modifier = Modifier.clickable(enabled = !p.number.isNullOrBlank()) { onBuyAgain(p) }
                    )
                    p.description?.takeIf { it.isNotBlank() }?.let { Text(it, style = MaterialTheme.typography.bodyMedium) }
                    val qty = p.quantity ?: p.quantityOrdered ?: "-"
                    val price = (p.priceInSiteCurrency ?: p.price)?.let(::parseAbcpNumber)?.takeIf { it > 0 }
                    Text("$qty шт." + (price?.let { " × ${formatRub(it)}" } ?: ""))
                    p.status?.let { StatusChip(it, p.statusColor) }
                    // В заказе из нескольких позиций у каждой свой срок — без него не понять, что и когда приедет
                    if (p.status?.let(::isClosedStatus) != true) expectedText(details.date, p.deadline, p.deadlineMax)?.let {
                        Text(it, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                    p.commentAnswer?.takeIf { it.isNotBlank() }?.let {
                        Text("Ответ менеджера: $it", style = MaterialTheme.typography.bodySmall)
                    }
                    Row {
                        // Та же деталь ещё раз — сразу к ценам по номеру
                        if (!p.number.isNullOrBlank()) TextButton(onClick = { onBuyAgain(p) }, contentPadding = PaddingValues(0.dp)) {
                            Text("Купить ещё")
                        }
                        Spacer(Modifier.weight(1f))
                        if (canCancel(p)) {
                            TextButton(onClick = { onCancel(p) }, enabled = !cancelling, contentPadding = PaddingValues(0.dp)) {
                                Text("Отменить позицию", color = MaterialTheme.colorScheme.error)
                            }
                        }
                    }
                }
            }
        }
    }
}

/** Предпросмотр «Повторить заказ»: старая цена → сегодняшняя, срок; галочками выбираем, что положить в корзину. */
@Composable
private fun RepeatOrderDialog(lines: List<RepeatLine>, onDismiss: () -> Unit, onConfirm: (List<RepeatLine>) -> Unit) {
    // По умолчанию отмечено всё, что есть в продаже
    var checked by remember { mutableStateOf(lines.indices.filter { lines[it].offer != null }.toSet()) }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Повторить заказ") },
        text = {
            LazyColumn(verticalArrangement = Arrangement.spacedBy(6.dp)) {
                item {
                    Text("Цены и сроки — сегодняшние. Снимите галочку с того, что не нужно.",
                        style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
                items(lines.size) { i ->
                    val l = lines[i]
                    val o = l.offer
                    Row(verticalAlignment = Alignment.Top) {
                        Checkbox(
                            checked = i in checked, enabled = o != null,
                            onCheckedChange = { on -> checked = if (on) checked + i else checked - i }
                        )
                        Column(Modifier.weight(1f).padding(top = 12.dp)) {
                            Text("${l.source.title} × ${l.qty}", style = MaterialTheme.typography.bodyMedium, fontWeight = FontWeight.SemiBold)
                            if (o == null) {
                                // Сбой связи — не «нет в продаже»: говорим как есть
                                Text(
                                    if (l.checkFailed) "Не удалось проверить наличие — попробуйте позже" else "Сейчас нет в продаже",
                                    color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.bodySmall
                                )
                            } else {
                                val old = l.source.oldPrice
                                val diff = if (old > 0) o.price - old else 0.0
                                Text(
                                    (if (old > 0 && kotlin.math.abs(diff) >= 1) "${formatRub(old)} → " else "") + formatRub(o.price) + " · " + o.deliveryText(),
                                    style = MaterialTheme.typography.bodySmall,
                                    color = if (diff >= 1) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.onSurface
                                )
                            }
                        }
                    }
                }
            }
        },
        confirmButton = {
            val chosen = checked.sorted().map { lines[it] }
            Button(enabled = chosen.isNotEmpty(), onClick = { onConfirm(chosen) }) {
                Text("В корзину (${chosen.size}) · ${formatRub(chosen.sumOf { it.offer!!.price * it.qty })}")
            }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text("Отмена") } }
    )
}

/**
 * Когда ждать позицию заказа: дата заказа + срок поставки в часах (ABCP отдаёт его у каждой позиции).
 * «16 нояб.» или «16–18 нояб.»; срок 0 — товар был в магазине. Нет даты или срока — ничего не пишем.
 */
internal fun expectedText(orderDate: String?, deadline: String?, deadlineMax: String?): String? {
    val hours = deadline?.trim()?.toDoubleOrNull()?.toInt() ?: return null
    if (hours <= 0) return "В наличии в магазине"
    val start = runCatching {
        java.text.SimpleDateFormat("yyyy-MM-dd HH:mm:ss", java.util.Locale.US).parse(orderDate.orEmpty().trim())
    }.getOrNull()?.time ?: return null
    val day = java.text.SimpleDateFormat("yyyy-MM-dd", java.util.Locale.US)
    val from = formatAbcpDate(day.format(java.util.Date(start + hours * 3_600_000L)), start)
    val max = deadlineMax?.trim()?.toDoubleOrNull()?.toInt() ?: 0
    val to = if (max > hours) formatAbcpDate(day.format(java.util.Date(start + max * 3_600_000L)), start) else from
    return if (to == from) "Ожидается $from" else "Ожидается $from – $to"
}
