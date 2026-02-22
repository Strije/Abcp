package com.example.myapplication.laximo

import com.example.myapplication.BuildConfig
import com.example.myapplication.laximo.model.*
import com.google.gson.JsonElement
import com.google.gson.JsonParser
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

class LaximoRepository(
    private val client: LaximoClient = LaximoClient(
        username = BuildConfig.LAXIMO_USER,
        password = BuildConfig.LAXIMO_PASS
    )
) {

    suspend fun findVehicle(identString: String): List<LaximoVehicleContext> = withContext(Dispatchers.IO) {
        val raw = client.post("findVehicle", mapOf("identString" to identString))
        val arr = JsonParser.parseString(raw).asJsonArray
        arr.map { el: JsonElement ->
            val o = el.asJsonObject
            LaximoVehicleContext(
                catalog = o["catalog"].asString,
                vehicleId = o["vehicleId"].asString,
                ssd = o["ssd"].asString,
                brand = o.get("brand")?.asString,
                name = o.get("name")?.asString
            )
        }
    }

    suspend fun listCategories(ctx: LaximoVehicleContext): List<LaximoCategory> = withContext(Dispatchers.IO) {
        val raw = client.post(
            "listCategories",
            mapOf("catalog" to ctx.catalog, "vehicleId" to ctx.vehicleId, "ssd" to ctx.ssd)
        )
        val arr = JsonParser.parseString(raw).asJsonArray
        arr.map { el: JsonElement ->
            val o = el.asJsonObject
            LaximoCategory(
                categoryId = o["categoryId"].asString,
                name = o["name"].asString,
                ssd = o["ssd"].asString,
                childrens = o["childrens"].asBoolean
            )
        }
    }

    suspend fun listUnits(ctx: LaximoVehicleContext, category: LaximoCategory): List<LaximoUnit> = withContext(Dispatchers.IO) {
        val raw = client.post(
            "listUnits",
            mapOf(
                "catalog" to ctx.catalog,
                "vehicleId" to ctx.vehicleId,
                "ssd" to category.ssd,
                "categoryId" to category.categoryId
            )
        )
        val arr = JsonParser.parseString(raw).asJsonArray
        arr.map { el: JsonElement ->
            val o = el.asJsonObject
            LaximoUnit(
                unitId = o["unitId"].asString,
                name = o["name"].asString,
                code = o.get("code")?.asString,
                ssd = o["ssd"].asString,
                imageUrl = o.get("imageUrl")?.asString,
                largeImageUrl = o.get("largeImageUrl")?.asString
            )
        }
    }

    suspend fun listDetailByUnit(ctx: LaximoVehicleContext, unit: LaximoUnit): List<LaximoDetail> = withContext(Dispatchers.IO) {
        val raw = client.post(
            "listDetailByUnit",
            mapOf("catalog" to ctx.catalog, "ssd" to unit.ssd, "unitId" to unit.unitId)
        )
        val arr = JsonParser.parseString(raw).asJsonArray
        arr.map { el: JsonElement ->
            val o = el.asJsonObject

            val attrs: List<LaximoAttribute> =
                o.getAsJsonArray("attributes")?.map { a: JsonElement ->
                    val ao = a.asJsonObject
                    LaximoAttribute(
                        key = ao["key"].asString,
                        name = ao.get("name")?.asString,
                        value = ao.get("value")?.asString
                    )
                }.orEmpty()

            LaximoDetail(
                name = o.get("name")?.asString,
                codeOnImage = o.get("codeOnImage")?.asString,
                oem = o.get("oem")?.asString,
                ssd = o.get("ssd")?.asString,
                filter = o.get("filter")?.asString,
                attributes = attrs
            )
        }
    }

    suspend fun listImageMapByUnit(ctx: LaximoVehicleContext, unit: LaximoUnit): List<LaximoImageMapItem> = withContext(Dispatchers.IO) {
        val raw = client.post(
            "listImageMapByUnit",
            mapOf("catalog" to ctx.catalog, "ssd" to unit.ssd, "unitId" to unit.unitId)
        )
        val arr = JsonParser.parseString(raw).asJsonArray
        arr.map { el: JsonElement ->
            val o = el.asJsonObject
            LaximoImageMapItem(
                x1 = o["x1"].asString.toInt(),
                y1 = o["y1"].asString.toInt(),
                x2 = o["x2"].asString.toInt(),
                y2 = o["y2"].asString.toInt(),
                type = o.get("type")?.asString,
                code = o.get("code")?.asString
            )
        }
    }

    suspend fun getFilterByDetail(
        catalog: String,
        unitId: String,
        ssd: String,
        filter: String,
        locale: String = "ru_RU",
        detailId: String? = null
    ): LaximoFilterDef = withContext(Dispatchers.IO) {
        val q = linkedMapOf(
            "Locale" to locale,
            "Catalog" to catalog,
            "UnitId" to unitId,
            "Filter" to filter,
            "ssd" to ssd
        )
        if (!detailId.isNullOrBlank()) q["DetailId"] = detailId

        val raw = client.post("getFilterByDetail", q)
        val o = JsonParser.parseString(raw).asJsonObject

        val values: List<LaximoFilterValue> =
            o.getAsJsonArray("values")?.map { el: JsonElement ->
                val v = el.asJsonObject
                LaximoFilterValue(
                    name = v["name"].asString,
                    note = v.get("note")?.asString,
                    ssdModification = v.get("ssdmodification")?.asString
                )
            }.orEmpty()

        LaximoFilterDef(
            name = o["name"].asString,
            type = o["type"].asString,
            values = values,
            regexp = o.get("regexp")?.asString,
            ssdModification = o.get("ssdmodification")?.asString
        )
    }
}