package com.example.myapplication.abcp

import android.os.Bundle
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.rememberTransformableState
import androidx.compose.foundation.gestures.transformable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.pager.HorizontalPager
import androidx.compose.foundation.pager.rememberPagerState
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.ShoppingCart
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import coil.compose.AsyncImage
import com.example.myapplication.SessionManager
import com.example.myapplication.server.AppServer
import com.example.myapplication.ui.theme.AvtodrugTheme
import com.example.myapplication.ui.theme.DeliveryColors
import kotlinx.coroutines.launch

/** «Быстрее» — по сроку: наличие на Хрусталёва/ПОР (самовывоз сегодня) само окажется сверху. */
private enum class Sort(val title: String) { Fast("Быстрее"), Price("Дешевле") }

/** Фильтр по сроку: «Сегодня» — забрать сегодня (свой склад или поставка до конца дня), «До 3 дней». */
private enum class Term(val title: String) { Today("Сегодня"), ThreeDays("До 3 дней") }

private fun Term?.accepts(o: Offer): Boolean = when (this) {
    null -> true
    Term.Today -> o.inStore || formatDelivery(o.deliveryHours) == "сегодня"
    Term.ThreeDays -> o.deliveryHours <= 72
}

private fun Sort.comparator(): Comparator<Offer> = when (this) {
    // Быстрее, при равном сроке — больше ★ (поставщиков), потом дешевле
    Sort.Fast -> compareBy<Offer> { it.deliveryHours }.thenByDescending { it.confirm }.thenBy { it.price }
    Sort.Price -> compareBy<Offer> { it.price }.thenBy { it.deliveryHours }
}

/** Карточка номера, как мобильная выдача сайта: «Наличие» и «Аналоги», сортировка, фото с увеличением. */
@OptIn(ExperimentalMaterial3Api::class)
class OffersActivity : ComponentActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        val brand = intent.getStringExtra(EXTRA_BRAND).orEmpty()
        val number = intent.getStringExtra(EXTRA_NUMBER).orEmpty()
        val description = intent.getStringExtra(EXTRA_DESCRIPTION).orEmpty()
        val shop = AbcpShop(SessionManager(this))
        val server = AppServer(this)

        setContent {
            AvtodrugTheme {
                val scope = rememberCoroutineScope()
                var loading by remember { mutableStateOf(true) }
                var error by remember { mutableStateOf<String?>(null) }
                var offers by remember { mutableStateOf<List<Offer>>(emptyList()) }
                var reload by remember { mutableIntStateOf(0) }
                var sort by remember { mutableStateOf(Sort.Fast) }
                var term by remember { mutableStateOf<Term?>(null) }
                var picked by remember { mutableStateOf<Offer?>(null) }
                var viewer by remember { mutableStateOf<List<String>?>(null) }
                var showAll by remember { mutableStateOf(false) }
                var advices by remember { mutableStateOf<List<BrandHit>>(emptyList()) }

                var onlyConfirmed by remember { mutableStateOf(false) }
                // Гарантии избранных брендов (с сервера) и «только с гарантией»
                var warranties by remember { mutableStateOf<Map<String, BrandWarranty>>(emptyMap()) }
                var onlyWarranty by remember { mutableStateOf(false) }
                var warrantyShown by remember { mutableStateOf<BrandWarranty?>(null) }
                var starsShown by remember { mutableStateOf<Int?>(null) }
                var newSearch by remember { mutableStateOf<Offer?>(null) }
                LaunchedEffect(Unit) { warranties = Warranties.load() }
                fun warrantyOf(o: Offer) = warranties[Warranties.key(o.brand)]

                LaunchedEffect(Unit) { if (!shop.isGuest) advices = runCatching { shop.advices(brand, number) }.getOrDefault(emptyList()) }
                // Достоверные аналоги (звёздочка, как на сайте) — с сервера, из кроссов articles/info

                LaunchedEffect(Unit) { com.example.myapplication.Analytics.event("offers_open", mapOf("brand" to brand, "number" to number, "guest" to shop.isGuest)) }
                LaunchedEffect(showAll, reload) {
                    loading = true
                    error = null
                    try {
                        offers = withConfirmations(shop.offers(number, brand, all = showAll))
                    } catch (e: Exception) {
                        com.example.myapplication.Analytics.error("Выдача → загрузка предложений", e)
                        error = e.message ?: "Не удалось загрузить предложения. Проверьте интернет."
                    } finally {
                        loading = false
                    }
                    // Картинки — потом, список уже на экране (articles/info доступен только API-админу, через сервер)
                    val need = offers.filter { it.images.isEmpty() }.map { it.brand to it.number }.distinct()
                    if (need.isNotEmpty() && !shop.isGuest) {
                        val imgs = runCatching { server.images(need) }.getOrDefault(emptyMap())
                        if (imgs.isNotEmpty()) offers = offers.map { o ->
                            if (o.images.isNotEmpty()) o else o.copy(images = imgs["${o.brand}|${o.number}"].orEmpty())
                        }
                    }
                }

                val fix = cleanNumber(number)
                val (own, analogs) = offers.partition {
                    it.numberFix.equals(fix, ignoreCase = true) && it.brand.equals(brand, ignoreCase = true)
                }
                val cmp = sort.comparator()
                // Одна выдача: искомый номер закреплён сверху, ниже аналоги. Сортировка и фильтры — на весь список.
                // Группы «бренд + номер»: внутри — по выбранной сортировке, сами группы — по лучшему предложению
                fun grouped(list: List<Offer>) = list.filter { term.accepts(it) && (!onlyWarranty || warrantyOf(it) != null) }
                    .groupBy { "${it.brand}|${it.numberFix}" }
                    .values.map { it.sortedWith(cmp) }
                    .sortedWith { a, b -> cmp.compare(a.first(), b.first()) }
                val ownGroups = grouped(own)
                val analogGroups = grouped(analogs.filter { !onlyConfirmed || it.confirm >= 2 })

                val snackbar = remember { SnackbarHostState() }
                Scaffold(
                    snackbarHost = { SnackbarHost(snackbar) },
                    topBar = {
                        TopAppBar(
                            title = {
                                Column {
                                    Text("$brand $number", maxLines = 1, overflow = TextOverflow.Ellipsis)
                                    val d = description.ifBlank { own.firstOrNull()?.description.orEmpty() }
                                    if (d.isNotBlank()) Text(d, style = MaterialTheme.typography.bodySmall, maxLines = 1)
                                }
                            },
                            actions = { if (!shop.isGuest) CartIconButton() }
                        )
                    }
                ) { padding ->
                    Column(Modifier.padding(padding).fillMaxSize()) {
                        Row(
                            Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()).padding(horizontal = 12.dp, vertical = 8.dp),
                            horizontalArrangement = Arrangement.spacedBy(8.dp)
                        ) {
                            Sort.entries.forEach { s ->
                                FilterChip(selected = sort == s, onClick = { sort = s }, label = { Text(s.title) })
                            }
                            VerticalDivider(Modifier.height(32.dp).padding(horizontal = 2.dp))
                            Term.entries.forEach { t ->
                                FilterChip(
                                    selected = term == t,
                                    onClick = { term = if (term == t) null else t },
                                    label = { Text(t.title) }
                                )
                            }
                            if (offers.any { warrantyOf(it) != null }) {
                                FilterChip(
                                    selected = onlyWarranty, onClick = { onlyWarranty = !onlyWarranty },
                                    label = { Text("🛡 С гарантией") }
                                )
                            }
                            if (analogs.any { it.confirm >= 2 }) {
                                FilterChip(
                                    selected = onlyConfirmed, onClick = { onlyConfirmed = !onlyConfirmed },
                                    label = { Text("★ Частые замены") }
                                )
                            }
                        }
                        // «Показать все варианты» — старый список остаётся, сверху полоска загрузки
                        if (loading && offers.isNotEmpty()) LinearProgressIndicator(Modifier.fillMaxWidth())
                        when {
                            loading && offers.isEmpty() -> Box(Modifier.fillMaxSize()) { CircularProgressIndicator(Modifier.align(Alignment.Center)) }
                            error != null && offers.isEmpty() -> com.example.myapplication.ui.ErrorState(error!!, onRetry = { reload++ })
                            ownGroups.isEmpty() && analogGroups.isEmpty() -> Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
                                Text(
                                    when {
                                        term != null -> "С условием «${term!!.title}» ничего нет — снимите фильтр."
                                        onlyConfirmed -> "Аналогов от 2 и более поставщиков нет — снимите фильтр «★»."
                                        else -> "Предложений не найдено. Спросите менеджера в чате — подберём."
                                    }
                                )
                                if (!showAll) OutlinedButton(onClick = { showAll = true }) { Text("Показать все варианты") }
                                if (advices.isNotEmpty()) AdvicesBlock(advices)
                            }
                            else -> LazyColumn(
                                Modifier.fillMaxSize(),
                                contentPadding = PaddingValues(start = 12.dp, end = 12.dp, bottom = 16.dp),
                                verticalArrangement = Arrangement.spacedBy(10.dp)
                            ) {
                                // Искомый номер — закреплён сверху
                                if (ownGroups.isNotEmpty()) {
                                    item(key = "h_own") { SectionTitle("Вы искали") }
                                    items(ownGroups, key = { "o_" + it.first().brand + it.first().numberFix }) { list ->
                                        ArticleCard(list, warrantyOf(list.first()), onImage = { viewer = it }, onPick = { picked = it },
                                            onWarranty = { warrantyShown = it }, onStars = { starsShown = it }, onNumber = { newSearch = it })
                                    }
                                } else if (own.isNotEmpty() || term != null) {
                                    item(key = "h_own_empty") {
                                        Text("По самому номеру ${if (term != null) "с условием «${term!!.title}» " else ""}предложений нет",
                                            style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant,
                                            modifier = Modifier.padding(top = 8.dp))
                                    }
                                } else {
                                    item(key = "h_own_none") {
                                        Text("Самого номера сейчас нет в продаже — ниже аналоги",
                                            style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant,
                                            modifier = Modifier.padding(top = 8.dp))
                                    }
                                }
                                if (analogGroups.isNotEmpty()) {
                                    item(key = "h_an") {
                                        val n = analogGroups.sumOf { it.size }
                                        SectionTitle("Аналоги · ${analogGroups.size} арт., $n предл.")
                                    }
                                    items(analogGroups, key = { "a_" + it.first().brand + it.first().numberFix }) { list ->
                                        ArticleCard(list, warrantyOf(list.first()), onImage = { viewer = it }, onPick = { picked = it },
                                            onWarranty = { warrantyShown = it }, onStars = { starsShown = it }, onNumber = { newSearch = it })
                                    }
                                }
                                if (!showAll) item {
                                    // ABCP по умолчанию отдаёт сокращённую выдачу — как сайт до «Показать все варианты»
                                    OutlinedButton(onClick = { showAll = true }, modifier = Modifier.fillMaxWidth()) {
                                        Text("Показать все варианты")
                                    }
                                }
                                if (advices.isNotEmpty()) item { AdvicesBlock(advices) }
                            }
                        }
                    }
                }

                warrantyShown?.let { WarrantySheet(it) { warrantyShown = null } }
                newSearch?.let { o ->
                    AlertDialog(
                        onDismissRequest = { newSearch = null },
                        title = { Text("Новый поиск по ${o.number} ${o.brand}?") },
                        text = {
                            Text(
                                "Откроется выдача по этому номеру. Предложения и отметки ★ будут относиться уже к нему, " +
                                    "а не к исходной детали $number, и носят справочный характер."
                            )
                        },
                        confirmButton = {
                            TextButton(onClick = {
                                newSearch = null
                                startActivity(
                                    android.content.Intent(this@OffersActivity, OffersActivity::class.java)
                                        .putExtra(EXTRA_BRAND, o.brand).putExtra(EXTRA_NUMBER, o.number)
                                        .putExtra(EXTRA_DESCRIPTION, o.description)
                                )
                            }) { Text("Искать") }
                        },
                        dismissButton = { TextButton(onClick = { newSearch = null }) { Text("Отмена") } }
                    )
                }
                starsShown?.let { n ->
                    AlertDialog(
                        onDismissRequest = { starsShown = null },
                        title = { Text("★ Частая замена") },
                        text = {
                            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                                Text("Эту деталь как замену предлагают сразу несколько поставщиков ($n) — высокая вероятность, что она подойдёт.")
                                Text(
                                    "Но это подсказка, а не проверка. Сверяйте оригинальный OEM-номер по каталогу производителя " +
                                        "с учётом комплектации и модификации вашей машины и возможных нештатных деталей.",
                                    style = MaterialTheme.typography.bodySmall
                                )
                                Text("Не уверены — спросите менеджера, проверим по VIN.", style = MaterialTheme.typography.bodySmall)
                            }
                        },
                        confirmButton = { TextButton(onClick = { starsShown = null }) { Text("Понятно") } },
                        dismissButton = {
                            TextButton(onClick = {
                                starsShown = null
                                startActivity(android.content.Intent(this@OffersActivity, com.example.myapplication.ChatActivity::class.java))
                            }) { Text("Спросить менеджера") }
                        }
                    )
                }

                picked?.let { o ->
                    AddToCartSheet(
                        offer = o,
                        onImage = { viewer = it },
                        onDismiss = { picked = null },
                        onConfirm = { qty ->
                            picked = null
                            if (shop.isGuest) {
                                Toast.makeText(this@OffersActivity, "Войдите, чтобы положить в корзину", Toast.LENGTH_LONG).show()
                                startActivity(android.content.Intent(this@OffersActivity, com.example.myapplication.MainActivity::class.java))
                                return@AddToCartSheet
                            }
                            scope.launch {
                                try {
                                    shop.addToBasket(o, qty)
                                    com.example.myapplication.Analytics.event("add_to_cart", mapOf("brand" to o.brand, "number" to o.number, "qty" to qty, "in_store" to o.inStore))
                                    CartState.refresh(shop)
                                    val r = snackbar.showSnackbar("Добавлено в корзину", actionLabel = "Перейти", duration = SnackbarDuration.Short)
                                    if (r == SnackbarResult.ActionPerformed) startActivity(android.content.Intent(this@OffersActivity, CartActivity::class.java))
                                } catch (e: Exception) {
                                    com.example.myapplication.Analytics.error("Выдача → в корзину", e)
                                    Toast.makeText(this@OffersActivity, e.message ?: "Ошибка", Toast.LENGTH_LONG).show()
                                }
                            }
                        }
                    )
                }

                viewer?.let { urls -> ImageViewer(urls) { viewer = null } }
            }
        }
    }

    companion object {
        const val EXTRA_BRAND = "extra_brand"
        const val EXTRA_NUMBER = "extra_number"
        const val EXTRA_DESCRIPTION = "extra_description"
    }
}

/**
 * Артикул — как мобильная выдача сайта: фото, подчёркнутый номер (нажатие — новый поиск), бренд, ★,
 * оранжевая плашка гарантии + кубки, описание; ниже — предложения цветными плашками; «Показать ещё N».
 */
@Composable
private fun ArticleCard(
    list: List<Offer>, warranty: BrandWarranty?, onImage: (List<String>) -> Unit, onPick: (Offer) -> Unit,
    onWarranty: (BrandWarranty) -> Unit, onStars: (Int) -> Unit, onNumber: (Offer) -> Unit
) {
    val head = list.first()
    val images = list.flatMap { it.images }.distinct()
    // Сразу — 3 лучших предложения (список уже отсортирован), остальные по кнопке, как «Показать ещё» на сайте
    var expanded by remember(head.brand, head.numberFix) { mutableStateOf(false) }
    val shown = if (expanded) list else list.take(3)
    Surface(
        color = MaterialTheme.colorScheme.surfaceContainerLow,
        shape = RoundedCornerShape(12.dp),
        modifier = Modifier.fillMaxWidth()
    ) {
        Column(Modifier.padding(8.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            Row(verticalAlignment = Alignment.Top) {
                // Фото (своих у ABCP почти нет — значок камеры: фото в Яндексе/Google)
                if (images.isNotEmpty()) {
                    AsyncImage(
                        model = images.first(), contentDescription = "Фото", contentScale = ContentScale.Fit,
                        modifier = Modifier.size(52.dp).clip(RoundedCornerShape(8.dp)).background(Color.White).clickable { onImage(images) }
                    )
                } else PhotoSearchButton(head.brand, head.number)
                Spacer(Modifier.width(10.dp))
                Column(Modifier.weight(1f)) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Text(
                            head.number, style = MaterialTheme.typography.titleMedium,
                            textDecoration = androidx.compose.ui.text.style.TextDecoration.Underline,
                            modifier = Modifier.clickable { onNumber(head) }
                        )
                        Text("  ${head.brand}", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold,
                            maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f, fill = false))
                        // ★ — частая замена (у 2+ поставщиков); подробности по нажатию
                        val n = list.maxOf { it.confirm }
                        if (n >= 2) Text(" ★", color = Color(0xFFF2B01E), style = MaterialTheme.typography.titleLarge,
                            modifier = Modifier.clickable { onStars(n) })
                    }
                    warranty?.let { w -> WarrantyBadge(w) { onWarranty(w) } }
                    if (head.description.isNotBlank()) {
                        Text(head.description, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant,
                            maxLines = 2, overflow = TextOverflow.Ellipsis)
                    }
                }
            }
            shown.forEach { o -> OfferRow(o) { onPick(o) } }
            if (list.size > 3) {
                Button(
                    onClick = { expanded = !expanded },
                    colors = ButtonDefaults.buttonColors(containerColor = Color(0xFFFFC52E), contentColor = Color.White),
                    shape = RoundedCornerShape(8.dp),
                    modifier = Modifier.fillMaxWidth().height(40.dp)
                ) {
                    Text(if (expanded) "Свернуть" else "Показать ещё", fontWeight = FontWeight.Bold)
                    if (!expanded) {
                        Spacer(Modifier.width(8.dp))
                        Text("${list.size - 3}", color = Color.Black, style = MaterialTheme.typography.labelMedium,
                            modifier = Modifier.background(Color.White, RoundedCornerShape(10.dp)).padding(horizontal = 7.dp, vertical = 1.dp))
                    }
                }
            }
        }
    }
}

/** Предложение — цветная плашка поставщика (как на сайте): срок/склад, значки, цена, количество, корзина. */
@Composable
private fun OfferRow(o: Offer, onClick: () -> Unit) {
    // Цвет поставщика из ABCP — фоном; тёмный текст на нём читается (цвета у магазина светлые)
    val bg = parseAbcpColor(o.supplierColor) ?: MaterialTheme.colorScheme.surfaceVariant
    Row(
        Modifier.fillMaxWidth().clip(RoundedCornerShape(8.dp)).background(bg).clickable(onClick = onClick)
            .padding(start = 12.dp, end = 8.dp, top = 8.dp, bottom = 8.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        Column(Modifier.weight(1f)) {
            Text(o.deliveryText(), style = MaterialTheme.typography.titleSmall, fontWeight = FontWeight.SemiBold,
                color = Color(0xFF1B1B1B), maxLines = 2, overflow = TextOverflow.Ellipsis)
            SupplierIcons(o)
        }
        Text(formatRub(o.price), style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold, color = Color(0xFF1B1B1B))
        Text(
            if (o.availability > 0) "${o.availability} шт." else "",
            style = MaterialTheme.typography.labelSmall, color = Color(0xFF5F6368),
            modifier = Modifier.widthIn(min = 44.dp).padding(horizontal = 8.dp)
        )
        Box(
            Modifier.size(38.dp).background(Color(0xFFFFC52E), RoundedCornerShape(6.dp)).clickable(onClick = onClick),
            contentAlignment = Alignment.Center
        ) { Icon(Icons.Default.ShoppingCart, "В корзину", tint = Color.White, modifier = Modifier.size(20.dp)) }
    }
}

/** Значки поставщика, как на сайте: 🏠✓ надёжный, склад ✕ сторонний, ↩ без возврата, ₽ предоплата, Б/у. Текст — в шторке заказа. */
@Composable
private fun SupplierIcons(o: Offer) {
    val list = buildList {
        addAll(o.badges)
        if (o.noReturn && o.badges.none { "возврат" in it.text.lowercase() }) add(SupplierBadge("Возврат невозможен", BadgeKind.Bad))
        if (o.isUsed) add(SupplierBadge("Б/у", BadgeKind.Bad))
    }
    if (list.isEmpty()) return
    Row(Modifier.padding(top = 2.dp), horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
        list.forEach { b ->
            val t = b.text.lowercase()
            val (glyph, color) = when {
                "возврат" in t -> "↩" to Color(0xFFD32F2F)
                "сторон" in t || "под заказ" in t -> "⌂✕" to Color(0xFFD32F2F)
                "предоплат" in t || "оплат" in t -> "₽" to Color(0xFF1B1B1B)
                "б/у" in t -> "Б/у" to Color(0xFFD32F2F)
                b.kind == BadgeKind.Good -> "⌂✓" to Color(0xFF1E6FD9)
                b.kind == BadgeKind.Bad -> "!" to Color(0xFFD32F2F)
                else -> "•" to Color(0xFF5F6368)
            }
            Text(glyph, color = color, fontWeight = FontWeight.Bold, style = MaterialTheme.typography.labelLarge)
        }
    }
}

/** Метки поставщика текстом — в шторке «В корзину», где есть место объяснить. */
@Composable
private fun Badges(o: Offer) {
    val list = buildList {
        if (o.isUsed) add(SupplierBadge("Б/у", BadgeKind.Bad))
        addAll(o.badges)
        if (o.badges.isEmpty() && o.noReturn) add(SupplierBadge("Возврат невозможен", BadgeKind.Bad))
    }
    Row(Modifier.padding(top = 4.dp).horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(4.dp)) {
        list.forEach { b ->
            val c = when (b.kind) {
                BadgeKind.Good -> DeliveryColors.today
                BadgeKind.Bad -> MaterialTheme.colorScheme.error
                BadgeKind.Info -> DeliveryColors.later
            }
            Text(
                b.text, style = MaterialTheme.typography.labelSmall, color = c, maxLines = 1,
                modifier = Modifier.background(c.copy(alpha = 0.12f), RoundedCornerShape(6.dp)).padding(horizontal = 6.dp, vertical = 2.dp)
            )
        }
    }
}

/** «С этим товаром покупают» — статистика заказов магазина; нажатие открывает цены. */
@Composable
private fun AdvicesBlock(list: List<BrandHit>) {
    val ctx = androidx.compose.ui.platform.LocalContext.current
    Column(Modifier.padding(top = 8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Text("С этим товаром покупают", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold)
        Row(Modifier.horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            list.forEach { h ->
                OutlinedCard(
                    Modifier.width(170.dp).clickable {
                        ctx.startActivity(
                            android.content.Intent(ctx, OffersActivity::class.java)
                                .putExtra(OffersActivity.EXTRA_BRAND, h.brand)
                                .putExtra(OffersActivity.EXTRA_NUMBER, h.number)
                                .putExtra(OffersActivity.EXTRA_DESCRIPTION, h.description)
                        )
                    }
                ) {
                    Column(Modifier.padding(12.dp)) {
                        Text(h.number, fontWeight = FontWeight.Bold, maxLines = 1)
                        Text(h.brand, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.primary, maxLines = 1)
                        if (h.description.isNotBlank()) {
                            Text(h.description, style = MaterialTheme.typography.bodySmall, maxLines = 2, overflow = TextOverflow.Ellipsis)
                        }
                    }
                }
            }
        }
    }
}

/** Шторка снизу: фото, срок, метки, количество с учётом кратности и итог. */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun AddToCartSheet(offer: Offer, onImage: (List<String>) -> Unit, onDismiss: () -> Unit, onConfirm: (Int) -> Unit) {
    val step = offer.packing
    val max = if (offer.availability > 0) offer.availability else Int.MAX_VALUE
    var qty by remember { mutableIntStateOf(step) }
    ModalBottomSheet(onDismissRequest = onDismiss) {
        Column(
            Modifier.fillMaxWidth().padding(horizontal = 20.dp).padding(bottom = 28.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp)
        ) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                if (offer.images.isNotEmpty()) {
                    AsyncImage(
                        model = offer.images.first(), contentDescription = "Фото", contentScale = ContentScale.Fit,
                        modifier = Modifier.size(80.dp).clip(RoundedCornerShape(12.dp)).background(Color.White)
                            .clickable { onImage(offer.images) }
                    )
                    Spacer(Modifier.width(12.dp))
                }
                Column {
                    Text("${offer.brand} ${offer.number}", style = MaterialTheme.typography.titleLarge)
                    if (offer.description.isNotBlank()) Text(offer.description, style = MaterialTheme.typography.bodyMedium)
                }
            }
            Text("${formatRub(offer.price)} · ${offer.deliveryText()}", style = MaterialTheme.typography.titleMedium)
            Text(formatAvailability(offer.availability), style = MaterialTheme.typography.bodySmall)
            offer.probability?.let { p ->
                Text("Вероятность поставки $p%" + (offer.probabilityText?.let { " — $it" } ?: ""), style = MaterialTheme.typography.bodySmall)
            }
            offer.updatedAt?.let { Text("Прайс обновлён: $it", style = MaterialTheme.typography.bodySmall) }
            if (offer.badges.isNotEmpty() || offer.noReturn || offer.isUsed) Badges(offer)
            Row(verticalAlignment = Alignment.CenterVertically) {
                FilledTonalButton(onClick = { qty = (qty - step).coerceAtLeast(step) }) { Text("−") }
                Text("$qty шт.", Modifier.padding(horizontal = 20.dp), style = MaterialTheme.typography.titleLarge)
                FilledTonalButton(onClick = { if (qty + step <= max) qty += step }) { Text("+") }
            }
            Button(onClick = { onConfirm(qty) }, modifier = Modifier.fillMaxWidth().height(52.dp)) {
                Text("В корзину · ${formatRub(offer.price * qty)}")
            }
        }
    }
}

/** Фото на весь экран: листать пальцем, приближать щипком; пока не приближено — свайп листает фото. */
@OptIn(androidx.compose.foundation.ExperimentalFoundationApi::class)
@Composable
fun ImageViewer(urls: List<String>, onClose: () -> Unit) {
    Dialog(onDismissRequest = onClose, properties = DialogProperties(usePlatformDefaultWidth = false)) {
        Box(Modifier.fillMaxSize().background(Color.Black)) {
            val pager = rememberPagerState { urls.size }
            HorizontalPager(state = pager, modifier = Modifier.fillMaxSize()) { page ->
                var scale by remember(page) { mutableFloatStateOf(1f) }
                var offset by remember(page) { mutableStateOf(androidx.compose.ui.geometry.Offset.Zero) }
                val state = rememberTransformableState { zoom, pan, _ ->
                    scale = (scale * zoom).coerceIn(1f, 5f)
                    offset = if (scale == 1f) androidx.compose.ui.geometry.Offset.Zero else offset + pan
                }
                AsyncImage(
                    model = urls[page], contentDescription = null, contentScale = ContentScale.Fit,
                    modifier = Modifier.fillMaxSize()
                        .transformable(state, canPan = { scale > 1f })
                        .graphicsLayer { scaleX = scale; scaleY = scale; translationX = offset.x; translationY = offset.y }
                )
            }
            if (urls.size > 1) {
                Text(
                    "${pager.currentPage + 1} / ${urls.size}", color = Color.White,
                    modifier = Modifier.align(Alignment.BottomCenter).padding(24.dp)
                )
            }
            IconButton(onClick = onClose, modifier = Modifier.align(Alignment.TopEnd).padding(8.dp)) {
                Icon(Icons.Default.Close, "Закрыть", tint = Color.White)
            }
        }
    }
}

/** Кнопка 📷 в карточке артикула: меню «Фото в Яндексе / Google». */
@Composable
private fun PhotoSearchButton(brand: String, number: String) {
    val ctx = androidx.compose.ui.platform.LocalContext.current
    var menu by remember { mutableStateOf(false) }
    val q = android.net.Uri.encode("$brand $number".trim())
    fun open(url: String) = ctx.startActivity(android.content.Intent(android.content.Intent.ACTION_VIEW, android.net.Uri.parse(url)))
    Box {
        FilledTonalIconButton(onClick = { menu = true }, modifier = Modifier.size(40.dp)) { Text("📷") }
        DropdownMenu(expanded = menu, onDismissRequest = { menu = false }) {
            DropdownMenuItem(text = { Text("Фото в Яндексе") }, onClick = { menu = false; open("https://yandex.ru/images/search?text=$q") })
            DropdownMenuItem(text = { Text("Фото в Google") }, onClick = { menu = false; open("https://www.google.com/search?tbm=isch&q=$q") })
        }
    }
}

/** «Фото в Яндексе / Google»: поиск картинок по бренду и номеру, открывается в браузере. */
@Composable
fun PhotoSearchLinks(brand: String, number: String) {
    val ctx = androidx.compose.ui.platform.LocalContext.current
    val q = android.net.Uri.encode("$brand $number".trim())
    fun open(url: String) = ctx.startActivity(android.content.Intent(android.content.Intent.ACTION_VIEW, android.net.Uri.parse(url)))
    Row(Modifier.padding(top = 6.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        FilledTonalButton(
            onClick = { open("https://yandex.ru/images/search?text=$q") },
            contentPadding = PaddingValues(horizontal = 12.dp, vertical = 4.dp),
            modifier = Modifier.height(34.dp)
        ) { Text("📷 Фото в Яндексе", style = MaterialTheme.typography.labelMedium) }
        FilledTonalButton(
            onClick = { open("https://www.google.com/search?tbm=isch&q=$q") },
            contentPadding = PaddingValues(horizontal = 12.dp, vertical = 4.dp),
            modifier = Modifier.height(34.dp)
        ) { Text("📷 Фото в Google", style = MaterialTheme.typography.labelMedium) }
    }
}

@Composable
private fun SectionTitle(text: String) {
    Text(text, style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.Bold, modifier = Modifier.padding(top = 8.dp))
}
