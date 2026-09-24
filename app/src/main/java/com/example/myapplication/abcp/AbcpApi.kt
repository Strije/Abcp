package com.example.myapplication.abcp

import com.example.myapplication.OrdersResponseDto
import com.example.myapplication.UserInfoDto
import com.google.gson.JsonElement
import retrofit2.Response
import retrofit2.http.FieldMap
import retrofit2.http.FormUrlEncoded
import retrofit2.http.GET
import retrofit2.http.POST
import retrofit2.http.Query

interface AbcpApi {
    // ✅ user/info
    @GET("user/info")
    suspend fun userInfo(
        @Query("userlogin") userlogin: String,
        @Query("userpsw") userpsw: String
    ): Response<UserInfoDto>

    // ✅ orders list
    @GET("orders")
    suspend fun orders(
        @Query("userlogin") userlogin: String,
        @Query("userpsw") userpsw: String
    ): Response<OrdersResponseDto>

    // ✅ orders/list details (возвращает список заказов с positions)
    @GET("orders/list")
    suspend fun orderDetails(
        @Query("userlogin") userlogin: String,
        @Query("userpsw") userpsw: String,
        @Query(value = "orders[0]", encoded = true) number: String,
    ): Response<JsonElement>

    /** Все статусы магазина: id, name, color, isFinalStatus */
    @GET("orders/statuses")
    suspend fun orderStatuses(
        @Query("userlogin") userlogin: String,
        @Query("userpsw") userpsw: String
    ): Response<JsonElement>

    /** Заказы вместе с позициями (format=p) — для фоновой проверки статусов */
    @GET("orders")
    suspend fun ordersWithPositions(
        @Query("userlogin") userlogin: String,
        @Query("userpsw") userpsw: String,
        @Query("format") format: String = "p",
        @Query("limit") limit: Int = 50
    ): Response<JsonElement>

    // --- Поиск. Списки ABCP приходят то массивом, то объектом {"0": {...}}, поэтому JsonElement ---

    @GET("search/brands")
    suspend fun searchBrands(
        @Query("userlogin") userlogin: String,
        @Query("userpsw") userpsw: String,
        @Query("number") number: String,
        @Query("useOnlineStocks") useOnlineStocks: Int = 1
    ): Response<JsonElement>

    @GET("search/articles")
    suspend fun searchArticles(
        @Query("userlogin") userlogin: String,
        @Query("userpsw") userpsw: String,
        @Query("number") number: String,
        @Query("brand") brand: String,
        @Query("useOnlineStocks") useOnlineStocks: Int = 1,
        /** 1 — полная выдача, как «Показать все варианты» на сайте */
        @Query("disableFiltering") disableFiltering: Int = 0
    ): Response<JsonElement>

    /** Последние (до 50) поисковые запросы клиента */
    @GET("search/history")
    suspend fun searchHistory(
        @Query("userlogin") userlogin: String,
        @Query("userpsw") userpsw: String
    ): Response<JsonElement>

    /** «С этим товаром покупают» — по статистике заказов магазина */
    @GET("advices")
    suspend fun advices(
        @Query("userlogin") userlogin: String,
        @Query("userpsw") userpsw: String,
        @Query("brand") brand: String,
        @Query("number") number: String,
        @Query("limit") limit: Int = 8
    ): Response<JsonElement>

    @GET("search/tips")
    suspend fun searchTips(
        @Query("userlogin") userlogin: String,
        @Query("userpsw") userpsw: String,
        @Query("number") number: String
    ): Response<JsonElement>

    // --- Корзина ---

    @GET("basket/content")
    suspend fun basketContent(
        @Query("userlogin") userlogin: String,
        @Query("userpsw") userpsw: String
    ): Response<JsonElement>

    /** positions[i][brand|number|itemKey|supplierCode|quantity]; quantity=0 удаляет позицию */
    @FormUrlEncoded
    @POST("basket/add")
    suspend fun basketAdd(@FieldMap fields: Map<String, String>): Response<JsonElement>

    @GET("basket/paymentMethods")
    suspend fun paymentMethods(
        @Query("userlogin") userlogin: String,
        @Query("userpsw") userpsw: String
    ): Response<JsonElement>

    @GET("basket/shipmentMethods")
    suspend fun shipmentMethods(
        @Query("userlogin") userlogin: String,
        @Query("userpsw") userpsw: String
    ): Response<JsonElement>

    @GET("basket/shipmentOffices")
    suspend fun shipmentOffices(
        @Query("userlogin") userlogin: String,
        @Query("userpsw") userpsw: String
    ): Response<JsonElement>

    @GET("basket/shipmentAddresses")
    suspend fun shipmentAddresses(
        @Query("userlogin") userlogin: String,
        @Query("userpsw") userpsw: String
    ): Response<JsonElement>

    @GET("basket/shipmentDates")
    suspend fun shipmentDates(
        @Query("userlogin") userlogin: String,
        @Query("userpsw") userpsw: String,
        @Query("minDeadlineTime") minDeadlineTime: Int,
        @Query("maxDeadlineTime") maxDeadlineTime: Int
    ): Response<JsonElement>

    @FormUrlEncoded
    @POST("basket/order")
    suspend fun basketOrder(@FieldMap fields: Map<String, String>): Response<JsonElement>
}
