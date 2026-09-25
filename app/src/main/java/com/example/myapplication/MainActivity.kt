package com.example.myapplication
import com.example.myapplication.ui.theme.AvtodrugTheme

import android.content.Intent
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.Image
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import com.example.myapplication.abcp.AbcpApi
import com.example.myapplication.abcp.ApiClient
import com.example.myapplication.abcp.prettifyAbcpError
import com.google.gson.Gson
import kotlinx.coroutines.launch

class MainActivity : ComponentActivity() {

    private val api = ApiClient.create()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        val session = SessionManager(this)

        setContent {
            AvtodrugTheme {
                var checkingAutoLogin by remember { mutableStateOf(true) }
                var autoLoginError by remember { mutableStateOf<String?>(null) }
                var inFlight by remember { mutableStateOf(false) }

                fun goToCabinet(user: UserInfoDto?) {
                    val json = user?.let { Gson().toJson(it) } ?: session.userJson()
                    if (user != null && json != null) session.saveUser(json)
                    startActivity(
                        Intent(this@MainActivity, MainShellActivity::class.java)
                            .putExtra(MainShellActivity.EXTRA_USER_JSON, json)
                    )
                    finish()
                }

                // Автологин. Профиль с прошлого раза есть — открываемся сразу, пароль главный экран проверит сам.
                LaunchedEffect(Unit) {
                    if (session.isLoggedIn() && session.userJson() != null) {
                        goToCabinet(null)
                    } else if (session.isLoggedIn() && !inFlight) {
                        inFlight = true
                        try {
                            val resp = performRequestWithRetry {
                                api.userInfo(
                                    userlogin = session.login(),
                                    userpsw = session.passMd5()
                                )
                            }

                            val user = resp.body()
                            if (resp.isSuccessful && user != null) {
                                goToCabinet(user)
                            } else {
                                val raw = resp.errorBody()?.string()
                                session.clear()
                                autoLoginError = prettifyAbcpError(raw)
                                checkingAutoLogin = false
                            }
                        } catch (_: Exception) {
                            // Нет интернета — это не повод выходить из аккаунта
                            goToCabinet(null)
                        } finally {
                            inFlight = false
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
                        onSuccess = { user ->
                            goToCabinet(user)
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
    onSuccess: (UserInfoDto) -> Unit
) {
    var login by remember { mutableStateOf("") }
    var password by remember { mutableStateOf("") }
    var loading by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf(initialError) }

    val scope = rememberCoroutineScope()
    var showPassword by remember { mutableStateOf(false) }
    val focus = LocalFocusManager.current

    fun submit() {
        val l = login.trim()
        val p = password.trim()
        if (loading) return
        if (l.isEmpty() || p.isEmpty()) {
            error = "Введите логин и пароль"
            return
        }
        focus.clearFocus()
        loading = true
        error = null
        scope.launch {
            try {
                val passMd5 = md5(p)
                val resp = performRequestWithRetry { api.userInfo(l, passMd5) }
                val user = resp.body()
                if (resp.isSuccessful && user != null) {
                    session.save(l, passMd5)
                    onSuccess(user)
                } else {
                    error = prettifyAbcpError(resp.errorBody()?.string())
                }
            } catch (_: Exception) {
                error = "Не удалось подключиться к серверу. Проверьте интернет."
            } finally {
                loading = false
            }
        }
    }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .imePadding()
            .verticalScroll(rememberScrollState())
            .padding(24.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center
    ) {

        Image(
            painter = painterResource(id = R.drawable.logo),
            contentDescription = "Логотип",
            modifier = Modifier
                .size(200.dp)
                .padding(bottom = 16.dp)
        )

        OutlinedTextField(
            value = login,
            onValueChange = { login = it },
            label = { Text("Логин") },
            singleLine = true,
            enabled = !loading,
            keyboardOptions = KeyboardOptions(imeAction = ImeAction.Next),
            modifier = Modifier.fillMaxWidth()
        )

        Spacer(modifier = Modifier.height(16.dp))

        OutlinedTextField(
            value = password,
            onValueChange = { password = it },
            label = { Text("Пароль") },
            singleLine = true,
            enabled = !loading,
            visualTransformation = if (showPassword) VisualTransformation.None else PasswordVisualTransformation(),
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password, imeAction = ImeAction.Done),
            keyboardActions = KeyboardActions(onDone = { submit() }),
            trailingIcon = {
                TextButton(onClick = { showPassword = !showPassword }) { Text(if (showPassword) "Скрыть" else "Показать") }
            },
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
            onClick = { submit() }
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

        Spacer(modifier = Modifier.height(8.dp))
        val ctx = androidx.compose.ui.platform.LocalContext.current
        Row(horizontalArrangement = Arrangement.SpaceBetween, modifier = Modifier.fillMaxWidth()) {
            TextButton(onClick = {
                ctx.startActivity(android.content.Intent(ctx, AccountActivity::class.java))
            }) { Text("Регистрация") }
            TextButton(onClick = {
                ctx.startActivity(android.content.Intent(ctx, AccountActivity::class.java).putExtra(AccountActivity.EXTRA_RESTORE, true))
            }) { Text("Забыли пароль?") }
        }
        OutlinedButton(
            onClick = {
                ctx.startActivity(
                    android.content.Intent(ctx, MainShellActivity::class.java).putExtra(MainShellActivity.EXTRA_GUEST, true)
                )
            },
            modifier = Modifier.fillMaxWidth()
        ) { Text("Смотреть каталог без входа") }
    }
}

@Preview(showBackground = true)
@Composable
fun LoginScreenPreview() {
    AvtodrugTheme {
        Column(Modifier.fillMaxSize(), horizontalAlignment = Alignment.CenterHorizontally) {
            Text("Preview")
        }
    }
}
