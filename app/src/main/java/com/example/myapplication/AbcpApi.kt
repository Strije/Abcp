package com.example.myapplication

import retrofit2.Response
import retrofit2.http.GET
import retrofit2.http.Query
import retrofit2.http.QueryMap

interface AbcpApi {

    @GET("user/info")
    suspend fun userInfo(
        @Query("userlogin") userlogin: String,
        @Query("userpsw") userpsw: String
    ): Response<UserInfoDto>

    // Получение списка заказов (по страницам)
    @GET("orders/")
    suspend fun orders(
        @Query("userlogin") userlogin: String,
        @Query("userpsw") userpsw: String,
        @Query("skip") skip: Int = 0,
        @Query("limit") limit: Int = 100
    ): Response<OrdersResponseDto>

    // Получение позиций/статусов по списку номеров заказов: orders[0], orders[1]...
    @GET("orders/list")
    suspend fun ordersList(
        @QueryMap(encoded = true) params: Map<String, String>
    ): Response<Map<String, OrderDetailsDto>>
}