package com.example.myapplication

data class AbcpErrorDto(
    val errorCode: Int? = null,
    val errorMessage: String? = null
)

data class UserInfoDto(
    val id: Long? = null,
    val code: String? = null,
    val email: String? = null,
    val name: String? = null,
    val mobile: String? = null,
    val organization: String? = null
)

/**
 * Ответ /orders:
 * count приходит строкой ("1")
 * items приходит объектом-словарём: { "250504247": { ... } }
 */
data class OrdersResponseDto(
    val count: String? = null,
    val items: Map<String, OrderDto>? = null
) {
    fun itemsList(): List<OrderDto> = items?.values?.toList().orEmpty()
}

data class OrderDto(
    val number: String? = null,
    val status: String? = null,
    val statusId: String? = null,
    val statusCode: String? = null,
    val statusColor: String? = null,

    val positionsQuantity: String? = null,

    val deliveryAddressId: String? = null,
    val deliveryAddress: String? = null,
    val deliveryOfficeId: String? = null,
    val deliveryOffice: String? = null,
    val deliveryTypeId: String? = null,
    val deliveryType: String? = null,

    val paymentTypeId: String? = null,
    val paymentType: String? = null,

    val deliveryCost: String? = null,
    val shipmentDate: String? = null,

    val sum: String? = null,
    val date: String? = null,
    val debt: String? = null,

    val comment: String? = null,
    val clientOrderNumber: String? = null,

    // приходит, если format=p
    val positions: List<OrderPositionDto>? = null
)

data class OrderDetailsDto(
    val number: String? = null,
    val status: String? = null,
    val statusId: String? = null,
    val statusCode: String? = null,
    val statusColor: String? = null,

    val positionsQuantity: String? = null,
    val deliveryAddress: String? = null,
    val deliveryOffice: String? = null,
    val paymentType: String? = null,
    val deliveryCost: String? = null,
    val shipmentDate: String? = null,

    val sum: String? = null,
    val date: String? = null,
    val debt: String? = null,

    val comment: String? = null,
    val clientOrderNumber: String? = null,

    val positions: List<OrderPositionDto>? = null
)

data class OrderPositionDto(
    val brand: String? = null,
    val number: String? = null,
    val description: String? = null,

    val quantityOrdered: String? = null,
    val quantity: String? = null,

    val price: String? = null,
    val priceInSiteCurrency: String? = null,

    val deadline: String? = null,
    val deadlineMax: String? = null,

    val status: String? = null,
    val statusId: String? = null,
    val statusCode: String? = null,
    val statusColor: String? = null,
    val statusDate: String? = null,

    val comment: String? = null,
    val commentAnswer: String? = null
)