package com.example.myapplication.abcp
import com.example.myapplication.ui.theme.AvtodrugTheme

import androidx.compose.material3.ExperimentalMaterial3Api
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.Send
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.withStyle
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
                // История смены статусов — с нашего сервера, отдельно от заказа: карточка её не ждёт
                var history by remember { mutableStateOf<OrderHistory?>(null) }
                var historyLoading by remember { mutableStateOf(false) }
                // Открытый лист «что значит статус»: по позиции или по заказу целиком
                var statusSheet by remember { mutableStateOf<StatusSheetTarget?>(null) }

                LaunchedEffect(orderNumber, reload) {
                    if (!server.enabled) return@LaunchedEffect
                    val started = reload
                    historyLoading = true
                    try {
                        // Не разобралось — оставляем прошлую историю
                        parseOrderHistory(server.orderHistory(orderNumber))?.let { history = it }
                    } catch (e: CancellationException) {
                        throw e
                    } catch (_: Exception) {
                        // 404 (на сервере метода ещё нет), нет сети — карточка работает и без истории
                    } finally {
                        // Отменённый запрос не должен гасить признак загрузки у следующего
                        if (started == reload) historyLoading = false
                    }
                }

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
                                history = history,
                                onStatus = { p -> statusSheet = StatusSheetTarget(p) },
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

                statusSheet?.let { target ->
                    details?.let { d ->
                        // Заказ могли обновить, пока лист открыт — показываем позицию в свежем виде
                        val fresh = target.position?.let { old ->
                            d.positions.orEmpty().firstOrNull { !it.positionId.isNullOrBlank() && it.positionId == old.positionId } ?: old
                        }
                        StatusSheet(
                            details = d, position = fresh, history = history, historyLoading = historyLoading,
                            onDismiss = { statusSheet = null },
                            onChat = {
                                statusSheet = null
                                com.example.myapplication.ui.openChat(ctx)
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
    history: OrderHistory?,
    onStatus: (OrderPositionDto?) -> Unit,
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
    val serverStatuses = history?.statuses
    val stage = remember(details, serverStatuses) { orderStage(orderStatuses(details), serverStatuses) }
    val stages = history?.stages ?: DEFAULT_STAGES
    LazyColumn(
        modifier = Modifier.fillMaxSize(),
        contentPadding = PaddingValues(12.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp)
    ) {
        error?.let { e -> item { Text(e, color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.bodySmall) } }
        item {
            ElevatedCard(Modifier.fillMaxWidth()) {
                Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                    val lead = statusInfo(stage.lead ?: details.status, serverStatuses)
                    // Нажатие — что значит статус и как он менялся
                    // Позиции в разных статусах: общий статус ABCP («Готово к выдаче») обманывал бы — часть ещё едет
                    // (телефон 13.11, заказ из 9 позиций). Тогда пишем статус самой «отстающей» позиции.
                    val partlyReady = stage.stage in 1..3 && stage.ready > 0
                    StatusButton(
                        if (partlyReady) stage.lead ?: "Заказ в работе"
                        else details.status?.takeIf { it.isNotBlank() } ?: stage.lead ?: "Заказ в работе",
                        if (partlyReady) null else details.statusColor
                    ) { onStatus(null) }
                    if (stage.stage == 0) {
                        // Весь заказ в отказе или возврате — шкала этапов ни к чему
                        Text(
                            lead.hint.ifBlank { "Заказ отменён. Если это ошибка — напишите нам в чат." },
                            color = MaterialTheme.colorScheme.error
                        )
                    } else {
                        StageScale(stage.stage, stages.size)
                        // Подписи под точками на узком экране наезжали бы друг на друга — пишем только текущий этап
                        Text(
                            "Этап ${stage.stage} из ${stages.size}: ${stages.getOrNull(stage.stage - 1).orEmpty()}",
                            style = MaterialTheme.typography.titleSmall, fontWeight = FontWeight.Bold
                        )
                        if (lead.hint.isNotBlank()) Text(
                            lead.hint, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant
                        )
                    }
                    if (partlyReady) Text(
                        "Уже можно забрать: ${stage.ready} из ${stage.active} ${plural(stage.active, "позиции", "позиций", "позиций")}",
                        color = MaterialTheme.colorScheme.onPrimaryContainer, style = MaterialTheme.typography.bodyMedium,
                        modifier = Modifier.fillMaxWidth()
                            .background(MaterialTheme.colorScheme.primaryContainer, RoundedCornerShape(8.dp))
                            .padding(horizontal = 10.dp, vertical = 6.dp)
                    )
                    orderWarnings(stage).forEach { w ->
                        Text(
                            w, color = MaterialTheme.colorScheme.onErrorContainer, style = MaterialTheme.typography.bodyMedium,
                            modifier = Modifier.fillMaxWidth()
                                .background(MaterialTheme.colorScheme.errorContainer, RoundedCornerShape(8.dp))
                                .padding(horizontal = 10.dp, vertical = 6.dp)
                        )
                    }
                    // Когда приедет всё: самая поздняя дата среди позиций, которые ещё едут
                    if (stage.active > 1) orderExpectedAt(details, serverStatuses)?.let { at ->
                        InfoLine(if (isPastDay(at)) "Весь заказ ожидался" else "Весь заказ ожидается", dayText(at), bold = true)
                    }
                    HorizontalDivider(Modifier.padding(vertical = 4.dp))
                    details.date?.takeIf { it.isNotBlank() }?.let { InfoLine("Оформлен", formatAbcpDate(it)) }
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
                    (office ?: addr)?.let { InfoLine(if (office != null) "Где забрать" else "Доставка", it) }
                    // Дата, которую клиент сам выбрал при оформлении. У завершённого заказа она уже ни о чём
                    if (stage.stage in 1 until stages.size) details.shipmentDate?.trim()?.take(10)
                        ?.let { com.example.myapplication.shipmentDateLabel(it, System.currentTimeMillis()) }
                        ?.let { InfoLine(if (office != null) "Вы планировали приехать" else "Вы выбрали дату", it) }
                    details.paymentType?.takeIf { it.isNotBlank() }?.let { InfoLine("Оплата", it) }
                    details.comment?.takeIf { it.isNotBlank() }?.let { InfoLine("Ваш комментарий", it) }
                    Row(verticalAlignment = Alignment.Bottom) {
                        val sum = parseAbcpNumber(details.sum)
                        val debt = parseAbcpNumber(details.debt)
                        Column(Modifier.weight(1f)) {
                            Text("Сумма", style = MaterialTheme.typography.bodySmall)
                            Text(details.sum?.let { formatRub(sum) }.orEmpty(), fontWeight = FontWeight.Bold)
                        }
                        if (debt > 0) {
                            Column(horizontalAlignment = Alignment.End) {
                                Text("К оплате", style = MaterialTheme.typography.bodySmall)
                                Text(formatRub(debt), color = MaterialTheme.colorScheme.error, fontWeight = FontWeight.Bold)
                            }
                            if (onPay != null) {
                                Spacer(Modifier.width(12.dp))
                                Button(onClick = onPay, enabled = !payInProgress) { Text("Оплатить") }
                            }
                        } else if (!details.debt.isNullOrBlank() && sum > 0 && stage.stage != 0) {
                            // Долг 0 пишем, только если ABCP его прислал: без поля «Оплачен» было бы догадкой
                            Text("Оплачен", color = MaterialTheme.colorScheme.primary, fontWeight = FontWeight.Bold)
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
                    // Поставщик подтвердил меньше, чем заказано. Ноль не считаем: до подтверждения там может быть пусто
                    val confirmed = wholeQuantity(p.quantity)
                    val ordered = wholeQuantity(p.quantityOrdered)
                    if (confirmed != null && ordered != null && confirmed in 1 until ordered) Text(
                        "Подтверждено $confirmed из $ordered шт.",
                        color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.bodyMedium
                    )
                    val info = statusInfo(p.status, serverStatuses)
                    p.status?.takeIf { it.isNotBlank() }?.let { s ->
                        // Нажатие — что значит статус и как он менялся
                        StatusButton(s, p.statusColor) { onStatus(p) }
                        statusDateText(p.statusDate).takeIf { it.isNotEmpty() }?.let {
                            Text("с $it", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                        }
                    }
                    // В заказе из нескольких позиций у каждой свой срок — без него не понять, что и когда приедет.
                    // Деталь уже в магазине, выдана или отказана — срок больше не нужен
                    if (p.status?.let(::isClosedStatus) != true && info.kind != KIND_STOP && info.kind != KIND_DONE && info.stage < 4) {
                        expectedText(details.date, p.deadline, p.deadlineMax)?.let {
                            val late = expectedRange(details.date, p.deadline, p.deadlineMax)?.let { r -> isPastDay(r.to) } == true
                            Text(
                                if (late) it.replace("Ожидается", "Ожидалась") else it,
                                style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant
                            )
                        }
                    }
                    p.comment?.takeIf { it.isNotBlank() }?.let {
                        Text("Ваш комментарий: $it", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
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
    val r = expectedRange(orderDate, deadline, deadlineMax) ?: return null
    val day = java.text.SimpleDateFormat("yyyy-MM-dd", java.util.Locale.US)
    val from = formatAbcpDate(day.format(java.util.Date(r.from)), r.start)
    val to = formatAbcpDate(day.format(java.util.Date(r.to)), r.start)
    return if (to == from) "Ожидается $from" else "Ожидается $from – $to"
}

// ---------- статусы, этапы и сроки в карточке ----------

/** Чей статус открыт в листе: позиции или (position = null) заказа целиком. */
private class StatusSheetTarget(val position: OrderPositionDto?)

/** Статусы позиций; позиций в ответе нет — общий статус заказа. */
private fun orderStatuses(details: OrderDetailsDto): List<String> =
    details.positions.orEmpty().mapNotNull { it.status?.takeIf { s -> s.isNotBlank() } }
        .ifEmpty { listOfNotNull(details.status?.takeIf { it.isNotBlank() }) }

/** Когда приедет последняя из позиций, которые ещё едут (миллисекунды). null — сроков нет или ждать уже нечего. */
private fun orderExpectedAt(details: OrderDetailsDto, serverStatuses: Map<String, StatusInfo>?): Long? =
    details.positions.orEmpty().mapNotNull { p ->
        val info = statusInfo(p.status, serverStatuses)
        if (info.kind == KIND_STOP || info.kind == KIND_DONE || info.stage >= 4) null
        else expectedRange(details.date, p.deadline, p.deadlineMax)?.to
    }.maxOrNull()

private fun dayText(millis: Long): String =
    formatAbcpDate(java.text.SimpleDateFormat("yyyy-MM-dd", java.util.Locale.US).format(java.util.Date(millis)))

/** День уже прошёл — срок сорван, «ожидается» писать нельзя. */
private fun isPastDay(millis: Long, now: Long = System.currentTimeMillis()): Boolean {
    val today = java.util.Calendar.getInstance().apply {
        timeInMillis = now
        set(java.util.Calendar.HOUR_OF_DAY, 0); set(java.util.Calendar.MINUTE, 0)
        set(java.util.Calendar.SECOND, 0); set(java.util.Calendar.MILLISECOND, 0)
    }.timeInMillis
    return millis < today
}

/** «2», «2.00» → 2; пусто или не число — null. */
private fun wholeQuantity(s: String?): Int? =
    s?.filterNot { it == ' ' || it == ' ' }?.replace(',', '.')?.toDoubleOrNull()?.toInt()

/** Дата смены статуса для человека; ABCP вместо пустой даты может прислать нули. */
private fun statusDateText(raw: String?): String {
    val s = raw?.trim().orEmpty()
    return if (s.isEmpty() || s.startsWith("0000")) "" else formatAbcpDate(s)
}

/** Шаги позиции от нового к старому: статус и когда поставлен. Истории нет — только текущий статус. */
private fun positionSteps(p: OrderPositionDto, history: OrderHistory?): List<Pair<String, String>> {
    val known = p.positionId?.let { history?.steps?.get(it) }.orEmpty()
    val out = known.asReversed().map { it.status to formatUnixTime(it.ts) }.toMutableList()
    val current = p.status?.trim().orEmpty()
    // Сервер мог ещё не узнать о последней смене — текущий статус из ABCP ставим первым
    if (current.isNotEmpty() && !known.lastOrNull()?.status.equals(current, ignoreCase = true)) {
        out.add(0, current to statusDateText(p.statusDate))
    }
    return out
}

/** Строка «подпись: значение» одним текстом — при крупном шрифте переносится, а не наезжает. */
@Composable
private fun InfoLine(label: String, value: String, bold: Boolean = false) {
    val muted = MaterialTheme.colorScheme.onSurfaceVariant
    Text(
        buildAnnotatedString {
            withStyle(SpanStyle(color = muted)) { append("$label: ") }
            if (bold) withStyle(SpanStyle(fontWeight = FontWeight.Bold)) { append(value) } else append(value)
        },
        style = MaterialTheme.typography.bodyMedium
    )
}

/** Плашка статуса, на которую можно нажать: область не меньше 48dp и «›» — видно, что за ней что-то есть. */
@Composable
private fun StatusButton(text: String, hex: String?, onClick: () -> Unit) {
    Row(
        Modifier
            .heightIn(min = 48.dp)
            .clip(RoundedCornerShape(8.dp))
            .clickable(onClickLabel = "Что значит статус и история", role = Role.Button, onClick = onClick),
        verticalAlignment = Alignment.CenterVertically
    ) {
        // Длинный статус переносится внутри плашки, а «›» остаётся на экране
        Box(Modifier.weight(1f, fill = false)) { StatusChip(text, hex) }
        Text(
            "›", style = MaterialTheme.typography.titleLarge, color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.padding(horizontal = 10.dp)
        )
    }
}

/** Шкала этапов: точки, соединённые линией. Пройденные и текущий — цветом, текущий крупнее. */
@Composable
private fun StageScale(stage: Int, count: Int) {
    val on = MaterialTheme.colorScheme.primary
    val off = MaterialTheme.colorScheme.outline
    Row(Modifier.fillMaxWidth().padding(vertical = 4.dp), verticalAlignment = Alignment.CenterVertically) {
        for (i in 1..count) {
            if (i > 1) Box(Modifier.weight(1f).height(3.dp).background(if (i <= stage) on else off))
            Box(Modifier.size(if (i == stage) 18.dp else 12.dp).background(if (i <= stage) on else off, CircleShape))
        }
    }
}

/** Шаг истории: точка, статус и время. Первый (текущий) выделен. */
@Composable
private fun StepRow(status: String, time: String, current: Boolean) {
    Row(verticalAlignment = Alignment.Top) {
        Box(
            Modifier.padding(top = 6.dp).size(8.dp)
                .background(if (current) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.outline, CircleShape)
        )
        Spacer(Modifier.width(10.dp))
        Column(Modifier.weight(1f)) {
            Text(status, style = MaterialTheme.typography.bodyMedium, fontWeight = if (current) FontWeight.Bold else FontWeight.Normal)
            if (time.isNotEmpty()) Text(time, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}

/**
 * Лист «что значит статус»: название, подсказка, история смены. position = null — по заказу целиком
 * (история по всем позициям). Истории с сервера нет — показываем то, что знает ABCP: текущий статус и его дату.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun StatusSheet(
    details: OrderDetailsDto,
    position: OrderPositionDto?,
    history: OrderHistory?,
    historyLoading: Boolean,
    onDismiss: () -> Unit,
    onChat: () -> Unit
) {
    val serverStatuses = history?.statuses
    val positions = details.positions.orEmpty()
    val shown = if (position != null) listOf(position) else positions
    val stage = orderStage(orderStatuses(details), serverStatuses)
    val stages = history?.stages ?: DEFAULT_STAGES
    val name = if (position != null) position.status.orEmpty()
        else details.status?.takeIf { it.isNotBlank() } ?: stage.lead ?: "Заказ в работе"
    val info = statusInfo(if (position != null) position.status else stage.lead ?: details.status, serverStatuses)
    // Менеджер нужен, когда от клиента что-то ждут или что-то пошло не так
    val needChat = if (position != null) info.kind == KIND_ACTION || info.kind == KIND_WARN || info.kind == KIND_STOP
        else stage.stage == 0 || orderWarnings(stage).isNotEmpty()
    // Полная история есть, если сервер прислал шаги хотя бы по одной из показанных позиций
    val hasSteps = shown.any { p -> !p.positionId?.let { history?.steps?.get(it) }.isNullOrEmpty() }
    val created = history?.created?.takeIf { it > 0 }?.let { formatUnixTime(it) }
        ?: details.date?.takeIf { it.isNotBlank() }?.let { formatAbcpDate(it) }.orEmpty()

    ModalBottomSheet(onDismissRequest = onDismiss) {
        Column(
            Modifier.fillMaxWidth().verticalScroll(rememberScrollState()).navigationBarsPadding()
                .padding(horizontal = 20.dp).padding(bottom = 28.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp)
        ) {
            Text(
                if (position != null) "Статус позиции" else "Статус заказа",
                style = MaterialTheme.typography.labelLarge, color = MaterialTheme.colorScheme.onSurfaceVariant
            )
            if (position != null) {
                val title = "${position.brand.orEmpty()} ${position.number.orEmpty()}".trim()
                if (title.isNotEmpty()) Text(title, style = MaterialTheme.typography.bodyMedium)
            }
            Text(name, style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.Bold)
            if (position == null && stage.stage != 0) Text(
                "Этап ${stage.stage} из ${stages.size}: ${stages.getOrNull(stage.stage - 1).orEmpty()}",
                style = MaterialTheme.typography.titleSmall
            )
            if (info.hint.isNotBlank()) Text(
                info.hint, style = MaterialTheme.typography.bodyMedium,
                color = if (info.kind == KIND_STOP) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.onSurface
            )
            if (position == null) orderWarnings(stage).forEach { w ->
                Text(w, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.error)
            }
            if (needChat) Button(onClick = onChat, modifier = Modifier.fillMaxWidth()) { Text("Написать менеджеру") }

            HorizontalDivider()
            Text("История", style = MaterialTheme.typography.titleSmall, fontWeight = FontWeight.Bold)
            shown.forEach { p ->
                if (position == null) {
                    // В истории заказа шаги сгруппированы по позициям
                    val title = "${p.brand.orEmpty()} ${p.number.orEmpty()}".trim()
                    if (title.isNotEmpty()) Text(title, style = MaterialTheme.typography.labelLarge, modifier = Modifier.padding(top = 4.dp))
                }
                positionSteps(p, history).forEachIndexed { i, (status, time) -> StepRow(status, time, current = i == 0) }
            }
            StepRow("Заказ оформлен", created, current = false)
            when {
                hasSteps -> {}
                historyLoading -> Text(
                    "Загружаем историю…", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant
                )
                else -> Text(
                    "Полная история статусов появится у новых заказов.",
                    style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant
                )
            }
        }
    }
}
