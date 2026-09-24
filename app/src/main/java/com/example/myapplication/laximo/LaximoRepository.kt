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
        password = BuildConfig.LAXIMO_PASS,
        language = "ru_RU"
    )
) {

    suspend fun findVehicle(identString: String): List<LaximoVehicleContext> = withContext(Dispatchers.IO) {
        val q = identString.trim()
        require(q.isNotBlank()) { "VIN/FRAME пустой" }
        val raw = client.post("findVehicle", mapOf("identString" to q))
        parseVehicleContexts(raw)
    }

    suspend fun findVehicleByPlate(plate: String): List<LaximoVehicleContext> = withContext(Dispatchers.IO) {
        val raw = client.post(
            "findVehicleByPlateNumber",
            mapOf("countryCode" to "ru", "plateNumber" to plate)
        )
        parseVehicleContexts(raw)
    }

    /** Госномер РФ ищем по номеру, всё остальное — как VIN/Frame. */
    suspend fun findVehicleAny(query: String): List<LaximoVehicleContext> {
        val plate = normalizeRuPlate(query)
        return if (plate != null) findVehicleByPlate(plate) else findVehicle(query)
    }

    suspend fun listCategories(ctx: LaximoVehicleContext): List<LaximoCategory> = withContext(Dispatchers.IO) {
        val raw = client.post(
            "listCategories",
            mapOf(
                "catalog" to ctx.catalog,
                "ssd" to ctx.ssd,
                "vehicleId" to ctx.vehicleId,
                "categoryId" to "-1"
            )
        )
        val arr = JsonParser.parseString(raw).asJsonArray
        arr.map { el ->
            val o = el.asJsonObject
            LaximoCategory(
                categoryId = o.stringOrNullAny("categoryId", "id") ?: "",
                name = o.stringOrNullAny("name") ?: "Без названия",
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
                "ssd" to category.ssd,
                "vehicleId" to ctx.vehicleId,
                "categoryId" to category.categoryId
            )
        )
        val arr = JsonParser.parseString(raw).asJsonArray
        arr.map { el ->
            val o = el.asJsonObject
            LaximoUnit(
                unitId = o.stringOrNullAny("unitId") ?: "",
                name = o.stringOrNullAny("name") ?: "Без названия",
                code = o.stringOrNullAny("code"),
                ssd = o.stringOrNullAny("ssd") ?: category.ssd,
                imageUrl = o.stringOrNullAny("imageUrl"),
                largeImageUrl = o.stringOrNullAny("largeImageUrl")
            )
        }
    }

    suspend fun listDetailByUnit(ctx: LaximoVehicleContext, unit: LaximoUnit): List<LaximoDetail> = withContext(Dispatchers.IO) {
        val raw = client.post(
            "listDetailByUnit",
            mapOf("catalog" to ctx.catalog, "ssd" to unit.ssd, "unitId" to unit.unitId)
        )
        val arr = JsonParser.parseString(raw).asJsonArray
        arr.map { el ->
            val o = el.asJsonObject
            val attrs = o.getAsJsonArray("attributes")?.map { a ->
                val ao = a.asJsonObject
                LaximoAttribute(
                    key = ao.stringOrNullAny("key") ?: "",
                    name = ao.stringOrNullAny("name"),
                    value = ao.stringOrNullAny("value")
                )
            }.orEmpty()

            LaximoDetail(
                name = o.stringOrNullAny("name"),
                codeOnImage = o.stringOrNullAny("codeOnImage"),
                oem = o.stringOrNullAny("oem"),
                ssd = o.stringOrNullAny("ssd") ?: unit.ssd,
                filter = o.stringOrNullAny("filter"),
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
        arr.mapNotNull { el ->
            val o = el.asJsonObject
            val x1 = o.stringOrNullAny("x1")?.toIntOrNull() ?: return@mapNotNull null
            val y1 = o.stringOrNullAny("y1")?.toIntOrNull() ?: return@mapNotNull null
            val x2 = o.stringOrNullAny("x2")?.toIntOrNull() ?: return@mapNotNull null
            val y2 = o.stringOrNullAny("y2")?.toIntOrNull() ?: return@mapNotNull null
            LaximoImageMapItem(
                x1 = x1, y1 = y1, x2 = x2, y2 = y2,
                type = o.stringOrNullAny("type"),
                code = o.stringOrNullAny("code")
            )
        }
    }

    // Quick каталог: дерево групп
    suspend fun listQuickGroup(ctx: LaximoVehicleContext): LaximoQuickGroupNode? = withContext(Dispatchers.IO) {
        val raw = client.post(
            "listQuickGroup",
            mapOf("catalog" to ctx.catalog, "ssd" to ctx.ssd, "vehicleId" to ctx.vehicleId)
        )
        val root = JsonParser.parseString(raw).asJsonObject
        parseQuickNode(root)
    }

    // Поиск деталей в быстром каталоге по тексту
    suspend fun searchQuickDetail(ctx: LaximoVehicleContext, query: String): List<LaximoPartsCategory> = withContext(Dispatchers.IO) {
        val raw = client.post(
            "listQuickDetail",
            mapOf(
                "catalog" to ctx.catalog,
                "ssd" to ctx.ssd,
                "vehicleId" to ctx.vehicleId,
                "query" to query,
                "all" to "true"
            )
        )
        parseQuickDetailResponse(raw, ctx.ssd)
    }

    // Quick каталог: детали/узлы группы
    suspend fun listQuickDetail(ctx: LaximoVehicleContext, quickGroupId: Long, all: Boolean = false): List<LaximoPartsCategory> =
        withContext(Dispatchers.IO) {
            val raw = client.post(
                "listQuickDetail",
                mapOf(
                    "catalog" to ctx.catalog,
                    "ssd" to ctx.ssd,
                    "vehicleId" to ctx.vehicleId,
                    "quickGroupId" to quickGroupId.toString(),
                    "all" to all.toString()
                )
            )
            parseQuickDetailResponse(raw, ctx.ssd)
        }

    private fun parseQuickDetailResponse(raw: String, defaultSsd: String): List<LaximoPartsCategory> {
        val je = JsonParser.parseString(raw)
        val categories = mutableListOf<LaximoPartsCategory>()

        val rootObj: JsonObject? = when {
            je.isJsonObject -> je.asJsonObject
            else -> null
        }

        val catArr = rootObj?.getAsJsonArray("categories")
            ?: rootObj?.getAsJsonArray("data")
            ?: (if (je.isJsonArray) je.asJsonArray else null)

        if (catArr != null) {
            catArr.forEach { el ->
                val o = el.asJsonObject
                val name = o.stringOrNullAny("name") ?: "Категория"
                // units
                val units = o.getAsJsonArray("units")?.mapNotNull { uel ->
                    val uo = uel.asJsonObject
                    val unitId = uo.stringOrNullAny("unitId") ?: return@mapNotNull null
                    LaximoUnit(
                        unitId = unitId,
                        name = uo.stringOrNullAny("name") ?: "Узел",
                        code = uo.stringOrNullAny("code"),
                        ssd = uo.stringOrNullAny("ssd") ?: defaultSsd,
                        imageUrl = uo.stringOrNullAny("imageUrl"),
                        largeImageUrl = uo.stringOrNullAny("largeImageUrl")
                    )
                }.orEmpty()

                // details (если есть)
                val details = o.getAsJsonArray("details")?.map { del ->
                    val doo = del.asJsonObject
                    LaximoDetail(
                        name = doo.stringOrNullAny("name"),
                        codeOnImage = doo.stringOrNullAny("codeOnImage"),
                        oem = doo.stringOrNullAny("oem"),
                        ssd = doo.stringOrNullAny("ssd") ?: defaultSsd,
                        filter = doo.stringOrNullAny("filter"),
                        attributes = emptyList()
                    )
                }.orEmpty()

                categories += LaximoPartsCategory(name = name, units = units, details = details)
            }
        }
        return categories
    }

    private fun parseQuickNode(o: JsonObject): LaximoQuickGroupNode? {
        val name = o.stringOrNullAny("name") ?: o.stringOrNullAny("quickGroupName") ?: return null
        val qid = o.stringOrNullAny("quickGroupId")?.toLongOrNull()
        val syn = o.stringOrNullAny("synonyms")
        val children = o.getAsJsonArray("children")?.mapNotNull { el ->
            parseQuickNode(el.asJsonObject)
        }.orEmpty()
        return LaximoQuickGroupNode(name = name, quickGroupId = qid, children = children, synonyms = syn)
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
                o.get("vehicles")?.isJsonArray == true -> o.getAsJsonArray("vehicles")
                o.get("data")?.isJsonArray == true -> o.getAsJsonArray("data")
                else -> com.google.gson.JsonArray().apply { add(o) }
            }
        }
        else -> return emptyList()
    }

    return array.mapNotNull { el: JsonElement ->
        val o = el.asJsonObject
        val catalog = o.stringOrNullAny("catalog", "Catalog") ?: return@mapNotNull null
        val vehicleId = o.stringOrNullAny("vehicleId", "vehicleid", "VehicleId") ?: "0"
        val ssd = o.stringOrNullAny("ssd", "SSD") ?: return@mapNotNull null

        val attrs = o.getAsJsonArray("attributes")?.mapNotNull { a ->
            val ao = a.asJsonObject
            LaximoAttribute(
                key = ao.stringOrNullAny("key") ?: return@mapNotNull null,
                name = ao.stringOrNullAny("name"),
                value = ao.stringOrNullAny("value")
            )
        }.orEmpty()

        LaximoVehicleContext(
            catalog = catalog,
            vehicleId = vehicleId,
            ssd = ssd,
            brand = o.stringOrNullAny("brand", "Brand", "manufacturer"),
            name = o.stringOrNullAny("name", "Name", "model", "vehicle"),
            attributes = attrs
        )
    }
}

private fun JsonObject.stringOrNullAny(vararg names: String): String? {
    for (name in names) {
        val value = get(name) ?: continue
        if (value.isJsonNull) continue
        val s = runCatching { value.asString }.getOrNull()
        if (!s.isNullOrBlank()) return s
    }
    return null
}

private fun JsonObject.booleanOrFalse(name: String): Boolean {
    val value = get(name) ?: return false
    if (value.isJsonNull) return false
    return runCatching { value.asBoolean }.getOrDefault(false)
}
