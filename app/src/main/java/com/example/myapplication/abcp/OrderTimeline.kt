package com.example.myapplication.abcp

import com.google.gson.JsonElement
import com.google.gson.JsonObject
import com.google.gson.JsonParser
import java.util.Calendar
import java.util.GregorianCalendar
import java.util.Locale
import java.util.TimeZone

// Путь заказа для клиента: этапы, смысл статусов и история их смены.
// Чистая логика без Android — проверяется юнит-тестами (OrderTimelineTest).

const val KIND_NORMAL = "normal"
/** От клиента что-то нужно: оплатить, ответить менеджеру */
const val KIND_ACTION = "action"
/** Задержка */
const val KIND_WARN = "warn"
/** Отказ, возврат, не забрано — позиция дальше не поедет */
const val KIND_STOP = "stop"
/** Выдано */
const val KIND_DONE = "done"

/** Этапы по умолчанию — если сервер свой список не прислал. Номер этапа — с единицы. */
val DEFAULT_STAGES = listOf("Принят", "Заказан", "В пути", "Готов к выдаче", "Выдан")

/** Что значит статус: этап 1..5 (0 — вне шкалы), подсказка клиенту и вид ([KIND_NORMAL] и т.д.). */
data class StatusInfo(val stage: Int, val hint: String, val kind: String)

/** Шаг истории: статус и когда он был поставлен (unix-время в секундах). */
data class Step(val status: String, val ts: Long)

/**
 * История заказа с нашего сервера. steps — шаги по id позиции (равен positionId в карточке), от старых к новым;
 * titles — «бренд номер» по тому же id.
 */
data class OrderHistory(
    val created: Long = 0,
    val stages: List<String> = DEFAULT_STAGES,
    val steps: Map<String, List<Step>> = emptyMap(),
    val statuses: Map<String, StatusInfo> = emptyMap(),
    val titles: Map<String, String> = emptyMap()
)

private fun JsonElement?.textOrNull(): String? =
    this?.takeIf { it.isJsonPrimitive }?.asString?.trim()?.takeIf { it.isNotEmpty() }

private fun JsonElement?.longOrNull(): Long? =
    this?.takeIf { it.isJsonPrimitive }?.let { runCatching { it.asDouble.toLong() }.getOrNull() }

private fun JsonObject.child(key: String): JsonElement? = get(key)?.takeIf { !it.isJsonNull }

private val KNOWN_KINDS = setOf(KIND_NORMAL, KIND_ACTION, KIND_WARN, KIND_STOP, KIND_DONE)

/**
 * Разбор ответа /v1/orders/{номер}/history. Терпимый: сервер может добавить поля или не прислать часть —
 * карточка заказа из-за этого ломаться не должна. Не JSON-объект — null.
 */
fun parseOrderHistory(json: String): OrderHistory? {
    val root = runCatching { JsonParser.parseString(json) }.getOrNull()
        ?.takeIf { it.isJsonObject }?.asJsonObject ?: return null
    return runCatching {
        // Шкала в приложении — на пять точек; другой список этапов не подойдёт
        val stages = root.child("stages")?.takeIf { it.isJsonArray }?.asJsonArray
            ?.mapNotNull { it.textOrNull() }?.takeIf { it.size == DEFAULT_STAGES.size } ?: DEFAULT_STAGES

        val steps = LinkedHashMap<String, List<Step>>()
        val titles = LinkedHashMap<String, String>()
        root.child("positions")?.takeIf { it.isJsonArray }?.asJsonArray?.forEach { el ->
            val p = el.takeIf { it.isJsonObject }?.asJsonObject ?: return@forEach
            val id = p.child("id").textOrNull() ?: return@forEach
            steps[id] = p.child("steps")?.takeIf { it.isJsonArray }?.asJsonArray?.mapNotNull { s ->
                val o = s.takeIf { it.isJsonObject }?.asJsonObject ?: return@mapNotNull null
                val status = o.child("status").textOrNull() ?: return@mapNotNull null
                Step(status, o.child("ts").longOrNull() ?: 0L)
            }.orEmpty().sortedBy { it.ts }
            val title = listOfNotNull(p.child("brand").textOrNull(), p.child("number").textOrNull()).joinToString(" ")
            if (title.isNotEmpty()) titles[id] = title
        }

        val statuses = LinkedHashMap<String, StatusInfo>()
        root.child("statuses")?.takeIf { it.isJsonObject }?.asJsonObject?.entrySet()?.forEach { (name, v) ->
            val o = v.takeIf { it.isJsonObject }?.asJsonObject ?: return@forEach
            if (name.isBlank()) return@forEach
            statuses[name] = StatusInfo(
                stage = (o.child("stage").longOrNull() ?: 1L).toInt().coerceIn(0, DEFAULT_STAGES.size),
                hint = o.child("hint").textOrNull().orEmpty(),
                kind = o.child("kind").textOrNull()?.takeIf { it in KNOWN_KINDS } ?: KIND_NORMAL
            )
        }

        OrderHistory(
            created = root.child("created").longOrNull() ?: 0L,
            stages = stages, steps = steps, statuses = statuses, titles = titles
        )
    }.getOrNull()
}

// ---------- запасная таблица статусов ----------

/** note — короткая пометка для списка заказов (только там, где от клиента что-то нужно или что-то пошло не так). */
private class StatusRule(val words: List<String>, val info: StatusInfo, val note: String? = null)

private fun rule(words: String, stage: Int, kind: String, hint: String, note: String? = null) =
    StatusRule(words.split('|'), StatusInfo(stage, hint, kind), note)

// Порядок важен: отказы, возврат и «не забрано» — раньше общих слов.
// «Отказ складом (Не подтверждён поставщиком)» не должен попасть в «подтверждён», «Не забрано» — в «готов»,
// а «Подготовил предложение» — в «готов» (подГОТОВил).
private val STATUS_RULES = listOf(
    rule("отказ клиент", 0, KIND_STOP, "Позиция отменена по вашей просьбе."),
    rule("не забран", 0, KIND_STOP, "Заказ не забрали вовремя. Напишите нам, если он ещё нужен.", "Не забран"),
    rule("возврат", 0, KIND_STOP, "По позиции оформлен возврат."),
    rule(
        "отказ|не подтвержд", 0, KIND_STOP,
        "Поставщик не подтвердил наличие. Менеджер предложит замену — или напишите нам в чат.", "Не подтверждён поставщиком"
    ),
    rule(
        "ожидает оплаты", 1, KIND_ACTION,
        "Заказ подтверждён и ждёт оплаты. Оплатить можно в приложении или в магазине.", "Ждёт оплаты"
    ),
    rule(
        "ожидает обработки", 1, KIND_NORMAL,
        "Заказ принят. Менеджер проверит наличие и подтвердит — обычно в течение рабочего часа."
    ),
    rule("к проверке", 1, KIND_NORMAL, "Менеджер проверяет заказ."),
    rule(
        "предложение", 1, KIND_ACTION,
        "Менеджер подобрал вариант. Посмотрите ответ менеджера в позиции или напишите нам в чат.", "Нужен ваш ответ"
    ),
    rule("к заказу", 2, KIND_NORMAL, "Оплата получена — заказываем деталь у поставщика."),
    rule(
        "задерж", 3, KIND_WARN,
        "Поставщик задерживает деталь. Мы следим и сообщим, когда она приедет.", "Задерживается"
    ),
    rule("в пути", 3, KIND_NORMAL, "Деталь заказана у поставщика и едет в магазин."),
    rule("готов", 4, KIND_NORMAL, "Деталь в магазине — можно забирать."),
    rule("выдан", 5, KIND_DONE, "Заказ выдан. Спасибо за покупку!")
)

private val UNKNOWN_STATUS = StatusInfo(1, "", KIND_NORMAL)

private fun ruleFor(name: String): StatusRule? {
    val s = name.trim().lowercase()
    if (s.isEmpty()) return null
    return STATUS_RULES.firstOrNull { r -> r.words.any { it in s } }
}

/**
 * Что значит статус. Сначала — таблица с сервера (её правит магазин): точное название, затем по вхождению
 * («Отказ складом» ↔ «Отказ складом (Не подтверждён поставщиком)»); иначе — таблица, зашитая в приложение.
 */
fun statusInfo(name: String?, fromServer: Map<String, StatusInfo>? = null): StatusInfo {
    val raw = name?.trim().orEmpty()
    if (raw.isEmpty()) return UNKNOWN_STATUS
    val local = ruleFor(raw)?.info ?: UNKNOWN_STATUS
    if (fromServer.isNullOrEmpty()) return local
    val s = raw.lowercase()
    val found = fromServer[raw]
        ?: fromServer.entries.firstOrNull { it.key.trim().lowercase() == s }?.value
        // Из нескольких подходящих — самое длинное название: оно точнее
        ?: fromServer.entries
            .filter { e -> e.key.trim().lowercase().let { k -> k.isNotEmpty() && (k in s || s in k) } }
            .maxByOrNull { it.key.trim().length }?.value
        ?: return local
    // Подсказку сервер мог не написать — тогда нашу
    return if (found.hint.isBlank()) found.copy(hint = local.hint) else found
}

/** Короткая пометка для списка заказов: «Ждёт оплаты», «Задерживается»… null — всё идёт своим чередом. */
fun statusNote(name: String?): String? = ruleFor(name.orEmpty())?.note

/** Отказ поставщика (не отмена самим клиентом). */
private fun isSupplierRefusal(name: String): Boolean {
    val s = name.lowercase()
    return "отказ клиент" !in s && ("отказ" in s || "не подтвержд" in s)
}

/**
 * Где сейчас заказ целиком.
 * stage — этап 1..5; 0 — все позиции в отказе/возврате. lead — статус, который определил этап (по нему подсказка).
 * delayed — сколько позиций задерживается; actions — статусы, где от клиента что-то нужно;
 * refused — сколько позиций не подтвердил поставщик; active — сколько ещё в работе; total — всего.
 */
data class OrderStage(
    val stage: Int,
    val lead: String?,
    val delayed: Int,
    val actions: List<String>,
    val refused: Int,
    val active: Int,
    val total: Int
)

/**
 * Этап заказа — самый ранний среди позиций, которые ещё в работе: заказ готов, когда готово всё.
 * Все выданы (отказанные не в счёт) — 5; все в отказе/возврате — 0.
 */
fun orderStage(positionStatuses: List<String>, fromServer: Map<String, StatusInfo>? = null): OrderStage {
    val all = positionStatuses.filter { it.isNotBlank() }.map { it to statusInfo(it, fromServer) }
    val active = all.filter { it.second.kind != KIND_STOP && it.second.kind != KIND_DONE }
    val lead: Pair<String, StatusInfo>? = when {
        active.isNotEmpty() -> active.minByOrNull { it.second.stage.coerceIn(1, DEFAULT_STAGES.size) }
        else -> all.firstOrNull { it.second.kind == KIND_DONE } ?: all.firstOrNull()
    }
    val stage = when {
        // Статусов нет совсем — заказ только что принят
        lead == null -> 1
        lead.second.kind == KIND_DONE -> DEFAULT_STAGES.size
        lead.second.kind == KIND_STOP -> 0
        else -> lead.second.stage.coerceIn(1, DEFAULT_STAGES.size)
    }
    return OrderStage(
        stage = stage,
        lead = lead?.first,
        delayed = active.count { it.second.kind == KIND_WARN },
        actions = active.filter { it.second.kind == KIND_ACTION }.map { it.first }.distinct(),
        refused = all.count { it.second.kind == KIND_STOP && isSupplierRefusal(it.first) },
        active = active.size,
        total = all.size
    )
}

/** «1 позиция», «2 позиции», «5 позиций». */
internal fun plural(n: Int, one: String, few: String, many: String): String {
    val d = n % 100
    return when {
        d in 11..14 -> many
        d % 10 == 1 -> one
        d % 10 in 2..4 -> few
        else -> many
    }
}

/** Строки-предупреждения в шапке заказа: что требует внимания клиента. Всё в порядке — пусто. */
fun orderWarnings(s: OrderStage): List<String> {
    val out = ArrayList<String>()
    s.actions.map { name ->
        val n = name.lowercase()
        when {
            "оплат" in n -> "Ждём оплату"
            "предложен" in n -> "Менеджер подобрал вариант — нужен ваш ответ"
            else -> "Нужно ваше участие — нажмите на статус позиции"
        }
    }.distinct().forEach { out.add(it) }
    if (s.delayed > 0) out.add(
        when {
            s.total == 1 -> "Деталь задерживается у поставщика"
            s.delayed == 1 -> "Одна позиция задерживается"
            else -> "${s.delayed} ${plural(s.delayed, "позиция", "позиции", "позиций")} задерживаются"
        }
    )
    // Весь заказ в отказе — об этом скажет шапка, повторять не нужно
    if (s.refused > 0 && s.stage != 0) out.add(
        "${s.refused} ${plural(s.refused, "позиция", "позиции", "позиций")} из ${s.total} " +
            (if (s.refused == 1) "не подтверждена" else "не подтверждены") + " поставщиком"
    )
    return out
}

// ---------- время ----------

/**
 * Время шага истории тем же видом, что [formatAbcpDate]: «13 нояб., 13:42», год — если не текущий.
 * Время московское (магазин в Севастополе), каким бы ни был пояс телефона. now — для тестов.
 */
fun formatUnixTime(seconds: Long, now: Long = System.currentTimeMillis()): String {
    if (seconds <= 0) return ""
    val c = GregorianCalendar(TimeZone.getTimeZone("Europe/Moscow")).apply { timeInMillis = seconds * 1000L }
    val raw = String.format(
        Locale.US, "%04d-%02d-%02d %02d:%02d",
        c.get(Calendar.YEAR), c.get(Calendar.MONTH) + 1, c.get(Calendar.DAY_OF_MONTH),
        c.get(Calendar.HOUR_OF_DAY), c.get(Calendar.MINUTE)
    )
    return formatAbcpDate(raw, now)
}

/** Когда ждать позицию: start — момент заказа, from..to — ожидаемое прибытие (миллисекунды). */
data class ExpectedRange(val start: Long, val from: Long, val to: Long)

/**
 * Дата заказа + срок поставки в часах (ABCP отдаёт его у каждой позиции) → моменты времени.
 * null — нет даты или срока, либо срок 0 (товар был в магазине, ждать нечего).
 */
fun expectedRange(orderDate: String?, deadline: String?, deadlineMax: String?): ExpectedRange? {
    val hours = deadline?.trim()?.toDoubleOrNull()?.toInt() ?: return null
    if (hours <= 0) return null
    val start = runCatching {
        java.text.SimpleDateFormat("yyyy-MM-dd HH:mm:ss", Locale.US).parse(orderDate.orEmpty().trim())
    }.getOrNull()?.time ?: return null
    val max = deadlineMax?.trim()?.toDoubleOrNull()?.toInt() ?: 0
    val from = start + hours * 3_600_000L
    return ExpectedRange(start, from, if (max > hours) start + max * 3_600_000L else from)
}
