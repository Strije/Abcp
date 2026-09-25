package com.example.myapplication.abcp
import com.example.myapplication.ui.theme.AvtodrugTheme

import android.content.Intent
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.example.myapplication.SessionManager
import kotlinx.coroutines.launch

/** Оформление: оплата, самовывоз/доставка, офис, дата, комментарий → basket/order. */
@OptIn(ExperimentalMaterial3Api::class)
class CheckoutActivity : ComponentActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val shop = AbcpShop(SessionManager(this))

        setContent {
            AvtodrugTheme {
                val scope = rememberCoroutineScope()
                var loading by remember { mutableStateOf(true) }
                var sending by remember { mutableStateOf(false) }
                var error by remember { mutableStateOf<String?>(null) }
                var basket by remember { mutableStateOf<List<BasketItem>>(emptyList()) }
                var opts by remember { mutableStateOf<CheckoutOptions?>(null) }
                var done by remember { mutableStateOf<List<String>?>(null) }

                var payment by remember { mutableStateOf<String?>(null) }
                var method by remember { mutableStateOf<String?>(null) }
                var pickup by remember { mutableStateOf(true) }
                var office by remember { mutableStateOf<String?>(null) }
                var address by remember { mutableStateOf<String?>(null) }
                var date by remember { mutableStateOf<String?>(null) }
                var comment by remember { mutableStateOf("") }
                var reload by remember { mutableIntStateOf(0) }
                var showItems by remember { mutableStateOf(false) }

                LaunchedEffect(reload) {
                    loading = true
                    error = null
                    try {
                        basket = shop.basket()
                        val o = shop.checkoutOptions(basket)
                        opts = o
                        // Если вариант один — выбираем его сами
                        payment = o.payments.singleOrNull()?.id
                        method = o.shipmentMethods.singleOrNull()?.id
                        office = o.offices.singleOrNull()?.id
                        address = o.addresses.singleOrNull()?.id
                        date = o.dates.firstOrNull()?.date
                        pickup = o.addresses.isEmpty() || o.offices.isNotEmpty()
                    } catch (e: Exception) {
                        com.example.myapplication.Analytics.error("Оформление → варианты доставки", e)
                        error = e.message ?: "Ошибка загрузки"
                    } finally {
                        loading = false
                    }
                }

                val o = opts
                val missing = when {
                    o == null -> "…"
                    o.payments.isNotEmpty() && payment == null -> "Выберите способ оплаты"
                    o.shipmentMethods.isNotEmpty() && method == null -> "Выберите способ доставки"
                    pickup && o.offices.isNotEmpty() && office == null -> "Выберите офис самовывоза"
                    !pickup && address == null -> "Выберите адрес доставки"
                    o.dates.isNotEmpty() && date == null -> "Выберите дату"
                    else -> null
                }

                Scaffold(topBar = { TopAppBar(title = { Text("Оформление заказа") }) }) { padding ->
                    Box(Modifier.padding(padding).fillMaxSize()) {
                        when {
                            loading -> CircularProgressIndicator(Modifier.align(Alignment.Center))
                            done != null -> Column(
                                Modifier.align(Alignment.Center).padding(24.dp),
                                horizontalAlignment = Alignment.CenterHorizontally
                            ) {
                                Text("Заказ оформлен", style = MaterialTheme.typography.headlineSmall)
                                Text("№ " + done!!.joinToString(", "))
                                Spacer(Modifier.height(16.dp))
                                Button(onClick = {
                                    startActivity(Intent(this@CheckoutActivity, OrdersActivity::class.java))
                                    finish()
                                }) { Text("Мои заказы") }
                            }
                            o == null -> com.example.myapplication.ui.ErrorState(
                                error ?: "Не удалось загрузить варианты оформления", Modifier.align(Alignment.Center),
                                onRetry = { reload++ }
                            )
                            else -> Column(
                                Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp),
                                verticalArrangement = Arrangement.spacedBy(12.dp)
                            ) {
                                val total = basket.sumOf { it.price * it.quantity }
                                Text("${basket.size} поз. на ${formatRub(total)}", style = MaterialTheme.typography.titleMedium)
                                // Что заказываем — свёрнуто; позиции с замечанием ABCP (нет в наличии, цена) видны сразу
                                val problems = basket.filter { !it.errorMessage.isNullOrBlank() }
                                problems.forEach { b ->
                                    Text("⚠ ${b.brand} ${b.number}: ${b.errorMessage}", color = MaterialTheme.colorScheme.error,
                                        style = MaterialTheme.typography.bodySmall)
                                }
                                TextButton(onClick = { showItems = !showItems }, contentPadding = PaddingValues(0.dp)) {
                                    Text(if (showItems) "Скрыть состав заказа" else "Показать состав заказа")
                                }
                                if (showItems) basket.forEach { b ->
                                    Row(Modifier.fillMaxWidth()) {
                                        Text("${b.brand} ${b.number} × ${b.quantity}", Modifier.weight(1f), style = MaterialTheme.typography.bodySmall)
                                        Text(formatRub(b.price * b.quantity), style = MaterialTheme.typography.bodySmall)
                                    }
                                }

                                Choice("Оплата", o.payments, payment) { payment = it }
                                Choice("Способ доставки", o.shipmentMethods, method) { method = it }

                                if (o.addresses.isNotEmpty()) {
                                    Row {
                                        FilterChip(pickup, { pickup = true }, { Text("Самовывоз") })
                                        Spacer(Modifier.width(8.dp))
                                        FilterChip(!pickup, { pickup = false }, { Text("Доставка") })
                                    }
                                }
                                if (pickup) Choice("Офис самовывоза", o.offices, office) { office = it }
                                else Choice("Адрес доставки", o.addresses, address) { address = it }

                                Choice("Дата отгрузки", o.dates.map { IdName(it.date, it.name) }, date) { date = it }

                                OutlinedTextField(
                                    value = comment,
                                    onValueChange = { comment = it },
                                    label = { Text("Комментарий к заказу") },
                                    modifier = Modifier.fillMaxWidth()
                                )

                                error?.let { Text(it, color = MaterialTheme.colorScheme.error) }

                                Button(
                                    enabled = missing == null && !sending,
                                    modifier = Modifier.fillMaxWidth(),
                                    onClick = {
                                        sending = true
                                        error = null
                                        scope.launch {
                                            try {
                                                done = shop.placeOrder(
                                                    OrderChoice(
                                                        paymentId = payment,
                                                        shipmentMethodId = method,
                                                        addressId = if (pickup) "0" else address.orEmpty(),
                                                        officeId = if (pickup) office else null,
                                                        date = date,
                                                        comment = comment
                                                    ),
                                                    basketIds = basket.map { it.basketId }
                                                )
                                                CartState.refresh(shop)
                                                com.example.myapplication.Analytics.event("order_placed", mapOf("positions" to basket.size))
                                            } catch (e: Exception) {
                                                com.example.myapplication.Analytics.error("Оформление → подтвердить заказ", e)
                                                error = e.message ?: "Заказ не оформлен"
                                            } finally {
                                                sending = false
                                            }
                                        }
                                    }
                                ) { Text(if (sending) "Отправляем…" else missing ?: "Подтвердить заказ") }
                            }
                        }
                    }
                }
            }
        }
    }
}

/** Выбор одного варианта из списка ABCP; пустой список — блок не показываем. */
@Composable
private fun Choice(title: String, options: List<IdName>, selected: String?, onSelect: (String) -> Unit) {
    if (options.isEmpty()) return
    Column {
        Text(title, style = MaterialTheme.typography.titleSmall, fontWeight = FontWeight.Bold)
        options.forEach { opt ->
            Row(
                Modifier.fillMaxWidth()
                    .selectable(selected = opt.id == selected, onClick = { onSelect(opt.id) })
                    .padding(vertical = 4.dp),
                verticalAlignment = Alignment.CenterVertically
            ) {
                RadioButton(selected = opt.id == selected, onClick = { onSelect(opt.id) })
                Text(opt.name)
            }
        }
    }
}
