package com.example.myapplication

import android.content.Intent
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.Image
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.launch

class MainActivity : ComponentActivity() {

    private val api = ApiClient.create()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        val session = SessionManager(this)

        setContent {
            MaterialTheme {
                var checkingAutoLogin by remember { mutableStateOf(session.isLoggedIn()) }
                var autoLoginError by remember { mutableStateOf<String?>(null) }

                // Автологин
                LaunchedEffect(Unit) {
                    if (session.isLoggedIn()) {
                        try {
                            val resp = api.userInfo(session.login(), session.passMd5())
                            if (resp.isSuccessful && resp.body() != null) {
                                startActivity(Intent(this@MainActivity, CabinetActivity::class.java))
                                finish()
                            } else {
                                val raw = resp.errorBody()?.string()
                                session.clear()
                                autoLoginError = prettifyAbcpError(raw)
                                checkingAutoLogin = false
                            }
                        } catch (_: Exception) {
                            session.clear()
                            autoLoginError = "Не удалось подключиться к серверу."
                            checkingAutoLogin = false
                        }
                    } else {
                        checkingAutoLogin = false
                    }
                }

                if (checkingAutoLogin) {
                    Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                        CircularProgressIndicator()
                    }
                } else {
                    LoginScreen(
                        api = api,
                        session = session,
                        initialError = autoLoginError,
                        onSuccess = {
                            startActivity(Intent(this@MainActivity, CabinetActivity::class.java))
                            finish()
                        }
                    )
                }
            }
        }
    }
}

@Composable
fun LoginScreen(
    api: AbcpApi,
    session: SessionManager,
    initialError: String? = null,
    onSuccess: () -> Unit
) {
    var login by remember { mutableStateOf("") }
    var password by remember { mutableStateOf("") }
    var loading by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf(initialError) }

    val scope = rememberCoroutineScope()

    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(24.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center
    ) {

        Image(
            painter = painterResource(id = R.drawable.logo),
            contentDescription = "Логотип",
            modifier = Modifier
                .size(240.dp)
                .padding(bottom = 24.dp)
        )

        OutlinedTextField(
            value = login,
            onValueChange = { login = it },
            label = { Text("Логин") },
            singleLine = true,
            enabled = !loading,
            modifier = Modifier.fillMaxWidth()
        )

        Spacer(modifier = Modifier.height(16.dp))

        OutlinedTextField(
            value = password,
            onValueChange = { password = it },
            label = { Text("Пароль") },
            singleLine = true,
            enabled = !loading,
            visualTransformation = PasswordVisualTransformation(),
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password),
            modifier = Modifier.fillMaxWidth()
        )

        if (!error.isNullOrBlank()) {
            Spacer(modifier = Modifier.height(12.dp))
            Text(error!!, color = MaterialTheme.colorScheme.error)
        }

        Spacer(modifier = Modifier.height(24.dp))

        Button(
            enabled = !loading,
            modifier = Modifier.fillMaxWidth(),
            onClick = {
                val l = login.trim()
                val p = password.trim()

                if (l.isEmpty() || p.isEmpty()) {
                    error = "Введите логин и пароль"
                    return@Button
                }

                loading = true
                error = null

                scope.launch {
                    try {
                        val passMd5 = md5(p)
                        val resp = api.userInfo(l, passMd5)

                        if (resp.isSuccessful && resp.body() != null) {
                            session.save(l, passMd5)
                            onSuccess()
                        } else {
                            val raw = resp.errorBody()?.string()
                            error = prettifyAbcpError(raw)
                    }
                    } catch (_: Exception) {
                        error = "Не удалось подключиться к серверу."
                    } finally {
                        loading = false
                    }
                }
            }
        ) {
            if (loading) {
                CircularProgressIndicator(
                    modifier = Modifier.size(18.dp),
                    strokeWidth = 2.dp
                )
                Spacer(Modifier.width(12.dp))
                Text("Вход...")
            } else {
                Text("Войти")
            }
        }
    }
}

@Preview(showBackground = true)
@Composable
fun LoginScreenPreview() {
    MaterialTheme {
        Column(Modifier.fillMaxSize(), horizontalAlignment = Alignment.CenterHorizontally) {
            Text("Preview")
        }
    }
}