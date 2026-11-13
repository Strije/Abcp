package com.example.myapplication

/**
 * Магазин. abcpOfficeId — офис в ABCP (cp/offices): «Первый офис» = Хрусталёва, «Второй офис» = Окт. Революции.
 * name — короткое название (по умолчанию — адрес).
 */
data class Store(
    val address: String,
    val phone: String,
    val phoneDisplay: String,
    val abcpOfficeId: String,
    val name: String = address
)

/**
 * Контакты магазинов — как на avtodrug92.ru. Значения приходят с сервера ([AppConfig]) и меняются без выпуска
 * версии; то, что зашито в приложение, — в AppConfig.kt. Геттеры, а не const: настройки обновляются на ходу.
 */
object StoreInfo {
    val stores: List<Store> get() = AppConfig.stores
    val city: String get() = AppConfig.city
    val hours: String get() = AppConfig.hoursText

    /** Онлайн-чат открытой линии Битрикс24 (тот же, что виджет на сайте) — сообщения идут в CRM */
    val managerChatUrl: String get() = AppConfig.chatUrl

    /** Запасной канал — MAX (ссылка с сайта) */
    val maxChatUrl: String get() = AppConfig.maxUrl
}
