package com.example.myapplication

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import com.example.myapplication.server.AppServer
import com.example.myapplication.ui.theme.AvtodrugTheme
import kotlinx.coroutines.launch

/** Регистрация и восстановление пароля — через наш сервер (ABCP принимает их только с разрешённого IP). */
@OptIn(ExperimentalMaterial3Api::class)
class AccountActivity : ComponentActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val restoreMode = intent.getBooleanExtra(EXTRA_RESTORE, false)
        val server = AppServer(this)

        setContent {
            AvtodrugTheme {
                Scaffold(topBar = { TopAppBar(title = { Text(if (restoreMode) "Восстановление пароля" else "Регистрация") }) }) { p ->
                    Column(
                        Modifier.padding(p).fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp),
                        verticalArrangement = Arrangement.spacedBy(12.dp)
                    ) {
                        if (restoreMode) RestoreForm(server) { finish() } else RegisterForm(server) { finish() }
                    }
                }
            }
        }
    }

    companion object {
        const val EXTRA_RESTORE = "extra_restore"
    }
}

/** Российский мобильный в виде 79XXXXXXXXX или null. */
fun normalizeMobile(input: String): String? {
    var d = input.filter { it.isDigit() }
    if (d.length == 11 && d.startsWith("8")) d = "7" + d.drop(1)
    if (d.length == 10 && d.startsWith("9")) d = "7$d"
    return d.takeIf { it.length == 11 && it.startsWith("79") }
}

@Composable
private fun RegisterForm(server: AppServer, onDone: () -> Unit) {
    val scope = rememberCoroutineScope()
    var name by remember { mutableStateOf("") }
    var surname by remember { mutableStateOf("") }
    var phone by remember { mutableStateOf("") }
    var email by remember { mutableStateOf("") }
    var pass by remember { mutableStateOf("") }
    var office by remember { mutableStateOf(StoreInfo.stores.first().abcpOfficeId) }
    var busy by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }
    var done by remember { mutableStateOf(false) }

    if (done) {
        Text("Готово! Аккаунт создан.", style = MaterialTheme.typography.titleMedium)
        Text("Войдите с номером телефона (или email) и паролем, который вы указали.")
        Button(onClick = onDone, modifier = Modifier.fillMaxWidth()) { Text("Ко входу") }
        return
    }

    OutlinedTextField(name, { name = it }, label = { Text("Имя") }, singleLine = true, modifier = Modifier.fillMaxWidth())
    OutlinedTextField(surname, { surname = it }, label = { Text("Фамилия") }, singleLine = true, modifier = Modifier.fillMaxWidth())
    OutlinedTextField(
        phone, { phone = it }, label = { Text("Мобильный телефон") }, placeholder = { Text("+7 978 123-45-67") },
        singleLine = true, keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Phone), modifier = Modifier.fillMaxWidth()
    )
    OutlinedTextField(
        email, { email = it }, label = { Text("Email (необязательно)") }, singleLine = true,
        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Email), modifier = Modifier.fillMaxWidth()
    )
    OutlinedTextField(
        pass, { pass = it }, label = { Text("Пароль (от 6 символов)") }, singleLine = true,
        visualTransformation = PasswordVisualTransformation(),
        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password), modifier = Modifier.fillMaxWidth()
    )
    Text("Ваш магазин", style = MaterialTheme.typography.titleSmall)
    StoreInfo.stores.forEach { s ->
        Row(verticalAlignment = androidx.compose.ui.Alignment.CenterVertically) {
            RadioButton(selected = office == s.abcpOfficeId, onClick = { office = s.abcpOfficeId })
            Text(s.address)
        }
    }
    error?.let { Text(it, color = MaterialTheme.colorScheme.error) }
    Button(
        enabled = !busy,
        modifier = Modifier.fillMaxWidth().height(52.dp),
        onClick = {
            val mobile = normalizeMobile(phone)
            error = when {
                name.isBlank() -> "Укажите имя"
                mobile == null -> "Укажите мобильный номер, например +7 978 123-45-67"
                pass.length < 6 -> "Пароль — не короче 6 символов"
                else -> null
            }
            if (error != null) return@Button
            busy = true
            scope.launch {
                try {
                    server.register(name.trim(), surname.trim(), mobile!!, email.trim(), pass, office)
                    done = true
                } catch (e: Exception) {
                    error = e.message ?: "Регистрация не прошла"
                } finally {
                    busy = false
                }
            }
        }
    ) { Text(if (busy) "Отправляем…" else "Зарегистрироваться") }
}

@Composable
private fun RestoreForm(server: AppServer, onDone: () -> Unit) {
    val scope = rememberCoroutineScope()
    var login by remember { mutableStateOf("") }
    var code by remember { mutableStateOf("") }
    var pass by remember { mutableStateOf("") }
    var codeSent by remember { mutableStateOf(false) }
    var busy by remember { mutableStateOf(false) }
    var info by remember { mutableStateOf<String?>(null) }
    var error by remember { mutableStateOf<String?>(null) }
    var done by remember { mutableStateOf(false) }

    if (done) {
        Text("Пароль изменён.", style = MaterialTheme.typography.titleMedium)
        Button(onClick = onDone, modifier = Modifier.fillMaxWidth()) { Text("Ко входу") }
        return
    }

    Text("Укажите телефон или email. На телефон придёт код в SMS, на email — письмо со ссылкой.")
    OutlinedTextField(
        login, { login = it }, label = { Text("Телефон или email") }, singleLine = true, enabled = !codeSent,
        modifier = Modifier.fillMaxWidth()
    )
    if (codeSent) {
        OutlinedTextField(
            code, { code = it }, label = { Text("Код из SMS") }, singleLine = true,
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number), modifier = Modifier.fillMaxWidth()
        )
        OutlinedTextField(
            pass, { pass = it }, label = { Text("Новый пароль") }, singleLine = true,
            visualTransformation = PasswordVisualTransformation(), modifier = Modifier.fillMaxWidth()
        )
    }
    info?.let { Text(it) }
    error?.let { Text(it, color = MaterialTheme.colorScheme.error) }
    Button(
        enabled = !busy,
        modifier = Modifier.fillMaxWidth().height(52.dp),
        onClick = {
            val target = normalizeMobile(login) ?: login.trim()
            error = null
            if (target.length < 5) { error = "Укажите телефон или email"; return@Button }
            if (codeSent && (code.isBlank() || pass.length < 6)) { error = "Введите код и пароль от 6 символов"; return@Button }
            busy = true
            scope.launch {
                try {
                    if (!codeSent) {
                        info = server.restore(target) ?: "Код отправлен"
                        // Для email ABCP присылает ссылку — второй шаг на сайте, в приложении ждать нечего
                        codeSent = !target.contains('@')
                    } else {
                        server.restore(target, code.trim(), pass)
                        done = true
                    }
                } catch (e: Exception) {
                    error = e.message ?: "Не получилось"
                } finally {
                    busy = false
                }
            }
        }
    ) { Text(if (busy) "Отправляем…" else if (codeSent) "Сохранить пароль" else "Получить код") }
}
