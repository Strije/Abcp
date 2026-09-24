package com.example.myapplication

/** Контакты магазинов — как на avtodrug92.ru. Меняются здесь. */
/** abcpOfficeId — офис в ABCP (cp/offices): «Первый офис» = Хрусталёва, «Второй офис» = Окт. Революции */
data class Store(val address: String, val phone: String, val phoneDisplay: String, val abcpOfficeId: String)

object StoreInfo {
    val stores = listOf(
        Store("ул. Хрусталёва, 111", "+79782195625", "+7 (978) 219-56-25", "27993"),
        Store("пр. Октябрьской Революции, 20", "+79785407888", "+7 (978) 540-78-88", "60602")
    )
    const val city = "Севастополь"
    const val hours = "Пн–Пт 9:00–19:00 · Сб–Вс 9:00–17:00"

    /** Онлайн-чат открытой линии Битрикс24 (тот же, что виджет на сайте) — сообщения идут в CRM */
    const val managerChatUrl = "https://bitrix.freno.ru/online/avtodrug"

    /** Запасной канал — MAX (ссылка с сайта) */
    const val maxChatUrl = "https://max.ru/u/f9LHodD0cOLBKIaHZWQOl7LRIia77YiAfQxW7VJqyO8pcPEmbXQwXlarPNY"
}
