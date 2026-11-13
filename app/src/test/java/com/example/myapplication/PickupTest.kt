package com.example.myapplication

import org.junit.Assert.assertEquals
import org.junit.Test
import java.util.GregorianCalendar
import java.util.TimeZone

class PickupTest {
    private val weekday = DayHours(9 * 60, 19 * 60)
    private val weekend = DayHours(9 * 60, 17 * 60)
    private val week = listOf(weekday, weekday, weekday, weekday, weekday, weekend, weekend)
    private val schedule = Schedule(week, emptySet(), listOf(15 * 60))
    /** Магазин с выходными в субботу и воскресенье */
    private val fiveDays = Schedule(listOf(weekday, weekday, weekday, weekday, weekday, null, null), emptySet(), listOf(15 * 60))

    /** Время магазина (Москва), ноябрь 2026: 13-е — пятница, 16-е — понедельник */
    private fun nov(day: Int, hour: Int, minute: Int = 0): Long =
        GregorianCalendar(TimeZone.getTimeZone("Europe/Moscow")).apply { clear(); set(2026, 10, day, hour, minute) }.timeInMillis

    @Test
    fun inStockDuringTheDay() {
        val p = pickupEstimate(nov(13, 12), 0, schedule)
        assertEquals("сегодня до 19:00", p.text)
        assertEquals("2026-11-13", p.date)
        assertEquals(12, p.hour)
        // В субботу закрываемся раньше
        assertEquals("сегодня до 17:00", pickupEstimate(nov(14, 12), 0, schedule).text)
    }

    @Test
    fun inStockBeforeOpening() {
        val p = pickupEstimate(nov(13, 7), 0, schedule)
        assertEquals("сегодня с 9:00", p.text)
        assertEquals(9, p.hour)
    }

    @Test
    fun inStockLateEvening() {
        val p = pickupEstimate(nov(12, 22), 0, schedule)
        assertEquals("завтра с 9:00", p.text)
        assertEquals("2026-11-13", p.date)
        assertEquals(9, p.hour)
        assertEquals(0, p.minute)
        // Меньше получаса до закрытия — уже не успеть
        assertEquals("завтра с 9:00", pickupEstimate(nov(13, 18, 45), 0, schedule).text)
        assertEquals("сегодня до 19:00", pickupEstimate(nov(13, 18, 20), 0, schedule).text)
    }

    @Test
    fun inStockFridayEvening() {
        // Работаем без выходных — завтра
        assertEquals("завтра с 9:00", pickupEstimate(nov(13, 20), 0, schedule).text)
        // Суббота и воскресенье выходные — понедельник
        val p = pickupEstimate(nov(13, 20), 0, fiveDays)
        assertEquals("в понедельник, 16 нояб., с 9:00", p.text)
        assertEquals("2026-11-16", p.date)
    }

    @Test
    fun twoHoursInTheMorningCatchesTodaysArrival() {
        val p = pickupEstimate(nov(13, 10), 2, schedule)
        assertEquals("ориентировочно сегодня после 15:00", p.text)
        assertEquals("2026-11-13", p.date)
        assertEquals(15, p.hour)
        // Привоз уже прошёл, а срок сегодня — к ближайшему часу после прибытия
        assertEquals("ориентировочно сегодня после 16:00", pickupEstimate(nov(13, 14), 2, schedule).text)
        // Срок сегодня, но к закрытию — уже завтра с открытия
        assertEquals("ориентировочно завтра после 9:00", pickupEstimate(nov(13, 17), 2, schedule).text)
    }

    @Test
    fun twentyHours() {
        val p = pickupEstimate(nov(13, 10), 20, schedule)
        assertEquals("ориентировочно завтра после 15:00", p.text)
        assertEquals("2026-11-14", p.date)
    }

    @Test
    fun ninetySixHours() {
        val p = pickupEstimate(nov(13, 10), 96, schedule)
        assertEquals("ориентировочно 17 нояб. (вт) после 15:00", p.text)
        assertEquals("2026-11-17", p.date)
    }

    @Test
    fun dayMatchesAbcpDeadlineEvenIfHoursLandAfterArrival() {
        // Телефон 13.11: в корзине «16 нояб.», а расчёт по минутам давал 17-е — день должен совпадать со сроком ABCP
        val p = pickupEstimate(nov(13, 13, 27), 76, schedule)
        assertEquals("ориентировочно 16 нояб. (пн) после 15:00", p.text)
        assertEquals("2026-11-16", p.date)
    }

    @Test
    fun closedDate() {
        val closed = schedule.copy(closed = setOf("2026-11-14"))
        // Привоз выпал на закрытый день — на следующий рабочий с открытия
        val p = pickupEstimate(nov(13, 10), 20, closed)
        assertEquals("ориентировочно 15 нояб. (вс) после 9:00", p.text)
        assertEquals("2026-11-15", p.date)
        assertEquals("в воскресенье, 15 нояб., с 9:00", pickupEstimate(nov(13, 22), 0, closed).text)
    }

    @Test
    fun noArrivalsMeansFromOpening() {
        val none = schedule.copy(arrivals = emptyList())
        assertEquals("ориентировочно сегодня после 9:00", pickupEstimate(nov(13, 1), 2, none).text)
        // Товар придёт уже после открытия — к ближайшему часу
        assertEquals("ориентировочно сегодня после 12:00", pickupEstimate(nov(13, 10), 2, none).text)
    }

    @Test
    fun weeklyDayOff() {
        val p = pickupEstimate(nov(13, 10), 20, fiveDays)
        assertEquals("ориентировочно 16 нояб. (пн) после 9:00", p.text)
        assertEquals("2026-11-16", p.date)
    }

    @Test
    fun arrivalAfterClosingMovesToNextDay() {
        // Привоз в 18:00, а в субботу работаем до 17:00
        val late = schedule.copy(arrivals = listOf(18 * 60))
        assertEquals("ориентировочно 15 нояб. (вс) после 9:00", pickupEstimate(nov(13, 10), 20, late).text)
    }

    @Test
    fun noWorkingDaysAtAll() {
        val never = Schedule(listOf(null, null, null, null, null, null, null))
        assertEquals("уточните в магазине", pickupEstimate(nov(13, 10), 0, never).text)
        assertEquals("уточните в магазине", pickupEstimate(nov(13, 10), 20, never).text)
    }

    @Test
    fun shipmentDateLabels() {
        val now = nov(13, 12)
        assertEquals("сегодня, пт 13 нояб.", shipmentDateLabel("2026-11-13", now))
        assertEquals("завтра, сб 14 нояб.", shipmentDateLabel("2026-11-14", now))
        assertEquals("пн 16 нояб.", shipmentDateLabel("2026-11-16", now))
        assertEquals("вт 1 дек.", shipmentDateLabel("2026-12-01", now))
        // Поздний вечер по Москве — всё ещё «сегодня»
        assertEquals("сегодня, пт 13 нояб.", shipmentDateLabel("2026-11-13", nov(13, 23, 30)))
        assertEquals(null, shipmentDateLabel("2026-11-13 (пт)", now))
        assertEquals(null, shipmentDateLabel("завтра", now))
    }

    @Test
    fun defaultDateIsFirstNotBeforePickup() {
        val dates = listOf("2026-11-13", "2026-11-14", "2026-11-16")
        assertEquals("2026-11-14", defaultShipmentDate(dates, "2026-11-14"))
        assertEquals("2026-11-16", defaultShipmentDate(dates, "2026-11-15"))
        assertEquals("2026-11-16", defaultShipmentDate(dates, "2026-11-20"))
        assertEquals(null, defaultShipmentDate(emptyList(), "2026-11-14"))
    }
}
