package com.example.myapplication

import android.content.Intent
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.example.myapplication.abcp.OrdersActivity
import com.google.gson.Gson

@OptIn(ExperimentalMaterial3Api::class)
class CabinetActivity : ComponentActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        val session = SessionManager(this)

        if (!session.isLoggedIn()) {
            startActivity(Intent(this, MainActivity::class.java))
            finish()
            return
        }

        val json = intent.getStringExtra(EXTRA_USER_JSON)
        val userInfo = Gson().fromJson(json, UserInfoDto::class.java)

        setContent {
            MaterialTheme {
                Scaffold(
                    topBar = { TopAppBar(title = { Text("Кабинет") }) }
                ) { padding ->
                    Column(
                        modifier = Modifier
                            .padding(padding)
                            .fillMaxSize()
                            .padding(16.dp),
                        verticalArrangement = Arrangement.spacedBy(12.dp)
                    ) {

                        Text(
                            "Пользователь: ${userInfo?.name ?: "-"}",
                            style = MaterialTheme.typography.titleMedium
                        )

                        Text("Email: ${userInfo?.email ?: "-"}")
                        Text("Телефон: ${userInfo?.mobile ?: "-"}")
                        Text("Организация: ${userInfo?.organization ?: "-"}")

                        Spacer(Modifier.height(8.dp))

                        Button(
                            onClick = {
                                startActivity(
                                    Intent(this@CabinetActivity, OrdersActivity::class.java)
                                )
                            },
                            modifier = Modifier.fillMaxWidth()
                        ) {
                            Text("Мои заказы")
                        }
                        Button(
                            onClick = {
                                startActivity(Intent(this@CabinetActivity, VinSearchActivity::class.java))
                            },
                            modifier = Modifier.fillMaxWidth()
                        ) {
                            Text("Поиск по VIN или гос. номер Вашего авто")
                        }
                        Button(
                            onClick = {
                                startActivity(Intent(this@CabinetActivity, GarageActivity::class.java))
                            },
                            modifier = Modifier.fillMaxWidth()
                        ) {
                            Text("Мой гараж")
                        }
                        OutlinedButton(
                            onClick = {
                                session.clear()
                                startActivity(
                                    Intent(this@CabinetActivity, MainActivity::class.java)
                                )
                                finish()
                            },
                            modifier = Modifier.fillMaxWidth()
                        ) {
                            Text("Выйти")
                        }
                    }
                }
            }
        }
    }

    companion object {
        const val EXTRA_USER_JSON = "extra_user_json"
    }
}