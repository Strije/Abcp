package com.example.myapplication.abcp

import com.example.myapplication.OrderDetailsDto
import com.example.myapplication.OrdersResponseDto
import com.example.myapplication.UserInfoDto
import com.google.gson.JsonElement
import retrofit2.Response
import retrofit2.http.GET
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
}
