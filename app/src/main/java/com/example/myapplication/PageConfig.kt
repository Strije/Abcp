package com.example.myapplication

/**
 * Настройки со страницы магазина avtodrug92.ru/api — строки «ключ=значение», которые владелец правит сам в редакторе
 * сайта, без сервера и без бота. Страница важнее сервера: что на ней написано, то и действует; строку убрали —
 * снова действует значение с сервера (а без него — зашитое в приложение).
 *
 * Ключи те же, что у команды /config в боте:
 *   min_version=114                  update_text=Вышла новая версия
 *   notice=9 мая работаем до 15:00   notice.link=https://avtodrug92.ru/news
 *   hours.text=Пн–Пт 9:00–19:00 · Сб–Вс 9:00–17:00
 *   hours.week.6=10:00-16:00         hours.week.7=выходной        (1 — понедельник)
 *   hours.closed=2027-01-01,2027-01-02
 *   arrivals=11:00,15:00             (во сколько в магазин приезжают заказы; «нет» — без привозов)
 *   stores.1.address=ул. Хрусталёва, 111    stores.1.phone=+79782195625    stores.1.office_id=27993
 *   city=Севастополь   chat_url=https://…   max_url=https://…
 *   flags.brand_autopick=off
 * Строка server=https://… — адрес сервера, её разбирает ServerAddress (и проверяет подпись сервера).
 */
private val PAGE_LINE = Regex("^\\s*([A-Za-z0-9_.]+)\\s*=\\s*(.*)$")
private val PAGE_KEYS = setOf(
    "min_version", "update_text", "notice", "notice.text", "notice.link", "notice_link",
    "hours", "hours.text", "hours.closed", "arrivals", "city", "chat_url", "max_url"
)
private val PAGE_KEY_PATTERNS = listOf(
    Regex("^hours\\.week\\.[1-7]$"),
    Regex("^stores\\.[1-6]\\.(name|address|phone|office_id)$"),
    Regex("^flags\\.[a-z0-9_]{1,40}$")
)

private fun isPageKey(key: String) = key in PAGE_KEYS || PAGE_KEY_PATTERNS.any { it.matches(key) }

/**
 * Текст страницы (HTML или простой) → известные настройки. Незнакомые ключи пропускаем: на странице может быть что угодно,
 * а редактор сайта оборачивает строки в теги и заменяет пробелы на &nbsp;.
 */
internal fun parsePageConfig(page: String): Map<String, String> {
    val text = page
        .replace(Regex("(?is)<(script|style)[^>]*>.*?</\\1>"), "\n")
        .replace(Regex("(?i)<br\\s*/?>|</p>|</div>|</li>|</tr>|</h\\d>"), "\n")
        .replace(Regex("<[^>]+>"), "")
        .replace("&nbsp;", " ").replace("&quot;", "\"").replace("&lt;", "<").replace("&gt;", ">")
        .replace("&ndash;", "–").replace("&mdash;", "—").replace("&laquo;", "«").replace("&raquo;", "»")
        .replace("&middot;", "·").replace("&amp;", "&")
        .replace(' ', ' ')
    val out = LinkedHashMap<String, String>()
    for (line in text.lines()) {
        val m = PAGE_LINE.find(line) ?: continue
        val key = m.groupValues[1].lowercase()
        if (!isPageKey(key)) continue
        out[key] = m.groupValues[2].trim().take(500)
    }
    return out
}

private val NO_WORDS = setOf("нет", "выходной", "закрыто", "-", "—", "off", "none")
private val ON_WORDS = setOf("on", "да", "вкл", "true", "1", "yes")
private val OFF_WORDS = setOf("off", "нет", "выкл", "false", "0", "no")

/** «09:00-19:00» (дефис или тире) → часы дня; «выходной» → выходной; мусор → оставить как было (not). */
private fun parseDay(value: String, not: DayHours?): DayHours? {
    val v = value.trim().lowercase()
    if (v in NO_WORDS) return null
    val parts = v.split('-', '–', '—').map { it.trim() }
    if (parts.size != 2) return not
    val open = parseClock(parts[0]) ?: return not
    val close = parseClock(parts[1]) ?: return not
    return if (close > open) DayHours(open, close) else not
}

/** Телефон в любом написании → «+7XXXXXXXXXX»; не российский мобильный/городской из 11 цифр — null. */
internal fun normalizePagePhone(raw: String): String? {
    val digits = raw.filter { it.isDigit() }
    return when {
        digits.length == 11 && (digits[0] == '7' || digits[0] == '8') -> "+7" + digits.substring(1)
        digits.length == 10 -> "+7$digits"
        else -> null
    }
}

/** «+79782195625» → «+7 (978) 219-56-25» */
internal fun displayPhone(phone: String): String {
    val d = phone.filter { it.isDigit() }
    if (d.length != 11) return phone
    return "+7 (${d.substring(1, 4)}) ${d.substring(4, 7)}-${d.substring(7, 9)}-${d.substring(9, 11)}"
}

/** Настройки с сервера (или зашитые) + строки со страницы поверх. Неверное значение на странице не применяется. */
internal fun applyPageConfig(base: AppConfigData, page: Map<String, String>): AppConfigData {
    if (page.isEmpty()) return base
    fun https(key: String) = page[key]?.trim()?.takeIf { it.startsWith("https://") }

    val week = base.schedule.week.toMutableList()
    for (i in 1..7) page["hours.week.$i"]?.let { v -> if (i - 1 < week.size) week[i - 1] = parseDay(v, week[i - 1]) }

    val closed: Set<String>? = page["hours.closed"]?.let { v ->
        if (v.trim().lowercase() in NO_WORDS || v.isBlank()) emptySet()
        else v.split(',', ';', ' ').map { it.trim() }.filter { Regex("^\\d{4}-\\d{2}-\\d{2}$").matches(it) }.toSet()
            .takeIf { it.isNotEmpty() }
    }
    val arrivals: List<Int>? = page["arrivals"]?.let { v ->
        if (v.trim().lowercase() in NO_WORDS) emptyList()
        else v.split(',', ';', ' ').filter { it.isNotBlank() }.map { parseClock(it) }
            .takeIf { list -> list.isNotEmpty() && list.all { it != null } }?.filterNotNull()?.sorted()
    }

    val stores = base.stores.toMutableList()
    for (i in 1..6) {
        val address = page["stores.$i.address"]?.takeIf { it.isNotBlank() }
        val phone = page["stores.$i.phone"]?.let { normalizePagePhone(it) }
        val name = page["stores.$i.name"]?.takeIf { it.isNotBlank() }
        val office = page["stores.$i.office_id"]?.trim()?.takeIf { it.all { c -> c.isDigit() } && it.length <= 10 }
        if (address == null && phone == null && name == null && office == null) continue
        val old = stores.getOrNull(i - 1)
        // Новый магазин без адреса или телефона не заводим — показывать нечего
        val newAddress = address ?: old?.address ?: continue
        val newPhone = phone ?: old?.phone ?: continue
        val store = Store(
            address = newAddress,
            phone = newPhone,
            phoneDisplay = if (phone != null) displayPhone(phone) else old?.phoneDisplay ?: displayPhone(newPhone),
            abcpOfficeId = office ?: old?.abcpOfficeId.orEmpty(),
            name = name ?: if (address != null) address else old?.name ?: newAddress
        )
        if (i - 1 < stores.size) stores[i - 1] = store else stores.add(store)
    }

    val flags = base.flags.toMutableMap()
    page.forEach { (k, v) ->
        if (k.startsWith("flags.")) when (v.trim().lowercase()) {
            in ON_WORDS -> flags[k.removePrefix("flags.")] = true
            in OFF_WORDS -> flags[k.removePrefix("flags.")] = false
        }
    }

    val noticeLink = page["notice.link"] ?: page["notice_link"]
    return base.copy(
        minVersion = page["min_version"]?.trim()?.toIntOrNull()?.takeIf { it >= 0 } ?: base.minVersion,
        updateText = page["update_text"] ?: base.updateText,
        noticeText = page["notice.text"] ?: page["notice"] ?: base.noticeText,
        // Пустая строка убирает ссылку; не https — не применяем
        noticeLink = when {
            noticeLink == null -> base.noticeLink
            noticeLink.isBlank() -> ""
            noticeLink.startsWith("https://") -> noticeLink.trim()
            else -> base.noticeLink
        },
        hoursText = (page["hours.text"] ?: page["hours"])?.takeIf { it.isNotBlank() } ?: base.hoursText,
        schedule = Schedule(week = week, closed = closed ?: base.schedule.closed, arrivals = arrivals ?: base.schedule.arrivals),
        stores = stores,
        city = page["city"]?.takeIf { it.isNotBlank() } ?: base.city,
        chatUrl = https("chat_url") ?: base.chatUrl,
        maxUrl = https("max_url") ?: base.maxUrl,
        flags = flags
    )
}
