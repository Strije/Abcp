package com.example.myapplication.abcp

import com.example.myapplication.OrderDetailsDto
import com.example.myapplication.OrdersResponseDto
import com.example.myapplication.UserInfoDto
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

    // ✅ orders details (format=p возвращает positions)
    @GET("orders")
    suspend fun orderDetails(
        @Query("userlogin") userlogin: String,
        @Query("userpsw") userpsw: String,
        @Query("number") number: String,
        @Query("format") format: String = "p"
    ): Response<Map<String, OrderDetailsDto>>
}