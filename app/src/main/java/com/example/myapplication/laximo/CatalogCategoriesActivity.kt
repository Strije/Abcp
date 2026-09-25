package com.example.myapplication.laximo
import com.example.myapplication.laximo.resolveLaximoImageUrl

import android.content.Intent
import android.os.Bundle
import android.widget.ArrayAdapter
import android.widget.ListView
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.lifecycle.lifecycleScope
import com.example.myapplication.BuildConfig
import com.example.myapplication.laximo.model.LaximoCategory
import com.example.myapplication.laximo.model.LaximoVehicleContext
import kotlinx.coroutines.launch

class CatalogCategoriesActivity : ComponentActivity() {

    private val repo: LaximoRepository by lazy {
        LaximoRepository(this)
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        val catalog: String = intent.getStringExtra("catalog") ?: run {
            Toast.makeText(this, "Нет catalog", Toast.LENGTH_LONG).show()
            finish(); return
        }
        val vehicleId: String = intent.getStringExtra("vehicleId") ?: run {
            Toast.makeText(this, "Нет vehicleId", Toast.LENGTH_LONG).show()
            finish(); return
        }
        val ssd: String = intent.getStringExtra("ssd") ?: run {
            Toast.makeText(this, "Нет ssd", Toast.LENGTH_LONG).show()
            finish(); return
        }

        val ctx = LaximoVehicleContext(catalog = catalog, vehicleId = vehicleId, ssd = ssd)

        val listView = ListView(this)
        setContentView(listView)

        lifecycleScope.launch {
            try {
                val categories: List<LaximoCategory> = repo.listCategories(ctx)

                val titles = categories.map { c -> c.name }
                listView.adapter = ArrayAdapter(
                    this@CatalogCategoriesActivity,
                    android.R.layout.simple_list_item_1,
                    titles
                )

                listView.setOnItemClickListener { _, _, position, _ ->
                    val cat = categories[position]

                    // ✅ Переход на следующий экран
                    val i = Intent(this@CatalogCategoriesActivity, CatalogUnitsActivity::class.java)
                    i.putExtra("catalog", catalog)
                    i.putExtra("vehicleId", vehicleId)

                    // ⚠️ ВАЖНО: на Units передаём SSD КАТЕГОРИИ (не исходный)
                    i.putExtra("ssd", cat.ssd)

                    i.putExtra("categoryId", cat.categoryId)
                    i.putExtra("categoryName", cat.name)

                    startActivity(i)
                }

            } catch (e: Exception) {
                com.example.myapplication.Analytics.error("Каталог → категории", e)
                Toast.makeText(
                    this@CatalogCategoriesActivity,
                    laximoUserMessage(e),
                    Toast.LENGTH_LONG
                ).show()
            }
        }
    }
}