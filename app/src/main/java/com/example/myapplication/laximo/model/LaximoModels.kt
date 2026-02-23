package com.example.myapplication.laximo.model

data class LaximoVehicleContext(
    val catalog: String,
    val vehicleId: String,
    val ssd: String,
    val brand: String? = null,
    val name: String? = null
)

data class LaximoCategory(
    val categoryId: String,
    val name: String,
    val ssd: String,
    val childrens: Boolean
)

data class LaximoUnit(
    val unitId: String,
    val name: String,
    val code: String? = null,
    val ssd: String,
    val imageUrl: String? = null,
    val largeImageUrl: String? = null
)

data class LaximoDetail(
    val name: String? = null,
    val codeOnImage: String? = null,
    val oem: String? = null,
    val ssd: String? = null,
    val filter: String? = null,
    val attributes: List<LaximoAttribute> = emptyList()
)

data class LaximoAttribute(
    val key: String,
    val name: String? = null,
    val value: String? = null
)

data class LaximoImageMapItem(
    val x1: Int,
    val y1: Int,
    val x2: Int,
    val y2: Int,
    val type: String? = null,
    val code: String? = null
)

// ✅ ВОТ ЭТОГО ТЕБЕ НЕ ХВАТАЛО:

data class LaximoFilterValue(
    val name: String,
    val note: String? = null,
    val ssdModification: String? = null
)

data class LaximoFilterDef(
    val name: String,
    val type: String,
    val values: List<LaximoFilterValue>,
    val regexp: String? = null,
    val ssdModification: String? = null
)

data class LaximoQuickGroupNode(
    val name: String,
    val quickGroupId: Long? = null,
    val children: List<LaximoQuickGroupNode> = emptyList(),
    val synonyms: String? = null
)

data class LaximoPartsCategory(
    val name: String,
    val units: List<LaximoUnit> = emptyList(),
    val details: List<LaximoDetail> = emptyList()
)
