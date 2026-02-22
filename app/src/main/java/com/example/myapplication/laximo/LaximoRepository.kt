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

        // ✅ По OpenAPI (Laximo.CAT REST API v1):
        // POST /restApi/v1/findVehicle?identString=
        val raw = client.post("findVehicle", mapOf("identString" to query))
        val result = parseVehicleContexts(raw)
        if (result.isEmpty()) {
            throw IllegalStateException("Авто не найдено по идентификатору: $query")
        }
        result
    }

    suspend fun listCategories(ctx: LaximoVehicleContext): List<LaximoCategory> = withContext(Dispatchers.IO) {
        // ✅ По OpenAPI (Laximo.CAT REST API v1):
        // POST /restApi/v1/listCategories?catalog=&ssd=&vehicleId=&categoryId=
        // Возвращает массив CategoryDto.
        val raw = client.post(
            "listCategories",
            mapOf(
                "catalog" to ctx.catalog,
                "ssd" to ctx.ssd,
                "vehicleId" to ctx.vehicleId,
                // -1 = корневой уровень категорий
                "categoryId" to "-1"
            )
        )

        val arr = JsonParser.parseString(raw).asJsonArray
        arr.map { el: JsonElement ->
            val o = el.asJsonObject
            LaximoCategory(
                categoryId = o.stringOrNullAny("categoryId", "id") ?: "",
                name = o.stringOrNullAny("name") ?: "Без названия",
                // Обычно ssd в категориях тот же, что и в контексте, но берём из ответа если есть
                ssd = o.stringOrNullAny("ssd") ?: ctx.ssd,
                childrens = o.booleanOrFalse("childrens")
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


    suspend fun listQuickGroup(ctx: LaximoVehicleContext): LaximoQuickGroupNode = withContext(Dispatchers.IO) {
        // POST /restApi/v1/listQuickGroup?catalog=&ssd=&vehicleId=
        val raw = client.post(
            "listQuickGroup",
            mapOf(
                "catalog" to ctx.catalog,
                "ssd" to ctx.ssd,
                "vehicleId" to (ctx.vehicleId.ifBlank { "0" })
            )
        )

        val root = JsonParser.parseString(raw).asJsonObject
        parseQuickGroupNode(root)
    }

    suspend fun listQuickDetail(
        ctx: LaximoVehicleContext,
        quickGroupId: Long,
        all: Boolean = false
    ): List<LaximoPartsCategory> = withContext(Dispatchers.IO) {
        // POST /restApi/v1/listQuickDetail?catalog=&ssd=&vehicleId=&quickGroupId=&all=
        val raw = client.post(
            "listQuickDetail",
            mapOf(
                "catalog" to ctx.catalog,
                "ssd" to ctx.ssd,
                "vehicleId" to (ctx.vehicleId.ifBlank { "0" }),
                "quickGroupId" to quickGroupId.toString(),
                "all" to all.toString()
            )
        )

        val arr = JsonParser.parseString(raw).asJsonArray
        arr.map { el -> parsePartsCategory(el.asJsonObject, ctx.ssd) }
    }

    private fun parseQuickGroupNode(o: JsonObject): LaximoQuickGroupNode {
        val children = o.getAsJsonArray("children")?.map { ch ->
            parseQuickGroupNode(ch.asJsonObject)
        }.orEmpty()

        return LaximoQuickGroupNode(
            name = o.stringOrNullAny("name"),
            quickGroupId = o.get("quickGroupId")?.let { runCatching { it.asLong }.getOrNull() },
            synonyms = o.stringOrNullAny("synonyms"),
            contains = o.stringOrNullAny("contains"),
            link = o.booleanOrFalse("link"),
            children = children
        )
    }

    private fun parsePartsCategory(o: JsonObject, fallbackSsd: String): LaximoPartsCategory {
        val units = o.getAsJsonArray("units")?.map { uEl ->
            val u = uEl.asJsonObject
            val details = u.getAsJsonArray("details")?.map { dEl ->
                val d = dEl.asJsonObject
                LaximoQDetail(
                    name = d.stringOrNullAny("name"),
                    codeOnImage = d.stringOrNullAny("codeOnImage"),
                    oem = d.stringOrNullAny("oem"),
                    match = d.get("match")?.let { runCatching { it.asBoolean }.getOrDefault(false) } ?: false
                )
            }.orEmpty()

            LaximoPartsUnit(
                unitId = u.stringOrNullAny("unitId") ?: "",
                name = u.stringOrNullAny("name") ?: "",
                code = u.stringOrNullAny("code"),
                ssd = u.stringOrNullAny("ssd") ?: fallbackSsd,
                imageUrl = u.stringOrNullAny("imageUrl"),
                largeImageUrl = u.stringOrNullAny("largeImageUrl"),
                filter = u.stringOrNullAny("filter"),
                details = details
            )
        }.orEmpty()

        return LaximoPartsCategory(
            categoryId = o.stringOrNullAny("categoryId") ?: "",
            code = o.stringOrNullAny("code"),
            name = o.stringOrNullAny("name") ?: "",
            parentCategoryId = o.stringOrNullAny("parentCategoryId"),
            ssd = o.stringOrNullAny("ssd") ?: fallbackSsd,
            childrens = o.booleanOrFalse("childrens"),
            units = units
        )
    }
    suspend fun getFilterByDetail(
        catalog: String,
        unitId: String,
        ssd: String,
        filter: String,
        locale: String = "ru_RU",
        detailId: String? = null
    ): LaximoFilterDef = withContext(Dispatchers.IO) {
        // ✅ По OpenAPI (Laximo.CAT REST API v1):
        // POST /restApi/v1/getFilterByDetail?catalog=&unitId=&detailId=&filter=&ssd=
        // Ответ: массив FilterDto.
        val q = linkedMapOf(
            "catalog" to catalog,
            "unitId" to unitId,
            "filter" to filter,
            "ssd" to ssd
        )
        if (!detailId.isNullOrBlank()) q["detailId"] = detailId

        val raw = client.post("getFilterByDetail", q)
        val root = JsonParser.parseString(raw)

        val first = when {
            root.isJsonArray && root.asJsonArray.size() > 0 -> root.asJsonArray[0].asJsonObject
            root.isJsonObject -> root.asJsonObject
            else -> JsonObject()
        }

        val values: List<LaximoFilterValue> =
            first.getAsJsonArray("values")?.map { el: JsonElement ->
                val v = el.asJsonObject
                LaximoFilterValue(
                    name = v.get("name")?.asString ?: "",
                    note = v.get("note")?.asString,
                    // В Swagger поле называется ssdModification
                    ssdModification = v.get("ssdModification")?.asString
                        ?: v.get("ssdmodification")?.asString
                )
            }.orEmpty()

        LaximoFilterDef(
            name = first.get("name")?.asString ?: filter,
            type = first.get("type")?.asString ?: "list",
            values = values,
            regexp = first.get("regexp")?.asString,
            ssdModification = first.get("ssdModification")?.asString
                ?: first.get("ssdmodification")?.asString
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
                o.get("catalog") != null && o.get("vehicleId") != null && o.get("ssd") != null -> {
                    com.google.gson.JsonArray().apply { add(o) }
                }
                o.get("Catalog") != null && o.get("VehicleId") != null && o.get("SSD") != null -> {
                    com.google.gson.JsonArray().apply { add(o) }
                }
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
