package com.example.myapplication.abcp

import com.example.myapplication.SessionManager
import com.example.myapplication.performRequestWithRetry
import com.google.gson.JsonElement
import com.google.gson.JsonObject
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.sync.Semaphore
import kotlinx.coroutines.sync.withPermit
import kotlinx.coroutines.withContext
import retrofit2.Response
import java.text.DecimalFormat
import java.text.DecimalFormatSymbols
import java.util.Locale
import java.text.SimpleDateFormat
import java.util.Calendar

// ---------- Модели ----------

/** Позиция старого заказа для «Повторить заказ»: бренд, номер, количество, старая цена за штуку. */
data class RepeatSource(val brand: String, val number: String, val qty: Int, val oldPrice: Double) {
    val title get() = "$brand $number".trim()
}

/**
 * Что нашлось сейчас: лучшее предложение (или null — нет в продаже) и количество с учётом упаковки.
 * checkFailed — выдачу получить не удалось (сеть): это не «нет в продаже».
 */
data class RepeatLine(val source: RepeatSource, val offer: Offer?, val qty: Int, val checkFailed: Boolean = false)

/** Строка «Повторить заказ» по выдаче: found = null — выдачу получить не удалось. */
internal fun repeatLine(src: RepeatSource, found: List<Offer>?): RepeatLine {
    val fix = cleanNumber(src.number)
    // Бренд в заказе и в выдаче пишут по-разному (MANN-FILTER / MANN) — сравниваем нормализованные
    val best = found.orEmpty()
        .filter { offerBrandKey(it.brand) == offerBrandKey(src.brand) && it.numberFix.equals(fix, true) }
        .minWithOrNull(compareBy<Offer> { !it.inStore }.thenBy { it.deliveryHours }.thenBy { it.price })
    val qty = best?.let { val pack = it.packing.coerceAtLeast(1); ((src.qty.coerceAtLeast(1) + pack - 1) / pack) * pack } ?: src.qty
    return RepeatLine(src, best, qty, checkFailed = found == null)
}

/** Итог голосования по брендам номера: кого открыть сразу (или null) и у скольких поставщиков каждый проверенный. */
data class BrandVotes(val pick: BrandHit?, val suppliers: Map<String, Int>)

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

/** Сколько других брендов в списке упоминают этот бренд в описании («ан. MAHLE OC90» → голос за MAHLE). */
internal fun brandMentions(h: BrandHit, found: List<BrandHit>): Int {
    // «Mahle/Knecht» в описаниях пишут по частям: «ан. MAHLE OC90», «OC90_ KNECHT» (телефон 10.11: упоминаний было 0)
    val keys = (h.brand.split('/', ',', '(', ')').map(::brandKey) + brandKey(h.brand)).filter { it.length >= 3 }.toSet()
    if (keys.isEmpty()) return 0
    return found.count { other -> other !== h && brandKey(other.description).let { d -> keys.any { d.contains(it) } } }
}

/**
 * Кого проверять (каждый — запрос выдачи): брендов по номеру бывает несколько десятков по алфавиту, и все
 * «в наличии». Первыми — кого упоминают другие, потом в наличии; до 8.
 */
internal fun brandCandidates(found: List<BrandHit>): List<BrandHit> =
    found.sortedWith(compareByDescending<BrandHit> { brandMentions(it, found) }.thenByDescending { it.available })
        .take(8)

/** Порог как в Прайсере (search.choose_brand): лидер у половины ответивших поставщиков и в 1,5 раза впереди второго. */
internal const val BRAND_AUTO_SHARE = 0.5
internal const val BRAND_AUTO_LEAD = 1.5

/**
 * Выбор бренда голосованием, как в Прайсере: голос — поставщик, который даёт этот бренд с этим номером
 * ([votes] — бренд → разные поставщики), плюс упоминания в описаниях других вариантов. Лидер по баллу
 * (голоса + упоминания) берётся сам, если его дают не меньше половины всех ответивших поставщиков и балл
 * в 1,5 раза выше второго; иначе null — выбирает клиент. Прайсер на записи 02.10: W71295 — MANN 7 против
 * Redskin 4 берёт сам, OC90 — MAHLE 7+2 против AM POINT 7 спрашивает.
 */
internal fun pickDominant(found: List<BrandHit>, votes: List<Pair<BrandHit, Set<String>>>): BrandHit? {
    val answered = votes.flatMap { it.second }.toSet().size
    if (answered == 0) return null
    data class Row(val hit: BrandHit, val votes: Int, val score: Int)
    val rows = votes.map { (h, providers) -> Row(h, providers.size, providers.size + brandMentions(h, found)) }
        .sortedWith(compareByDescending<Row> { it.score }.thenByDescending { it.votes })
    val first = rows.firstOrNull() ?: return null
    if (rows.size == 1) return first.hit.takeIf { first.votes > 0 }
    val second = rows[1]
    return first.hit.takeIf {
        first.votes >= BRAND_AUTO_SHARE * answered && first.score >= BRAND_AUTO_LEAD * maxOf(second.score.toDouble(), 0.5)
    }
}

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
    val updatedAt: String? = null,
    /** Поставщик в ABCP (distributorId) — для подсчёта ★ */
    val distributorId: String = "",
    /** ★N: у скольких разных поставщиков есть этот бренд+артикул (гостям считает сервер) */
    val confirm: Int = 0
) {
    /** Товар лежит в нашем магазине — забрать можно сегодня */
    val inStore: Boolean get() = deliveryHours <= 0
}

enum class BadgeKind { Good, Bad, Info }

/** icon — класс Font Awesome из HTML ABCP (house-circle-check…), color — его цвет на сайте (blue/red/yellow). */
data class SupplierBadge(val text: String, val kind: BadgeKind, val icon: String? = null, val color: String? = null)

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
    val basketName: String? = null,
    /** Код детали на сайте — запасной способ удалить позицию */
    val code: String = ""
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

open class AbcpException(message: String) : Exception(message)

/** Нет связи или таймаут: запрос мог дойти до ABCP, а мог и нет — в отличие от отказа самого ABCP. */
class AbcpNetworkError(message: String) : AbcpException(message)

/** Меняли количество (удалить + добавить), а вернуть позицию в корзину не вышло. Экран показывает это диалогом. */
class BasketPositionLost(message: String) : AbcpException(message)

/** Корзины, которые надо очистить и собрать заново: только те, где лежат удаляемые позиции. Остальные не трогаем. */
internal fun basketsToRebuild(all: List<BasketItem>, drop: Set<BasketItem>): Map<String?, List<BasketItem>> {
    val ids = drop.map { it.basketId }.toSet()
    return all.filter { it.basketId in ids }.groupBy { it.basketId }
}

/** Сколько штук этой позиции лежит в корзине (itemKey у ABCP не уникален — сверяем ещё бренд и номер). */
internal fun basketQuantity(content: List<BasketItem>, b: BasketItem): Int = content.filter {
    it.basketId == b.basketId && it.itemKey == b.itemKey && it.supplierCode == b.supplierCode &&
        cleanNumber(it.number) == cleanNumber(b.number) && offerBrandKey(it.brand) == offerBrandKey(b.brand)
}.sumOf { it.quantity }

/** Разбор ответа не удался (ABCP прислал другой вид данных) — это не «нет интернета». */
internal fun isParseError(e: Throwable): Boolean = e is com.google.gson.JsonParseException || e is IllegalStateException

/** 103: клиенту ещё не включены права на API (включает менеджер по заявке). */
class NoApiAccess(message: String) : AbcpException(message)

/** ABCP ответил на basket/add, но позицию не принял (status=0) — в отличие от сбоя связи. */
class BasketRejected(message: String) : AbcpException(message)

/** Позиция в корзине устарела, и ABCP не удаляет её поштучно — только очисткой корзины. */
class StaleBasketItem(reason: String?) : AbcpException(
    "Эта позиция устарела (поставщик обновил прайс), и удалить её отдельно не получается" +
        (reason?.takeIf { it.isNotBlank() }?.let { ".\n$it" } ?: ".")
)

// ---------- Репозиторий ----------

class AbcpShop(private val session: SessionManager) {

    companion object {
        /** Справочник статусов на час — один на всё приложение */
        @Volatile private var statusCache: Pair<Long, Set<String>>? = null
        /** Поиск вошедшего клиента ушёл на гостевые цены (нет доступа к API) — показать пометку над выдачей */
        @Volatile var guestPrices = false
    }

    private val api = ApiClient.create()
    private val login get() = session.login()
    private val psw get() = session.passMd5()

    /** Гость — не вошёл в аккаунт: поиск идёт через наш сервер с ценами гостевого профиля */
    val isGuest: Boolean get() = !session.isLoggedIn()
    private val server by lazy { com.example.myapplication.server.AppServer(session.context) }

    suspend fun brands(number: String): List<BrandHit> = emptyIfNoResults {
        items(if (isGuest) server.guestBrands(number) else orGuest({ server.guestBrands(number) }) { call { api.searchBrands(login, psw, number) } })
            .mapNotNull { it.toBrandHit() }
    }

    /**
     * Доступ к поиску ещё не включён (заявка у менеджера) — не тупик с ошибкой, а цены гостевого профиля через наш
     * сервер, как без входа. Корзина заработает, когда менеджер включит доступ ([guestPrices] — для пометки на экране).
     */
    private suspend fun orGuest(guest: suspend () -> JsonElement, own: suspend () -> JsonElement): JsonElement = try {
        own()
    } catch (e: NoApiAccess) {
        guestPrices = true
        guest()
    }

    suspend fun offers(number: String, brand: String, all: Boolean = false): List<Offer> = emptyIfNoResults {
        items(
            if (isGuest) server.guestOffers(number, brand, all)
            else orGuest({ server.guestOffers(number, brand, all) }) {
                call { api.searchArticles(login, psw, number, brand, disableFiltering = if (all) 1 else 0) }
            }
        )
            .mapNotNull { it.toOffer() }
            // Цена 0 или меньше — ошибка прайса поставщика, заказать такое нельзя
            .filter { it.price > 0 }
            .sortedWith(compareBy<Offer> { it.price }.thenBy { it.deliveryHours })
    }

    /**
     * «Ничего не нашлось» ABCP присылает ошибкой «No results». Для клиента это пустой список
     * («Предложений не найдено»), а не красная ошибка по-английски (AppMetrica: 563 таких «ошибки» за месяц).
     */
    private inline fun <T> emptyIfNoResults(block: () -> List<T>): List<T> = try {
        block()
    } catch (e: Exception) {
        if (e.message?.contains("no results", ignoreCase = true) == true) emptyList() else throw e
    }

    /**
     * Брендов по номеру несколько, а однозначного нет: голосование поставщиков, как в Прайсере. Кандидаты —
     * [brandCandidates]; по каждому — какие поставщики дают именно этот бренд и номер; выбор — [pickDominant].
     * null — клиент выбирает сам.
     */
    suspend fun dominantBrand(found: List<BrandHit>): BrandVotes =
        // Сервер знает больше (синонимы ABCP «Mahle/Knecht» = MAHLE, уровень фирм, все поставщики) и отвечает одним
        // запросом; недоступен — голосуем здесь, по тем же правилам
        runCatching { serverChoice(found) }.getOrNull() ?: localVotes(found)

    private suspend fun serverChoice(found: List<BrandHit>): BrandVotes? {
        val number = found.firstOrNull()?.number ?: return null
        val o = server.brandChoice(number)
        val brand = o.get("brand")?.takeIf { !it.isJsonNull }?.asString
        val suppliers = o.getAsJsonObject("suppliers")?.entrySet()?.associate { (k, v) -> k to v.asInt }.orEmpty()
        return BrandVotes(found.firstOrNull { it.brand == brand }, suppliers)
    }

    private suspend fun localVotes(found: List<BrandHit>): BrandVotes = kotlinx.coroutines.coroutineScope {
        val counts = brandCandidates(found).map { h ->
            async {
                val fix = cleanNumber(h.number)
                val providers = runCatching { offers(h.number, h.brand) }.getOrDefault(emptyList())
                    .filter { offerBrandKey(it.brand) == offerBrandKey(h.brand) && it.numberFix.equals(fix, true) }
                    .map(::offerProvider).toSet()
                h to providers
            }
        }.awaitAll()
        BrandVotes(pickDominant(found, counts), counts.associate { (h, p) -> h.brand to p.size })
    }

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

    suspend fun deleteFromGarage(carId: String) {
        call(retry = false) { api.garageDelete(auth() + mapOf("carId" to carId)) }
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

    /** Одна корзина по её id (null — основная) — для сверки после сбоя. */
    private suspend fun basketContentById(basketId: String?): List<BasketItem> =
        items(call { api.basketContent(login, psw, basketId) }).mapNotNull { it.toBasketItem()?.copy(basketId = basketId) }

    /**
     * «Повторить заказ», шаг 1 — только подбор, в корзину ничего не кладём: для каждой позиции то же
     * предложение по бренду и номеру, лучшее сейчас (в магазине → быстрее → дешевле), количество — с учётом упаковки.
     * Цены и сроки — сегодняшние; клиент видит их рядом со старой ценой и сам решает, что добавить.
     */
    suspend fun findRepeat(positions: List<RepeatSource>): List<RepeatLine> = coroutineScope {
        // Заказ на 30 позиций по одной ждать долго, а все разом — упрёмся в ограничения ABCP
        val gate = Semaphore(4)
        positions.map { src ->
            async {
                gate.withPermit {
                    val found = try {
                        offers(src.number, src.brand)
                    } catch (e: CancellationException) {
                        throw e
                    } catch (e: Exception) {
                        // Сбой связи — не «нет в продаже»: строка получит checkFailed
                        null
                    }
                    repeatLine(src, found)
                }
            }
        }.awaitAll()
    }

    /** Шаг 2 — отмеченные строки в корзину. Возвращает, что не добавилось. */
    suspend fun addRepeat(lines: List<RepeatLine>): List<String> =
        lines.filter { it.offer != null }.mapNotNull { l ->
            if (runCatching { addToBasket(l.offer!!, l.qty) }.isSuccess) null else l.source.title
        }

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
            // Между удалением и добавлением уходить нельзя: уйдёт клиент с экрана — позиция пропадёт
            else -> withContext(NonCancellable) {
                try {
                    removeFromBasket(b)
                } catch (e: AbcpNetworkError) {
                    // Ответ на удаление потерялся: позиция на месте — ничего не изменилось, отдаём ошибку;
                    // пропала — удаление прошло, возвращаем её с новым количеством
                    val content = try {
                        basketContentById(b.basketId)
                    } catch (c: Exception) {
                        throw e
                    }
                    if (basketQuantity(content, b) > 0) throw e
                }
                val title = "${b.brand} ${b.number}".trim()
                when (addChecked(b, newQty)) {
                    true -> Unit
                    false -> throw BasketPositionLost(
                        "Изменить количество не получилось, и позиции «$title» сейчас нет в корзине. " +
                            "Сейчас обновим корзину — добавьте позицию заново."
                    )
                    null -> throw BasketPositionLost(
                        "Связь прервалась, и мы не успели проверить корзину. Сейчас обновим её — " +
                            "если позиции «$title» нет, добавьте её заново."
                    )
                }
            }
        }
    }

    /**
     * Добавить позицию с проверкой. Запрос упал — ответ мог потеряться, а позиция лечь в корзину, и слепой повтор
     * задвоил бы количество. Поэтому перечитываем корзину и повторяем (один раз), только если позиции там нет.
     * [already] — сколько таких же штук уже лежит. true — в корзине, false — нет, null — проверить не удалось.
     */
    private suspend fun addChecked(b: BasketItem, qty: Int, already: Int = 0): Boolean? {
        repeat(2) {
            try {
                setBasketQuantity(b.brand, b.number, b.itemKey, b.supplierCode, qty, b.basketId)
                return true
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                val content = try {
                    basketContentById(b.basketId)
                } catch (c: CancellationException) {
                    throw c
                } catch (c: Exception) {
                    return null
                }
                if (basketQuantity(content, b) > already) return true
            }
        }
        return false
    }

    /**
     * Удаление — тем же набором бренд/номер/itemKey/код поставки с количеством 0. У устаревшей позиции
     * (прайс поставщика обновился) ABCP по этому набору её может не найти — тогда пробуем по коду детали.
     * Не вышло и так — бросаем [StaleBasketItem]: удалить её можно только очисткой корзины ([rebuildBasket]).
     */
    suspend fun removeFromBasket(b: BasketItem) {
        val first = runCatching { setBasketQuantity(b.brand, b.number, b.itemKey, b.supplierCode, 0, b.basketId) }
        if (first.isSuccess) return
        // Нет связи и прочие сбои — не «устарела», показываем как есть
        first.exceptionOrNull()?.let { if (it !is BasketRejected) throw it }
        if (b.code.isNotBlank() && runCatching { removeByCode(b) }.isSuccess) return
        throw StaleBasketItem(first.exceptionOrNull()?.message)
    }

    private suspend fun removeByCode(b: BasketItem) {
        val fields = auth() + (b.basketId?.let { mapOf("basketId" to it) } ?: emptyMap()) + mapOf(
            "positions[0][code]" to b.code,
            "positions[0][itemKey]" to b.itemKey,
            "positions[0][quantity]" to "0"
        )
        val r = call(retry = false) { api.basketAdd(fields) }.asJsonObjectOrNull()
        if (r?.str("status") == "0") throw AbcpException("Не удалось удалить позицию")
    }

    /**
     * Очистить корзину (basket/clear) и положить обратно всё, кроме [drop]. Трогаем только корзины, где есть
     * позиции из [drop]. Возвращает то, что вернуть не удалось (устарело само или подвела связь).
     * Для позиций, которые ABCP не удаляет поштучно.
     */
    suspend fun rebuildBasket(all: List<BasketItem>, drop: Set<BasketItem>): List<String> =
        // Прерывать нельзя: корзина уже очищена, уход с экрана на полпути оставил бы клиента без товаров
        withContext(NonCancellable) {
            val failed = mutableListOf<String>()
            try {
                basketsToRebuild(all, drop).forEach { (basketId, items) ->
                    clearBasket(basketId)
                    val restored = mutableListOf<BasketItem>()
                    items.filter { it !in drop }.forEach { b ->
                        if (addChecked(b, b.quantity, basketQuantity(restored, b)) == true) {
                            restored.add(b)
                        } else {
                            failed.add("${b.brand} ${b.number}".trim())
                        }
                    }
                }
            } catch (e: Exception) {
                // Упали на следующей корзине — не теряем список того, что не вернулось в предыдущей
                if (failed.isEmpty()) throw e
                throw AbcpException(com.example.myapplication.ui.userMessage(e, "Не удалось очистить корзину") + "\nНе удалось вернуть в корзину: ${failed.joinToString()}")
            }
            failed
        }

    private suspend fun clearBasket(basketId: String?) {
        val r = try {
            call(retry = false) {
                api.basketClear(auth() + (basketId?.let { mapOf("basketId" to it) } ?: emptyMap()))
            }.asJsonObjectOrNull()
        } catch (e: AbcpNetworkError) {
            // Ответ потерялся, а корзина могла очиститься: пуста — продолжаем возвращать позиции, нет — ничего не тронуто
            if (basketContentById(basketId).isNotEmpty()) throw e
            return
        }
        if (r?.str("status") == "0") throw AbcpException(cleanAbcpMessage(r.str("errorMessage")) ?: "Не удалось очистить корзину")
    }

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
            throw BasketRejected(explainAbcpError(cleanAbcpMessage(pos?.str("errorMessage")) ?: "Не удалось изменить корзину"))
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
        if (numbers.isEmpty()) throw AbcpException(explainAbcpError(cleanAbcpMessage(r.str("errorMessage")) ?: "Заказ не оформлен"))
        return numbers
    }

    private fun auth() = mapOf("userlogin" to login, "userpsw" to psw)

    /** retry только для чтения: повтор basket/add или basket/order задвоил бы позицию/заказ. */
    private suspend fun call(
        retry: Boolean = true,
        request: suspend () -> Response<JsonElement>
    ): JsonElement {
        val resp = try {
            if (retry) performRequestWithRetry { request() } else request()
        } catch (e: java.io.IOException) {
            throw AbcpNetworkError(networkErrorText(e))
        }
        if (!resp.isSuccessful) {
            val raw = resp.errorBody()?.string()
            val text = prettifyAbcpError(raw, resp.code())
            throw if (abcpErrorCode(raw) == 103) NoApiAccess(text) else AbcpException(text)
        }
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
        updatedAt = o.str("lastUpdateTime"),
        distributorId = o.str("distributorId").orEmpty(),
        confirm = o.int("confirmCount")
    )
}

// ---------- ★N: сколько разных поставщиков предлагают тот же бренд+артикул ----------

/** Бренды, которые разные поставщики пишут по-разному (ключ — после нормализации). */
private val BRAND_ALIASES = mapOf(
    "MANNFILTER" to "MANN", "HYUNDAIKIA" to "HYUNDAIKIA", "HYUNDAIMOBIS" to "HYUNDAIKIA", "MOBIS" to "HYUNDAIKIA",
    "GENERALMOTORS" to "GM", "MERCEDESBENZ" to "MERCEDES", "LEMFORDER" to "LEMFOERDER", "TRW" to "TRW",
)

fun offerArticleKey(s: String) = s.uppercase().filter { it in 'A'..'Z' || it in '0'..'9' || it in 'А'..'Я' || it == 'Ё' }

fun offerBrandKey(s: String): String {
    val k = s.uppercase().filter { it in 'A'..'Z' || it in '0'..'9' || it in 'А'..'Я' || it == 'Ё' }
    return BRAND_ALIASES[k] ?: k
}

/** Склады АвтоДруг (Хрусталёва, ПОР) в ABCP — разные поставщики, но для ★ это один поставщик. */
fun offerProvider(o: Offer): String =
    if (o.inStore && o.deadlineLabel?.contains("самовывоз", ignoreCase = true) == true) "AVTODRUG"
    else o.distributorId.ifBlank { o.supplierCode }

/**
 * ★N по всей выдаче сразу (до сворачивания «ещё N»): для каждого бренда+артикула — сколько разных
 * поставщиков. Нет данных о поставщиках (гость) — оставляем посчитанное сервером.
 */
fun withConfirmations(offers: List<Offer>): List<Offer> {
    if (offers.all { it.distributorId.isBlank() }) return offers
    val groups = HashMap<Pair<String, String>, MutableSet<String>>()
    offers.forEach { o ->
        val key = offerBrandKey(o.brand) to offerArticleKey(o.number)
        if (key.first.isNotEmpty() && key.second.isNotEmpty()) groups.getOrPut(key) { HashSet() } += offerProvider(o)
    }
    return offers.map { o -> o.copy(confirm = groups[offerBrandKey(o.brand) to offerArticleKey(o.number)]?.size ?: 0) }
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
        errorMessage = cleanAbcpMessage(o.str("errorMessage")),
        code = o.str("code").orEmpty()
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
        val icon = Regex("\\bfa-(?!solid|regular|brands)([a-z0-9-]+)").find(cls)?.groupValues?.get(1)
        val color = Regex("\\b(red|blue|yellow|green|orange|gray|grey)\\b").find(cls)?.groupValues?.get(1)
        SupplierBadge(unescape(title), kind, icon, color)
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
    return ("изменил" in m || "изменились" in m) && ("цен" in m || "налич" in m)
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
private val rubWhole = DecimalFormat("#,##0", DecimalFormatSymbols(Locale("ru")).apply { groupingSeparator = ' ' })

/** Целые суммы — без копеек («820 ₽»), дробные — с копейками («820,50 ₽»). */
fun formatRub(v: Double): String {
    val kopecks = Math.round(v * 100)
    return (if (kopecks % 100 == 0L) rubWhole.format(kopecks / 100) else rub.format(v)) + " ₽"
}

private val MONTHS_SHORT = listOf("янв.", "февр.", "мар.", "апр.", "мая", "июн.", "июл.", "авг.", "сент.", "окт.", "нояб.", "дек.")
private val ABCP_DATE = Regex("^(\\d{4})-(\\d{2})-(\\d{2})(?:[ T](\\d{2}):(\\d{2})(?::\\d{2})?)?$")

/**
 * Дата ABCP для человека: «2026-11-10 14:23:05» → «10 нояб., 14:23», «2026-11-10» → «10 нояб.».
 * Год — только если не текущий («10 нояб. 2025»). Нераспознанное возвращаем как есть. now — для тестов.
 */
fun formatAbcpDate(raw: String?, now: Long = System.currentTimeMillis()): String {
    val s = raw?.trim().orEmpty()
    val m = ABCP_DATE.find(s) ?: return s
    val year = m.groupValues[1].toInt()
    val month = m.groupValues[2].toInt()
    val day = m.groupValues[3].toInt()
    if (month !in 1..12 || day !in 1..31) return s
    val thisYear = Calendar.getInstance().apply { timeInMillis = now }.get(Calendar.YEAR)
    val date = "$day ${MONTHS_SHORT[month - 1]}" + if (year != thisYear) " $year" else ""
    val time = if (m.groupValues[4].isNotEmpty()) ", ${m.groupValues[4]}:${m.groupValues[5]}" else ""
    return date + time
}

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
