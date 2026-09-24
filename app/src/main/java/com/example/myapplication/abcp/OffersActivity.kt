package com.example.myapplication.abcp
import com.example.myapplication.ui.theme.AvtodrugTheme
import com.example.myapplication.ui.theme.DeliveryColors

import android.os.Bundle
import android.widget.Toast
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
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.example.myapplication.SessionManager
import kotlinx.coroutines.launch

/** Карточка номера: «Наличие» (сам номер) и «Аналоги» (группами по бренду+номеру). */
@OptIn(ExperimentalMaterial3Api::class)
class OffersActivity : ComponentActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        val brand = intent.getStringExtra(EXTRA_BRAND).orEmpty()
        val number = intent.getStringExtra(EXTRA_NUMBER).orEmpty()
        val description = intent.getStringExtra(EXTRA_DESCRIPTION).orEmpty()
        val shop = AbcpShop(SessionManager(this))

        setContent {
            AvtodrugTheme {
                val scope = rememberCoroutineScope()
                var loading by remember { mutableStateOf(true) }
                var error by remember { mutableStateOf<String?>(null) }
                var offers by remember { mutableStateOf<List<Offer>>(emptyList()) }
                var tab by remember { mutableIntStateOf(0) }
                var picked by remember { mutableStateOf<Offer?>(null) }

                LaunchedEffect(Unit) {
                    try {
                        offers = shop.offers(number, brand)
                    } catch (e: Exception) {
                        error = e.message ?: "Ошибка загрузки"
                    } finally {
                        loading = false
                    }
                }

                val fix = cleanNumber(number)
                val (own, analogs) = offers.partition {
                    it.numberFix.equals(fix, ignoreCase = true) && it.brand.equals(brand, ignoreCase = true)
                }
                val analogGroups = analogs.groupBy { "${it.brand} ${it.number}" }

                Scaffold(
                    topBar = {
                        TopAppBar(
                            title = {
                                Column {
                                    Text("$brand $number", maxLines = 1, overflow = TextOverflow.Ellipsis)
                                    val d = description.ifBlank { own.firstOrNull()?.description.orEmpty() }
                                    if (d.isNotBlank()) Text(d, style = MaterialTheme.typography.bodySmall, maxLines = 1)
                                }
                            },
                            actions = { CartIconButton() }
                        )
                    }
                ) { padding ->
                    Column(Modifier.padding(padding).fillMaxSize()) {
                        TabRow(selectedTabIndex = tab) {
                            Tab(tab == 0, { tab = 0 }, text = { Text("Наличие (${own.size})") })
                            Tab(tab == 1, { tab = 1 }, text = { Text("Аналоги (${analogs.size})") })
                        }
                        when {
                            loading -> Box(Modifier.fillMaxSize()) { CircularProgressIndicator(Modifier.align(Alignment.Center)) }
                            error != null -> Text(error!!, color = MaterialTheme.colorScheme.error, modifier = Modifier.padding(16.dp))
                            tab == 0 && own.isEmpty() -> Text(
                                "По самому номеру предложений нет — посмотрите аналоги.",
                                Modifier.padding(16.dp)
                            )
                            tab == 1 && analogs.isEmpty() -> Text("Аналогов не найдено", Modifier.padding(16.dp))
                            else -> LazyColumn(Modifier.fillMaxSize()) {
                                if (tab == 0) {
                                    items(own) { o -> OfferRow(o) { picked = o } }
                                } else {
                                    analogGroups.forEach { (title, list) ->
                                        item {
                                            Column(Modifier.padding(start = 16.dp, end = 16.dp, top = 14.dp, bottom = 4.dp)) {
                                                Text(title, style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
                                                val d = list.first().description
                                                if (d.isNotBlank()) Text(d, style = MaterialTheme.typography.bodySmall)
                                            }
                                        }
                                        items(list) { o -> OfferRow(o) { picked = o } }
                                    }
                                }
                            }
                        }
                    }
                }

                picked?.let { o ->
                    AddToCartDialog(
                        offer = o,
                        onDismiss = { picked = null },
                        onConfirm = { qty ->
                            picked = null
                            scope.launch {
                                try {
                                    shop.addToBasket(o, qty)
                                    CartState.refresh(shop)
                                    Toast.makeText(this@OffersActivity, "Добавлено в корзину", Toast.LENGTH_SHORT).show()
                                } catch (e: Exception) {
                                    Toast.makeText(this@OffersActivity, e.message ?: "Ошибка", Toast.LENGTH_LONG).show()
                                }
                            }
                        }
                    )
                }
            }
        }
    }

    companion object {
        const val EXTRA_BRAND = "extra_brand"
        const val EXTRA_NUMBER = "extra_number"
        const val EXTRA_DESCRIPTION = "extra_description"
    }
}

@Composable
private fun OfferRow(o: Offer, onClick: () -> Unit) {
    Row(
        Modifier.fillMaxWidth().clickable(onClick = onClick).padding(horizontal = 16.dp, vertical = 10.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        Column(Modifier.weight(1f)) {
            Text(formatRub(o.price), style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
            val pack = if (o.packing > 1) " (партия ${o.packing} шт.)" else ""
            Text(formatAvailability(o.availability) + pack, style = MaterialTheme.typography.bodySmall)
        }
        Column(horizontalAlignment = Alignment.End) {
            Text(
                formatDelivery(o.deliveryHours, o.deliveryHoursMax),
                style = MaterialTheme.typography.titleSmall,
                color = when {
                    o.deliveryHours <= 0 -> DeliveryColors.today
                    o.deliveryHours <= 72 -> DeliveryColors.soon
                    else -> DeliveryColors.later
                }
            )
            if (o.supplier.isNotBlank()) {
                Text(o.supplier, style = MaterialTheme.typography.bodySmall, maxLines = 1, overflow = TextOverflow.Ellipsis)
            }
        }
    }
    HorizontalDivider()
}

/** Шторка снизу: количество с учётом кратности и итог — карточка товара остаётся на месте. */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun AddToCartDialog(offer: Offer, onDismiss: () -> Unit, onConfirm: (Int) -> Unit) {
    val step = offer.packing
    val max = if (offer.availability > 0) offer.availability else Int.MAX_VALUE
    var qty by remember { mutableIntStateOf(step) }
    ModalBottomSheet(onDismissRequest = onDismiss) {
        Column(
            Modifier.fillMaxWidth().padding(horizontal = 20.dp).padding(bottom = 28.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp)
        ) {
            Text("${offer.brand} ${offer.number}", style = MaterialTheme.typography.titleLarge)
            if (offer.description.isNotBlank()) Text(offer.description, style = MaterialTheme.typography.bodyMedium)
            Text(
                "${formatRub(offer.price)} · ${formatDelivery(offer.deliveryHours, offer.deliveryHoursMax)}",
                style = MaterialTheme.typography.titleMedium
            )
            if (offer.noReturn) Text("Без возврата", color = MaterialTheme.colorScheme.error)
            Row(verticalAlignment = Alignment.CenterVertically) {
                FilledTonalButton(onClick = { qty = (qty - step).coerceAtLeast(step) }) { Text("−") }
                Text("$qty шт.", Modifier.padding(horizontal = 20.dp), style = MaterialTheme.typography.titleLarge)
                FilledTonalButton(onClick = { if (qty + step <= max) qty += step }) { Text("+") }
            }
            Button(
                onClick = { onConfirm(qty) },
                modifier = Modifier.fillMaxWidth().height(52.dp)
            ) { Text("В корзину · ${formatRub(offer.price * qty)}") }
        }
    }
}
