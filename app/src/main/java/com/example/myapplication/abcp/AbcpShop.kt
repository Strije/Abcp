package com.example.myapplication.abcp

import com.example.myapplication.SessionManager
import com.example.myapplication.performRequestWithRetry
import com.google.gson.JsonElement
import com.google.gson.JsonObject
import retrofit2.Response
import java.text.DecimalFormat
import java.text.DecimalFormatSymbols
import java.util.Locale
import java.text.SimpleDateFormat
import java.util.Calendar

// ---------- Модели ----------

data class BrandHit(
    val brand: String,
    val number: String,
    val description: String,
    /** ABCP: «есть в наличии» у этого бренда с этим номером */
    val available: Boolean = false
)

/**
 * Какой бренд открыть сразу, минуя список: единственный; совпавший с известным заранее
 * (Hyundai-KIA ≈ KIA, Mahle/Knecht ≈ Knecht); единственный в наличии. Иначе null — показать список.
 */
fun autoPickBrand(found: List<BrandHit>, preferred: String?): BrandHit? {
    found.singleOrNull()?.let { return it }
    val pref = preferred?.let(::brandKey).orEmpty()
    if (pref.isNotEmpty()) {
        found.firstOrNull { brandKey(it.brand) == pref }?.let { return it }
        found.filter { val k = brandKey(it.brand); k.contains(pref) || pref.contains(k) }.singleOrNull()?.let { return it }
    }
    return found.filter { it.available }.singleOrNull()
}

private fun brandKey(b: String) = b.uppercase().filter { it.isLetterOrDigit() }

data class Offer(
    val brand: String,
    val number: String,
    val numberFix: String,
    val description: String,
    val price: Double,
    /** >0 — штук на складе; <=0 — ABCP не знает точного количества */
    val availability: Int,
    val packing: Int,
    val deliveryHours: Int,
    val deliveryHoursMax: Int,
    val supplierCode: String,
    val itemKey: String,
    /** Короткий код поставщика (distributorCode), если задан */
    val supplier: String,
    val noReturn: Boolean,
    /** Метки из HTML-описания поставщика на сайте: «Надежный поставщик», «Сторонний склад»… */
    val badges: List<SupplierBadge> = emptyList(),
    /** Подпись вместо срока, например «Хрусталева 111 (самовывоз)» (deadlineReplace) */
    val deadlineLabel: String? = null,
    /** Цвет поставщика из ABCP (#RRGGBB) */
    val supplierColor: String? = null,
    /** Полные URL картинок, если ABCP их отдаёт */
    val images: List<String> = emptyList(),
    /** Вероятность поставки, % (null — ABCP не сообщил) */
    val probability: Int? = null,
    /** Пояснение к вероятности поставки */
    val probabilityText: String? = null,
    val isUsed: Boolean = false,
    /** Когда обновлён прайс поставщика */
    val updatedAt: String? = null
) {
    /** Товар лежит в нашем магазине — забрать можно сегодня */
    val inStore: Boolean get() = deliveryHours <= 0
}

enum class BadgeKind { Good, Bad, Info }

data class SupplierBadge(val text: String, val kind: BadgeKind)

data class BasketItem(
    val brand: String,
    val number: String,
    val description: String,
    val price: Double,
    val quantity: Int,
    val deadlineHours: Int,
    val deadlineHoursMax: Int,
    val supplierCode: String,
    val itemKey: String,
    val positionId: String,
    val errorMessage: String?,
    /** Кратность (мин. партия) */
    val packing: Int = 1,
    /** Корзина при мультикорзине (null — основная) */
    val basketId: String? = null,
    val basketName: String? = null
)

data class IdName(val id: String, val name: String)

data class ShipmentDate(val date: String, val name: String)

data class CheckoutOptions(
    val payments: List<IdName>,
    val shipmentMethods: List<IdName>,
    val offices: List<IdName>,
    val addresses: List<IdName>,
    val dates: List<ShipmentDate>
)

data class OrderChoice(
    val paymentId: String?,
    val shipmentMethodId: String?,
    /** "0" — самовывоз */
    val addressId: String,
    val officeId: String?,
    val date: String?,
    val comment: String
)

class AbcpException(message: String) : Exception(message)

// ---------- Репозиторий ----------

class AbcpShop(private val session: SessionManager) {

    companion object {
        /** Справочник статусов на час — один на всё приложение */
        @Volatile private var statusCache: Pair<Long, Set<String>>? = null
    }

    private val api = ApiClient.create()
    private val login get() = session.login()
    private val psw get() = session.passMd5()

    suspend fun tips(query: String): List<BrandHit> =
        items(call { api.searchTips(login, psw, query) }).mapNotNull { it.toBrandHit() }

    /** Гость — не вошёл в аккаунт: поиск идёт через наш сервер с ценами гостевого профиля */
    val isGuest: Boolean get() = !session.isLoggedIn()
    private val server by lazy { com.example.myapplication.server.AppServer(session.context) }

    suspend fun brands(number: String): List<BrandHit> =
        items(if (isGuest) server.guestBrands(number) else call { api.searchBrands(login, psw, number) })
            .mapNotNull { it.toBrandHit() }

    suspend fun offers(number: String, brand: String, all: Boolean = false): List<Offer> =
        items(
            if (isGuest) server.guestOffers(number, brand, all)
            else call { api.searchArticles(login, psw, number, brand, disableFiltering = if (all) 1 else 0) }
        )
            .mapNotNull { it.toOffer() }
            .sortedWith(compareBy<Offer> { it.price }.thenBy { it.deliveryHours })

    /** id статусов, которые в ABCP отмечены как конечные (выдано, отказ, возврат…) */
    suspend fun finalStatusIds(): Set<String> {
        statusCache?.let { (at, ids) -> if (System.currentTimeMillis() - at < 3_600_000L) return ids }
        return loadFinalStatusIds().also { statusCache = System.currentTimeMillis() to it }
    }

    private suspend fun loadFinalStatusIds(): Set<String> =
        items(call { api.orderStatuses(login, psw) }).mapNotNull {
            val o = it.asJsonObjectOrNull() ?: return@mapNotNull null
            val fin = o.str("isFinalStatus")
            if (fin == "1" || fin.equals("true", ignoreCase = true)) o.str("id") else null
        }.toSet()

    /** Что клиент искал раньше — без повторов, свежие сверху */
    suspend fun history(): List<BrandHit> =
        items(call { api.searchHistory(login, psw) }).mapNotNull { it.toBrandHit() }
            .distinctBy { cleanNumber(it.brand) + cleanNumber(it.number) }

    /** «С этим товаром покупают» */
    suspend fun advices(brand: String, number: String): List<BrandHit> =
        items(call { api.advices(login, psw, brand, number) }).mapNotNull { it.toBrandHit() }

    /**
     * Все позиции из всех корзин. Если в магазине включена мультикорзина, без basketId ABCP отдаёт
     * только основную — поэтому обходим каждую. (При опции «частичное оформление» ABCP отдаёт только отмеченные.)
     */
    /** Добавить машину в гараж ABCP. kind — что ввёл человек: vin / frame / plate. */
    suspend fun addToGarage(name: String, value: String, kind: String) {
        val key = when (kind) { "vin" -> "vin"; "plate" -> "vehicleRegPlate"; else -> "frame" }
        val fields = auth() + mapOf("name" to name.ifBlank { value }, key to value)
        call(retry = false) { api.garageAdd(fields) }
    }

    /** Запрос на отмену позиции; возвращает текст ответа ABCP. */
    suspend fun cancelPosition(positionId: String): String {
        val r = call(retry = false) { api.cancelPosition(auth() + mapOf("positionId" to positionId)) }
        return r.asJsonObjectOrNull()?.str("message") ?: "Запрос на отмену отправлен"
    }

    suspend fun basket(): List<BasketItem> {
        val baskets = runCatching { idNames(call { api.basketMultibasket(login, psw) }) }.getOrDefault(emptyList())
        if (baskets.size <= 1) return basketContent(baskets.firstOrNull())
        return baskets.flatMap { basketContent(it) }
    }

    private suspend fun basketContent(b: IdName?): List<BasketItem> =
        items(call { api.basketContent(login, psw, b?.id) })
            .mapNotNull { it.toBasketItem()?.copy(basketId = b?.id, basketName = b?.name) }

    suspend fun addToBasket(o: Offer, quantity: Int) =
        setBasketQuantity(o.brand, o.number, o.itemKey, o.supplierCode, quantity)

    /**
     * Новое количество позиции. ABCP при повторном basket/add прибавляет количество,
     * поэтому увеличение — добавлением разницы, уменьшение — удалением и добавлением заново (как советует документация).
     */
    suspend fun changeQuantity(b: BasketItem, newQty: Int) {
        when {
            newQty == b.quantity -> Unit
            newQty > b.quantity -> setBasketQuantity(b.brand, b.number, b.itemKey, b.supplierCode, newQty - b.quantity, b.basketId)
            newQty <= 0 -> removeFromBasket(b)
            else -> {
                removeFromBasket(b)
                setBasketQuantity(b.brand, b.number, b.itemKey, b.supplierCode, newQty, b.basketId)
            }
        }
    }

    suspend fun removeFromBasket(b: BasketItem) =
        setBasketQuantity(b.brand, b.number, b.itemKey, b.supplierCode, 0, b.basketId)

    private suspend fun setBasketQuantity(
        brand: String, number: String, itemKey: String, supplierCode: String, quantity: Int,
        basketId: String? = null
    ) {
        val fields = auth() + (basketId?.let { mapOf("basketId" to it) } ?: emptyMap()) + mapOf(
            "positions[0][brand]" to brand,
            "positions[0][number]" to number,
            "positions[0][itemKey]" to itemKey,
            "positions[0][supplierCode]" to supplierCode,
            "positions[0][quantity]" to quantity.toString()
        )
        val r = call(retry = false) { api.basketAdd(fields) }.asJsonObjectOrNull()
        if (r?.str("status") == "0") {
            val pos = r.get("positions")?.let { items(it) }?.firstOrNull()?.asJsonObjectOrNull()
            throw AbcpException(cleanAbcpMessage(pos?.str("errorMessage")) ?: "Не удалось изменить корзину")
        }
    }

    suspend fun checkoutOptions(basket: List<BasketItem>): CheckoutOptions {
        val minH = basket.minOfOrNull { it.deadlineHours } ?: 0
        val maxH = basket.maxOfOrNull { maxOf(it.deadlineHours, it.deadlineHoursMax) } ?: 0
        return CheckoutOptions(
            payments = idNames(call { api.paymentMethods(login, psw) }),
            shipmentMethods = idNames(call { api.shipmentMethods(login, psw) }),
            offices = idNames(call { api.shipmentOffices(login, psw) }),
            addresses = idNames(call { api.shipmentAddresses(login, psw) }),
            dates = items(call { api.shipmentDates(login, psw, minH, maxH) }).mapNotNull {
                val o = it.asJsonObjectOrNull() ?: return@mapNotNull null
                val d = o.str("date") ?: return@mapNotNull null
                ShipmentDate(d, o.str("name") ?: d)
            }
        )
    }

    /** Оформляет каждую корзину, где есть позиции. Возвращает номера созданных заказов. */
    suspend fun placeOrder(c: OrderChoice, basketIds: List<String?> = listOf(null)): List<String> {
        val numbers = mutableListOf<String>()
        var lastError: Exception? = null
        for (id in basketIds.distinct()) {
            try {
                numbers += placeOrderOne(c, id)
            } catch (e: Exception) {
                com.example.myapplication.Analytics.error("Оформление → basket/order", e)
                lastError = e
            }
        }
        if (numbers.isEmpty()) throw lastError ?: AbcpException("Заказ не оформлен")
        return numbers
    }

    private suspend fun placeOrderOne(c: OrderChoice, basketId: String?): List<String> {
        val fields = buildMap {
            basketId?.let { put("basketId", it) }
            putAll(auth())
            c.paymentId?.let { put("paymentMethod", it) }
            c.shipmentMethodId?.let { put("shipmentMethod", it) }
            put("shipmentAddress", c.addressId)
            c.officeId?.let { put("shipmentOffice", it) }
            c.date?.let { put("shipmentDate", it) }
            if (c.comment.isNotBlank()) put("comment", c.comment)
        }
        val r = call(retry = false) { api.basketOrder(fields) }.asJsonObjectOrNull()
            ?: throw AbcpException("Пустой ответ при оформлении заказа")
        // Даже при status=0 часть позиций может уйти в заказ — смотрим orders
        val numbers = r.get("orders")?.let { items(it) }.orEmpty()
            .mapNotNull { it.asJsonObjectOrNull()?.str("number") }
        if (numbers.isEmpty()) throw AbcpException(cleanAbcpMessage(r.str("errorMessage")) ?: "Заказ не оформлен")
        return numbers
    }

    private fun auth() = mapOf("userlogin" to login, "userpsw" to psw)

    /** retry только для чтения: повтор basket/add или basket/order задвоил бы позицию/заказ. */
    private suspend fun call(
        retry: Boolean = true,
        request: suspend () -> Response<JsonElement>
    ): JsonElement {
        val resp = if (retry) performRequestWithRetry { request() } else request()
        if (!resp.isSuccessful) throw AbcpException(prettifyAbcpError(resp.errorBody()?.string()))
        return resp.body() ?: throw AbcpException("Пустой ответ сервера")
    }
}

// ---------- Разбор JSON ----------

/** Массив или объект-словарь {"0": {...}, "1": {...}} → список элементов. */
internal fun items(e: JsonElement): List<JsonElement> = when {
    e.isJsonArray -> e.asJsonArray.toList()
    e.isJsonObject -> e.asJsonObject.entrySet().map { it.value }.filter { it.isJsonObject }
    else -> emptyList()
}

private fun idNames(e: JsonElement): List<IdName> = items(e).mapNotNull {
    val o = it.asJsonObjectOrNull() ?: return@mapNotNull null
    val id = o.str("id") ?: return@mapNotNull null
    IdName(id, o.str("name") ?: id)
}

private fun JsonElement.asJsonObjectOrNull(): JsonObject? = if (isJsonObject) asJsonObject else null

private fun JsonObject.str(key: String): String? =
    get(key)?.takeIf { it.isJsonPrimitive }?.asString?.trim()?.takeIf { it.isNotEmpty() }

private fun JsonObject.num(key: String): Double = parseAbcpNumber(str(key))

private fun JsonObject.int(key: String): Int = num(key).toInt()

private fun JsonElement.toBrandHit(): BrandHit? {
    val o = asJsonObjectOrNull() ?: return null
    return BrandHit(
        brand = o.str("brand") ?: return null,
        number = o.str("number") ?: return null,
        description = o.str("description").orEmpty(),
        available = o.str("availability").let { it == "1" || it.equals("true", true) || (it?.toIntOrNull() ?: 0) > 0 }
    )
}

private fun JsonElement.toOffer(): Offer? {
    val o = asJsonObjectOrNull() ?: return null
    val number = o.str("number") ?: return null
    return Offer(
        brand = o.str("brand").orEmpty(),
        number = number,
        numberFix = o.str("numberFix") ?: cleanNumber(number),
        description = o.str("description").orEmpty(),
        price = o.num("price"),
        availability = o.int("availability"),
        packing = o.int("packing").coerceAtLeast(1),
        deliveryHours = o.int("deliveryPeriod"),
        deliveryHoursMax = o.int("deliveryPeriodMax"),
        supplierCode = o.str("supplierCode").orEmpty(),
        itemKey = o.str("itemKey").orEmpty(),
        supplier = o.str("distributorCode").orEmpty(),
        noReturn = o.str("noReturn").let { it == "1" || it == "true" },
        badges = parseSupplierBadges(o.str("supplierDescription")),
        deadlineLabel = o.str("deadlineReplace")?.let(::stripHtml)?.takeIf { it.isNotBlank() },
        supplierColor = o.str("supplierColor"),
        images = parseImages(o),
        probability = o.str("deliveryProbability")?.replace(',', '.')?.toDoubleOrNull()
            ?.let { if (it <= 1.0) it * 100 else it }?.toInt()?.takeIf { it > 0 },
        probabilityText = o.str("descriptionOfDeliveryProbability")?.let(::stripHtml)?.takeIf { it.isNotBlank() },
        isUsed = o.str("isUsed").let { it == "1" || it.equals("true", true) },
        updatedAt = o.str("lastUpdateTime")
    )
}

private fun JsonElement.toBasketItem(): BasketItem? {
    val o = asJsonObjectOrNull() ?: return null
    return BasketItem(
        brand = o.str("brand").orEmpty(),
        number = o.str("number") ?: return null,
        description = o.str("description").orEmpty(),
        price = o.num("priceInSiteCurrency").takeIf { it > 0 } ?: o.num("price"),
        quantity = o.int("quantity"),
        deadlineHours = o.int("deadline"),
        deadlineHoursMax = o.int("deadlineMax"),
        supplierCode = o.str("supplierCode").orEmpty(),
        itemKey = o.str("itemKey").orEmpty(),
        positionId = o.str("positionId").orEmpty(),
        packing = o.int("packing").coerceAtLeast(1),
        errorMessage = cleanAbcpMessage(o.str("errorMessage"))
    )
}

/**
 * supplierDescription — HTML-значки с сайта: <i class="… red" data-original-title="Сторонний склад">.
 * Берём подсказку (data-original-title/title) и цвет по классу; если значков нет — просто текст без тегов.
 */
fun parseSupplierBadges(html: String?): List<SupplierBadge> {
    if (html.isNullOrBlank()) return emptyList()
    val icons = Regex("<i\\b[^>]*>").findAll(html).mapNotNull { m ->
        val tag = m.value
        val title = Regex("data-original-title=\"([^\"]*)\"").find(tag)?.groupValues?.get(1)
            ?.takeIf { it.isNotBlank() }
            ?: Regex("\\btitle=\"([^\"]*)\"").find(tag)?.groupValues?.get(1)?.takeIf { it.isNotBlank() }
            ?: return@mapNotNull null
        val cls = Regex("class=\"([^\"]*)\"").find(tag)?.groupValues?.get(1).orEmpty()
        val kind = when {
            Regex("\\b(red|danger)\\b").containsMatchIn(cls) -> BadgeKind.Bad
            Regex("\\b(blue|green|success)\\b").containsMatchIn(cls) -> BadgeKind.Good
            else -> BadgeKind.Info
        }
        SupplierBadge(unescape(title), kind)
    }.toList()
    if (icons.isNotEmpty()) return icons
    val text = stripHtml(html)
    return if (text.isBlank()) emptyList() else listOf(SupplierBadge(text, BadgeKind.Info))
}

/**
 * Текст ошибки ABCP для человека: сайт присылает HTML («Внимание!<br> цена и/или наличие…»)
 * и служебную приписку «The resource is blocked». Переносы <br> сохраняем, остальную разметку убираем.
 */
fun cleanAbcpMessage(s: String?): String? {
    if (s.isNullOrBlank()) return null
    val text = unescape(
        s.replace(Regex("(?i)<br\\s*/?>|</p>|</div>|</li>"), "\n").replace(Regex("<[^>]+>"), " ")
    ).replace(Regex("(?i)the resource is blocked\\.?"), "")
    return text.lines().map { it.replace(Regex("\\s+"), " ").trim() }.filter { it.isNotEmpty() }
        .joinToString("\n").ifBlank { null }
}

/** ABCP не оформил заказ, потому что у позиций в корзине изменились цена или наличие. */
fun isBasketChanged(message: String?): Boolean {
    val m = message?.lowercase() ?: return false
    return "изменил" in m && ("цен" in m || "налич" in m)
}

fun stripHtml(s: String): String =
    unescape(s.replace(Regex("<[^>]+>"), " ")).replace(Regex("\\s+"), " ").trim()

private fun unescape(s: String) = s.replace("&nbsp;", " ").replace("&quot;", "\"")
    .replace("&lt;", "<").replace("&gt;", ">").replace("&#039;", "'").replace("&amp;", "&")

/** Картинки: ABCP может отдать images (строки или объекты с name/url). Имя файла → imgcdn.abcp.ru. */
private fun parseImages(o: JsonObject): List<String> {
    val raw = listOf("images", "image", "imageUrl", "photos").firstNotNullOfOrNull { o.get(it) } ?: return emptyList()
    val names = when {
        raw.isJsonPrimitive -> raw.asString.split(',')
        else -> items(raw).ifEmpty { if (raw.isJsonArray) raw.asJsonArray.toList() else emptyList() }.mapNotNull { e ->
            when {
                e.isJsonPrimitive -> e.asString
                e.isJsonObject -> e.asJsonObject.str("url") ?: e.asJsonObject.str("name")
                else -> null
            }
        }
    }
    return names.map { it.trim() }.filter { it.isNotBlank() }.map { n ->
        when {
            n.startsWith("http") -> n
            n.startsWith("//") -> "https:$n"
            else -> "https://imgcdn.abcp.ru/p/full/${n.removePrefix("/")}"
        }
    }.distinct()
}

// ---------- Форматирование ----------

/** Как numberFix у ABCP: только буквы и цифры, верхний регистр. */
fun cleanNumber(s: String): String = s.uppercase().filter { it.isLetterOrDigit() }

private val rub = DecimalFormat("#,##0.00", DecimalFormatSymbols(Locale("ru")).apply { groupingSeparator = ' ' })

fun formatRub(v: Double): String = rub.format(v) + " ₽"

/** Срок предложения: подпись магазина (deadlineReplace) важнее часов. */
fun Offer.deliveryText(): String = deadlineLabel ?: formatDelivery(deliveryHours, deliveryHoursMax)

/**
 * Срок как на сайте — датой: сейчас + часы поставки («25 сент.», «3–5 окт.»).
 * Сегодня/завтра — словами. now передаётся для тестов.
 */
fun formatDelivery(hours: Int, hoursMax: Int = 0, now: Long = System.currentTimeMillis()): String {
    if (hours <= 0) return "сегодня"
    val from = dateAfter(now, hours)
    val to = if (hoursMax > hours) dateAfter(now, hoursMax) else from
    val a = dayLabel(now, from)
    val b = dayLabel(now, to)
    return when {
        a == b -> a
        // один месяц: «3–5 окт.»
        monthFmt.format(from.time) == monthFmt.format(to.time) && a.first().isDigit() ->
            "${from.get(Calendar.DAY_OF_MONTH)}–$b"
        else -> "$a – $b"
    }
}

private val dayFmt get() = SimpleDateFormat("d MMM", Locale("ru"))
private val monthFmt get() = SimpleDateFormat("MMM yyyy", Locale("ru"))

private fun dateAfter(now: Long, hours: Int): Calendar =
    Calendar.getInstance().apply { timeInMillis = now + hours * 3_600_000L }

private fun dayLabel(now: Long, c: Calendar): String {
    val today = Calendar.getInstance().apply { timeInMillis = now }
    val days = daysBetween(today, c)
    return when (days) {
        0 -> "сегодня"
        1 -> "завтра"
        else -> dayFmt.format(c.time)
    }
}

private fun daysBetween(a: Calendar, b: Calendar): Int {
    fun startOfDay(c: Calendar) = (c.clone() as Calendar).apply {
        set(Calendar.HOUR_OF_DAY, 0); set(Calendar.MINUTE, 0); set(Calendar.SECOND, 0); set(Calendar.MILLISECOND, 0)
    }.timeInMillis
    return ((startOfDay(b) - startOfDay(a)) / 86_400_000L).toInt()
}

fun formatAvailability(a: Int): String = if (a > 0) "Наличие $a шт." else "Наличие уточняется"


/** ABCP отдаёт числа по-разному: 4630, "4630.00", "4 630,00" (пробел — разделитель тысяч, бывает неразрывный). */
fun parseAbcpNumber(s: String?): Double =
    s?.filterNot { it == ' ' || it == '\u00A0' || it == '\u202F' }?.replace(',', '.')?.toDoubleOrNull() ?: 0.0
