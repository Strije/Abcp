package com.example.myapplication.abcp

import com.example.myapplication.SessionManager
import com.example.myapplication.performRequestWithRetry
import com.google.gson.JsonElement
import com.google.gson.JsonObject
import retrofit2.Response
import java.text.DecimalFormat
import java.text.DecimalFormatSymbols
import java.util.Locale
import kotlin.math.ceil

// ---------- Модели ----------

data class BrandHit(val brand: String, val number: String, val description: String)

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
    val supplier: String,
    val noReturn: Boolean
)

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
    val errorMessage: String?
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

    private val api = ApiClient.create()
    private val login get() = session.login()
    private val psw get() = session.passMd5()

    suspend fun tips(query: String): List<BrandHit> =
        items(call { api.searchTips(login, psw, query) }).mapNotNull { it.toBrandHit() }

    suspend fun brands(number: String): List<BrandHit> =
        items(call { api.searchBrands(login, psw, number) }).mapNotNull { it.toBrandHit() }

    suspend fun offers(number: String, brand: String): List<Offer> =
        items(call { api.searchArticles(login, psw, number, brand) })
            .mapNotNull { it.toOffer() }
            .sortedWith(compareBy<Offer> { it.price }.thenBy { it.deliveryHours })

    suspend fun basket(): List<BasketItem> =
        items(call { api.basketContent(login, psw) }).mapNotNull { it.toBasketItem() }

    suspend fun addToBasket(o: Offer, quantity: Int) =
        setBasketQuantity(o.brand, o.number, o.itemKey, o.supplierCode, quantity)

    suspend fun removeFromBasket(b: BasketItem) =
        setBasketQuantity(b.brand, b.number, b.itemKey, b.supplierCode, 0)

    private suspend fun setBasketQuantity(
        brand: String, number: String, itemKey: String, supplierCode: String, quantity: Int
    ) {
        val fields = auth() + mapOf(
            "positions[0][brand]" to brand,
            "positions[0][number]" to number,
            "positions[0][itemKey]" to itemKey,
            "positions[0][supplierCode]" to supplierCode,
            "positions[0][quantity]" to quantity.toString()
        )
        val r = call(retry = false) { api.basketAdd(fields) }.asJsonObjectOrNull()
        if (r?.str("status") == "0") {
            val pos = r.get("positions")?.let { items(it) }?.firstOrNull()?.asJsonObjectOrNull()
            throw AbcpException(pos?.str("errorMessage") ?: "Не удалось изменить корзину")
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

    /** Возвращает номера созданных заказов. */
    suspend fun placeOrder(c: OrderChoice): List<String> {
        val fields = buildMap {
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
        if (numbers.isEmpty()) throw AbcpException(r.str("errorMessage") ?: "Заказ не оформлен")
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

private fun JsonObject.num(key: String): Double = str(key)?.replace(',', '.')?.toDoubleOrNull() ?: 0.0

private fun JsonObject.int(key: String): Int = num(key).toInt()

private fun JsonElement.toBrandHit(): BrandHit? {
    val o = asJsonObjectOrNull() ?: return null
    return BrandHit(
        brand = o.str("brand") ?: return null,
        number = o.str("number") ?: return null,
        description = o.str("description").orEmpty()
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
        supplier = o.str("supplierDescription") ?: o.str("distributorCode").orEmpty(),
        noReturn = o.str("noReturn").let { it == "1" || it == "true" }
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
        errorMessage = o.str("errorMessage")
    )
}

// ---------- Форматирование ----------

/** Как numberFix у ABCP: только буквы и цифры, верхний регистр. */
fun cleanNumber(s: String): String = s.uppercase().filter { it.isLetterOrDigit() }

private val rub = DecimalFormat("#,##0.00", DecimalFormatSymbols(Locale("ru")).apply { groupingSeparator = ' ' })

fun formatRub(v: Double): String = rub.format(v) + " ₽"

/** Срок из часов ABCP в человеческий вид. */
fun formatDelivery(hours: Int, hoursMax: Int = 0): String {
    if (hours <= 0) return "сегодня"
    val d = ceil(hours / 24.0).toInt()
    val dMax = ceil(hoursMax / 24.0).toInt()
    return if (dMax > d) "$d–$dMax дн." else "$d дн."
}

fun formatAvailability(a: Int): String = if (a > 0) "Наличие $a шт." else "Наличие уточняется"
