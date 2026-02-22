package com.example.myapplication.laximo

import com.example.myapplication.BuildConfig
import com.example.myapplication.laximo.model.*
import com.google.gson.JsonElement
import com.google.gson.JsonObject
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
        val query = identString.trim()
        require(query.isNotBlank()) { "Идентификатор авто не указан" }

        val attempts = listOf(
            // Swagger/REST v1: поддерживаем универсальный поиск по identString.
            "FindVehicle" to mapOf("identString" to query)
        )

        val errors = mutableListOf<String>()
        for ((path, params) in attempts) {
            runCatching {
                val raw = client.post(path, params)
                parseVehicleContexts(raw)
            }.onSuccess { result ->
                if (result.isNotEmpty()) return@withContext result
            }.onFailure { ex ->
                errors += "$path: ${ex.message ?: ex.javaClass.simpleName}"
            }
        }

        if (errors.isNotEmpty()) {
            throw IllegalStateException("Не удалось расшифровать VIN/FRAME. Попытки: ${errors.joinToString(" | ")}")
        }

        emptyList()
    }

    suspend fun listCategories(ctx: LaximoVehicleContext): List<LaximoCategory> = withContext(Dispatchers.IO) {
        val vehicleInfoRaw = client.post(
            "GetVehicleInfo",
            mapOf(
                "Locale" to "ru_RU",
                "Catalog" to ctx.catalog,
                "VehicleId" to ctx.vehicleId,
                "ssd" to ctx.ssd,
                "Localized" to "true"
            )
        )

        val vehicleInfo = JsonParser.parseString(vehicleInfoRaw).asJsonObject
        val actualSsd = vehicleInfo.stringOrNull("ssd").orEmpty().ifBlank { ctx.ssd }

        val raw = client.post(
            "ListQuickGroup",
            mapOf(
                "Locale" to "ru_RU",
                "Catalog" to ctx.catalog,
                "VehicleId" to ctx.vehicleId,
                "ssd" to actualSsd
            )
        )
        val arr = JsonParser.parseString(raw).asJsonArray
        arr.map { el: JsonElement ->
            val o = el.asJsonObject
            LaximoCategory(
                categoryId = o.stringOrNull("quickGroupId")
                    ?: o.stringOrNull("quickgroupid")
                    ?: o.stringOrNull("categoryId")
                    ?: o.stringOrNull("id")
                    ?: "",
                name = o.stringOrNull("name")
                    ?: o.stringOrNull("quickGroupName")
                    ?: o.stringOrNull("quickgroupname")
                    ?: "Без названия",
                ssd = o.stringOrNull("ssd") ?: actualSsd,
                childrens = o.booleanOrFalse("childrens") || o.booleanOrFalse("hasChildren")
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

private fun parseVehicleContexts(raw: String): List<LaximoVehicleContext> {
    val root = JsonParser.parseString(raw)
    val array = when {
        root.isJsonArray -> root.asJsonArray
        root.isJsonObject -> {
            val o = root.asJsonObject
            when {
                o.get("rows")?.isJsonArray == true -> o.getAsJsonArray("rows")
                o.get("result")?.isJsonArray == true -> o.getAsJsonArray("result")
                o.get("vehicles")?.isJsonArray == true -> o.getAsJsonArray("vehicles")
                o.get("data")?.isJsonArray == true -> o.getAsJsonArray("data")
                else -> return emptyList()
            }
        }
        else -> return emptyList()
    }

    return array.mapNotNull { el: JsonElement ->
        val o = el.asJsonObject
        val catalog = o.stringOrNullAny("catalog", "Catalog") ?: return@mapNotNull null
        val vehicleId = o.stringOrNullAny("vehicleId", "vehicleid", "VehicleId") ?: return@mapNotNull null
        val ssd = o.stringOrNullAny("ssd", "SSD") ?: return@mapNotNull null

        LaximoVehicleContext(
            catalog = catalog,
            vehicleId = vehicleId,
            ssd = ssd,
            brand = o.stringOrNullAny("brand", "Brand", "manufacturer"),
            name = o.stringOrNullAny("name", "Name", "model", "vehicle")
        )
    }
}

private fun JsonObject.stringOrNull(name: String): String? {
    val value = get(name) ?: return null
    if (value.isJsonNull) return null
    return value.asString
}

private fun JsonObject.stringOrNullAny(vararg names: String): String? {
    for (name in names) {
        val value = stringOrNull(name)
        if (!value.isNullOrBlank()) return value
    }
    return null
}

private fun JsonObject.booleanOrFalse(name: String): Boolean {
    val value = get(name) ?: return false
    if (value.isJsonNull) return false
    return runCatching { value.asBoolean }.getOrDefault(false)
}
