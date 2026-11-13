package com.example.myapplication

import java.util.Calendar
import java.util.GregorianCalendar
import java.util.Locale
import java.util.TimeZone

/**
 * Когда можно забрать заказ. ABCP отдаёт только срок в часах и даты отгрузки — часа выдачи у него нет,
 * считаем сами по настройкам магазина (часы работы, закрытые дни, время привоза от поставщиков).
 * Всё считается по времени магазина (Москва), а не телефона: клиент может быть в отъезде.
 */

/** Часы работы одного дня — минуты от полуночи. */
data class DayHours(val open: Int, val close: Int)

data class Schedule(
    /** пн..вс; null — выходной */
    val week: List<DayHours?>,
    /** Закрытые дни, «2026-01-01» */
    val closed: Set<String> = emptySet(),
    /** Во сколько в магазин приезжают заказы от поставщиков — минуты от полуночи */
    val arrivals: List<Int> = emptyList()
)

/** month — 1..12; hour/minute — с какого времени можно забирать; text — готовая фраза для клиента. */
data class Pickup(val year: Int, val month: Int, val day: Int, val hour: Int, val minute: Int, val text: String) {
    /** «2026-11-13» — в том же виде, что даты отгрузки ABCP (сравниваются как строки) */
    val date: String get() = isoDate(year, month, day)
}

private val MONTHS = listOf("янв.", "февр.", "мар.", "апр.", "мая", "июн.", "июл.", "авг.", "сент.", "окт.", "нояб.", "дек.")
private val WEEK_SHORT = listOf("пн", "вт", "ср", "чт", "пт", "сб", "вс")
private val WEEK_ON = listOf("в понедельник", "во вторник", "в среду", "в четверг", "в пятницу", "в субботу", "в воскресенье")

private fun shopZone(): TimeZone = TimeZone.getTimeZone("Europe/Moscow")
private fun shopCal(millis: Long): Calendar = GregorianCalendar(shopZone()).apply { timeInMillis = millis }

private fun isoDate(year: Int, month: Int, day: Int) = String.format(Locale.US, "%04d-%02d-%02d", year, month, day)
private fun clock(minutes: Int) = String.format(Locale.US, "%d:%02d", minutes / 60, minutes % 60)

private fun Calendar.year() = get(Calendar.YEAR)
private fun Calendar.month() = get(Calendar.MONTH) + 1
private fun Calendar.day() = get(Calendar.DAY_OF_MONTH)
private fun Calendar.minuteOfDay() = get(Calendar.HOUR_OF_DAY) * 60 + get(Calendar.MINUTE)
/** 0 — понедельник … 6 — воскресенье */
private fun Calendar.weekIndex() = (get(Calendar.DAY_OF_WEEK) + 5) % 7
private fun Calendar.plusDays(n: Int): Calendar = (clone() as Calendar).apply { add(Calendar.DAY_OF_MONTH, n) }

/** Номер календарного дня — чтобы считать «сегодня/завтра» по датам, а не по разнице часов. */
private fun dayNumber(c: Calendar): Long =
    GregorianCalendar(TimeZone.getTimeZone("UTC")).apply { clear(); set(c.year(), c.month() - 1, c.day()) }.timeInMillis / 86_400_000L

private fun Schedule.hoursOn(c: Calendar): DayHours? {
    if (isoDate(c.year(), c.month(), c.day()) in closed) return null
    return week.getOrNull(c.weekIndex())?.takeIf { it.close > it.open }
}

/** Ближайший рабочий день после указанного; null — в настройках нет ни одного рабочего дня. */
private fun Schedule.nextWorkingDay(after: Calendar): Calendar? {
    for (i in 1..60) {
        val d = after.plusDays(i)
        if (hoursOn(d) != null) return d
    }
    return null
}

private fun pickup(day: Calendar, minutes: Int, text: String) =
    Pickup(day.year(), day.month(), day.day(), minutes / 60, minutes % 60, text)

/**
 * deliveryHours — срок поставки ABCP в часах от «сейчас»; 0 и меньше — товар уже в магазине.
 */
fun pickupEstimate(nowMillis: Long, deliveryHours: Int, schedule: Schedule): Pickup {
    val now = shopCal(nowMillis)
    val nowMin = now.minuteOfDay()
    // Настройки без единого рабочего дня (ошибка в них) — не выдумываем время
    val unknown = pickup(now, nowMin, "уточните в магазине")

    if (deliveryHours <= 0) {
        val today = schedule.hoursOn(now)
        // Меньше получаса до закрытия — доехать уже не успеть
        if (today != null && nowMin >= today.open && today.close - nowMin > 30) {
            return pickup(now, nowMin, "сегодня до ${clock(today.close)}")
        }
        val day = (if (today != null && nowMin < today.open) now else schedule.nextWorkingDay(now)) ?: return unknown
        val open = schedule.hoursOn(day)?.open ?: return unknown
        val text = when ((dayNumber(day) - dayNumber(now)).toInt()) {
            0 -> "сегодня с ${clock(open)}"
            1 -> "завтра с ${clock(open)}"
            else -> "${WEEK_ON[day.weekIndex()]}, ${day.day()} ${MONTHS[day.month() - 1]}, с ${clock(open)}"
        }
        return pickup(day, open, text)
    }

    val arrive = shopCal(nowMillis + deliveryHours * 3_600_000L)
    val arriveMin = arrive.minuteOfDay()
    val arrivals = schedule.arrivals.sorted()
    var day: Calendar? = null
    var time = 0
    if (arrivals.isEmpty()) {
        // Времени привоза в настройках нет — считаем, что товар на месте к открытию
        for (i in 0..60) {
            val d = arrive.plusDays(i)
            val h = schedule.hoursOn(d) ?: continue
            if (i > 0 || h.open >= arriveMin) {
                day = d
                time = h.open
                break
            }
        }
    } else {
        // Ближайший привоз не раньше прибытия товара: сегодня или завтра первым рейсом
        val same = arrivals.firstOrNull { it >= arriveMin }
        val d = if (same != null) arrive else arrive.plusDays(1)
        val t = same ?: arrivals.first()
        val h = schedule.hoursOn(d)
        if (h == null || t >= h.close) {
            // Привезли в выходной или к закрытию — забирать со следующего рабочего дня
            day = schedule.nextWorkingDay(d)
            time = day?.let { schedule.hoursOn(it) }?.open ?: 0
        } else {
            day = d
            time = maxOf(t, h.open)
        }
    }
    val at = day ?: return unknown
    val dayText = when ((dayNumber(at) - dayNumber(now)).toInt()) {
        0 -> "сегодня"
        1 -> "завтра"
        else -> "${at.day()} ${MONTHS[at.month() - 1]} (${WEEK_SHORT[at.weekIndex()]})"
    }
    return pickup(at, time, "ориентировочно $dayText после ${clock(time)}")
}

/**
 * Подпись даты отгрузки ABCP («2026-11-13») для клиента: «сегодня, пт 13 нояб.», «завтра, сб 14 нояб.», «пн 16 нояб.».
 * null — дата в непонятном виде (покажем как прислал ABCP).
 */
fun shipmentDateLabel(date: String, nowMillis: Long): String? {
    val m = Regex("^(\\d{4})-(\\d{2})-(\\d{2})$").find(date.trim()) ?: return null
    val y = m.groupValues[1].toInt()
    val mo = m.groupValues[2].toInt()
    val d = m.groupValues[3].toInt()
    if (mo !in 1..12 || d !in 1..31) return null
    val c = GregorianCalendar(shopZone()).apply { clear(); set(y, mo - 1, d, 12, 0) }
    val base = "${WEEK_SHORT[c.weekIndex()]} ${c.day()} ${MONTHS[c.month() - 1]}"
    return when (dayNumber(c) - dayNumber(shopCal(nowMillis))) {
        0L -> "сегодня, $base"
        1L -> "завтра, $base"
        else -> base
    }
}

/** Дата по умолчанию: первая не раньше расчётной выдачи; все раньше — самая поздняя из предложенных. */
fun defaultShipmentDate(dates: List<String>, pickupDate: String): String? =
    dates.firstOrNull { it >= pickupDate } ?: dates.lastOrNull()
