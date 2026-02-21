package com.example.myapplication

import android.content.Intent
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp

@OptIn(ExperimentalMaterial3Api::class)
class CabinetActivity : ComponentActivity() {

    private val api = ApiClient.create()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        val session = SessionManager(this)

        setContent {
            MaterialTheme {
                var loading by remember { mutableStateOf(true) }
                var user by remember { mutableStateOf<UserInfoDto?>(null) }
                var error by remember { mutableStateOf<String?>(null) }

                LaunchedEffect(Unit) {
                    try {
                        val resp = api.userInfo(session.login(), session.passMd5())
                        if (resp.isSuccessful) {
                            user = resp.body()
                        } else {
                            error = prettifyAbcpError(resp.errorBody()?.string())
                        }
                    } catch (_: Exception) {
                        error = "Не удалось подключиться к серверу."
                    } finally {
                        loading = false
                    }
                }

                Scaffold(
                    topBar = {
                        TopAppBar(
                            title = { Text("Личный кабинет") },
                            actions = {
                                TextButton(onClick = {
                                    session.clear()
                                    startActivity(Intent(this@CabinetActivity, MainActivity::class.java))
                                    finish()
                                }) { Text("Выход") }
                            }
                        )
                    }
                ) { padding ->
                    Box(Modifier.fillMaxSize().padding(padding)) {
                        when {
                            loading -> CircularProgressIndicator(Modifier.align(Alignment.Center))
                            error != null -> Text(error!!, Modifier.padding(24.dp))
                            user == null -> Text("Нет данных профиля", Modifier.padding(24.dp))
                            else -> Column(Modifier.fillMaxWidth().padding(24.dp)) {
                                Text(user?.name ?: "—", style = MaterialTheme.typography.headlineSmall)
                                Spacer(Modifier.height(8.dp))
                                Text("Телефон: ${user?.mobile ?: "—"}")
                                Text("Email: ${user?.email ?: "—"}")
                                if (!user?.organization.isNullOrBlank()) {
                                    Text("Организация: ${user?.organization}")
                                }
                                Spacer(Modifier.height(24.dp))

                                Button(
                                    onClick = {
                                        startActivity(Intent(this@CabinetActivity, OrdersActivity::class.java))
                                    },
                                    modifier = Modifier.fillMaxWidth()
                                ) { Text("Мои заказы") }
                                Button(
                                    onClick = {
                                        startActivity(Intent(this@CabinetActivity, VinSearchActivity::class.java))
                                    },
                                    modifier = Modifier.fillMaxWidth()
                                ) {
                                    Text("Поиск по VIN или гос. номер Вашего авто")
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}