package com.example.myapplication.abcp

import com.google.gson.JsonParser
import okhttp3.OkHttpClient
import okhttp3.Request

data class GarageCar(
    val vin: String,
    val title: String = vin
)

class AbcpGarageRepository(
    private val client: OkHttpClient = OkHttpClient()
) {
    fun loadGarage(userLogin: String, userPsw: String): List<GarageCar> {
        val url =
            "https://id25202.public.api.abcp.ru/user/garage" +
                    "?userlogin=$userLogin&userpsw=$userPsw"

        val req = Request.Builder().url(url).get().build()

        client.newCall(req).execute().use { resp ->
            val body = resp.body?.string().orEmpty()
            if (!resp.isSuccessful) error("ABCP HTTP ${resp.code}: $body")

            val root = JsonParser.parseString(body)

            val arr = when {
                root.isJsonArray -> root.asJsonArray
                root.isJsonObject && root.asJsonObject.get("data")?.isJsonArray == true ->
                    root.asJsonObject.getAsJsonArray("data")
                else -> error("Неожиданный формат ABCP: $body")
            }

            return arr.mapNotNull { el ->
                val o = el.asJsonObject

                val vin = listOf("vin", "VIN", "frame", "body", "chassis")
                    .firstNotNullOfOrNull { key ->
                        o.get(key)?.takeIf { it.isJsonPrimitive }?.asString
                    }
                    ?.trim()
                    .orEmpty()

                if (vin.isBlank()) return@mapNotNull null

                val title = listOf("name", "title", "model", "car", "comment")
                    .firstNotNullOfOrNull { key ->
                        o.get(key)?.takeIf { it.isJsonPrimitive }?.asString
                    }
                    ?.trim()
                    ?.takeIf { it.isNotBlank() }
                    ?: vin

                GarageCar(vin = vin, title = title)
            }
        }
    }
}