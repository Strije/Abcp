package com.example.myapplication.abcp

import com.google.gson.JsonParser
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.OkHttpClient
import okhttp3.Request

data class GarageCar(
    val vin: String,
    val title: String = vin,
    /** id машины в гараже ABCP — для удаления */
    val id: String = ""
)

/** Избранная машина — только на телефоне (в ABCP такого признака нет); её показывает «Моя машина» на главной. */
object GarageFavorite {
    private fun prefs(ctx: android.content.Context) = ctx.getSharedPreferences("garage_fav", android.content.Context.MODE_PRIVATE)
    fun get(ctx: android.content.Context): String? = prefs(ctx).getString("vin", null)
    fun set(ctx: android.content.Context, vin: String?) { prefs(ctx).edit().putString("vin", vin).apply() }
    /** Избранная первой, остальные как были */
    fun sorted(ctx: android.content.Context, cars: List<GarageCar>): List<GarageCar> {
        val fav = get(ctx)
        return cars.sortedByDescending { it.vin == fav }
    }
}

/** VIN / кузов / госномер для сравнения: без пробелов и дефисов, заглавными */
fun garageKey(s: String) = s.uppercase().filter { it.isLetterOrDigit() }

class AbcpGarageRepository(
    private val client: OkHttpClient = OkHttpClient()
) {
    fun loadGarage(userLogin: String, userPsw: String): List<GarageCar> {
        val url = "https://id25202.public.api.abcp.ru/user/garage".toHttpUrl().newBuilder()
            .addQueryParameter("userlogin", userLogin)
            .addQueryParameter("userpsw", userPsw)
            .build()

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

                val vin = listOf("vin", "VIN", "frame", "body", "chassis", "vehicleRegPlate")
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

                GarageCar(vin = vin, title = title, id = o.get("id")?.takeIf { it.isJsonPrimitive }?.asString.orEmpty())
            }
        }
    }
}