package com.example.myapplication.laximo

import com.example.myapplication.laximo.model.LaximoCategory
import com.example.myapplication.laximo.model.LaximoUnit
import com.example.myapplication.laximo.model.LaximoVehicleContext
import com.google.gson.JsonParser

object LaximoParsers {

    fun parseFindVehicle(raw: String): LaximoVehicleContext {
        val arr = JsonParser.parseString(raw).asJsonArray
        val o = arr[0].asJsonObject
        return LaximoVehicleContext(
            catalog = o["catalog"].asString,
            vehicleId = o["vehicleId"].asString,
            ssd = o["ssd"].asString,
            brand = o.get("brand")?.asString,
            name = o.get("name")?.asString
        )
    }

    fun parseCategories(raw: String): List<LaximoCategory> {
        val arr = JsonParser.parseString(raw).asJsonArray
        return arr.map { el ->
            val o = el.asJsonObject
            LaximoCategory(
                categoryId = o["categoryId"].asString,
                name = o["name"].asString,
                ssd = o["ssd"].asString,          // ВАЖНО: берём ssd категории
                childrens = o["childrens"].asBoolean
            )
        }
    }

    fun parseUnits(raw: String): List<LaximoUnit> {
        val arr = JsonParser.parseString(raw).asJsonArray
        return arr.map { el ->
            val o = el.asJsonObject
            LaximoUnit(
                unitId = o["unitId"].asString,
                name = o["name"].asString,
                code = o.get("code")?.asString,
                ssd = o["ssd"].asString,          // ВАЖНО: берём ssd узла
                imageUrl = o.get("imageUrl")?.asString,
                largeImageUrl = o.get("largeImageUrl")?.asString
            )
        }
    }

    fun imageSized(url: String?, size: String = "400"): String? {
        if (url.isNullOrBlank()) return null
        // В Laximo в URL плейсхолдер %size% (доступны 50/100/150/200/250/400/800/source)
        return url.replace("%size%", size)
    }
}