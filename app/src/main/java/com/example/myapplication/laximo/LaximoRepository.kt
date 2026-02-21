package com.example.myapplication.laximo

class LaximoRepository(private val client: LaximoClient) {

    suspend fun findVehicle(identString: String): String =
        client.post("findVehicle", mapOf("identString" to identString))

    suspend fun findVehicleByVin(vin: String): String =
        client.post("findVehicleByVin", mapOf("vin" to vin))

    suspend fun findVehicleByPlateNumber(plate: String): String =
        client.post("findVehicleByPlateNumber", mapOf("plateNumber" to plate))
    suspend fun listCategories(catalog: String, vehicleId: String, ssd: String, categoryId: String? = null): String {
        val params = mutableMapOf(
            "catalog" to catalog,
            "vehicleId" to vehicleId,
            "ssd" to ssd
        )
        if (categoryId != null) params["categoryId"] = categoryId
        return client.post("listCategories", params)
    }

    suspend fun listUnits(catalog: String, vehicleId: String, ssd: String, categoryId: String): String =
        client.post(
            "listUnits",
            mapOf(
                "catalog" to catalog,
                "vehicleId" to vehicleId,
                "ssd" to ssd,
                "categoryId" to categoryId
            )
        )
}