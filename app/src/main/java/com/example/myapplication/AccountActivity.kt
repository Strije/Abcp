package com.example.myapplication

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.unit.dp
import com.example.myapplication.server.AppServer
import com.example.myapplication.ui.theme.AvtodrugTheme
import kotlinx.coroutines.launch

/** Регистрация и восстановление пароля — через наш сервер (ABCP принимает их только с разрешённого IP). */
@OptIn(ExperimentalMaterial3Api::class)
class AccountActivity : ComponentActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val server = AppServer(this)

        // Вернуться ко входу с подставленным логином. password — только сразу после регистрации: экран входа
        // подставит его и войдёт сам. Передаём лишь в результате внутри приложения, нигде не сохраняем и не пишем в лог
        fun toLogin(login: String?, password: String? = null) {
            val data = android.content.Intent().putExtra(EXTRA_LOGIN, login.orEmpty())
            if (!password.isNullOrEmpty()) data.putExtra(EXTRA_PASSWORD, password)
            setResult(RESULT_OK, data)
            finish()
        }

        setContent {
            AvtodrugTheme {
                var restoreMode by rememberSaveable { mutableStateOf(intent.getBooleanExtra(EXTRA_RESTORE, false)) }
                var prefill by rememberSaveable { mutableStateOf(intent.getStringExtra(EXTRA_LOGIN).orEmpty()) }
                Scaffold(topBar = { TopAppBar(navigationIcon = { com.example.myapplication.ui.BackButton() }, title = { Text(if (restoreMode) "Восстановление пароля" else "Регистрация") }) }) { p ->
                    Column(
                        Modifier.padding(p).fillMaxSize().imePadding().verticalScroll(rememberScrollState()).padding(16.dp),
                        verticalArrangement = Arrangement.spacedBy(12.dp)
                    ) {
                        if (restoreMode) RestoreForm(
                            server, prefill, onDone = { toLogin(it) },
                            onRegister = { prefill = it; restoreMode = false }
                        ) else RegisterForm(
                            server, prefill, onDone = { login, password -> toLogin(login, password) },
                            onRestore = { prefill = it; restoreMode = true }
                        )
                    }
                }
            }
        }
    }

    companion object {
        const val EXTRA_RESTORE = "extra_restore"
        /** Телефон/email: на вход — подставить в форму, на выход — в поле логина */
        const val EXTRA_LOGIN = "extra_login"
        /** Только на выход и только после регистрации без подтверждения: пароль, с которым экран входа войдёт сам */
        const val EXTRA_PASSWORD = "extra_password"
    }
}

/** Политика обработки персональных данных на сайте магазина */
const val DATA_PROTECTION_URL = "https://avtodrug92.ru/data_protection"

/** Простая проверка почты: что-то до «@» и точка внутри домена. Пусто — почта не указана, это не ошибка. */
fun emailProblem(input: String): String? {
    val e = input.trim()
    if (e.isEmpty()) return null
    val at = e.indexOf('@')
    val domain = if (at > 0) e.substring(at + 1) else ""
    val dot = domain.lastIndexOf('.')
    val ok = e.none { it.isWhitespace() } && !domain.contains('@') && dot > 0 && dot < domain.length - 1
    return if (ok) null else "Проверьте email — например, ivan@mail.ru"
}

/** Российский мобильный в виде 79XXXXXXXXX или null. */
fun normalizeMobile(input: String): String? {
    var d = input.filter { it.isDigit() }
    if (d.length == 11 && d.startsWith("8")) d = "7" + d.drop(1)
    if (d.length == 10 && d.startsWith("9")) d = "7$d"
    return d.takeIf { it.length == 11 && it.startsWith("79") }
}

@Composable
private fun RegisterForm(server: AppServer, prefill: String, onDone: (String?, String?) -> Unit, onRestore: (String) -> Unit) {
    val scope = rememberCoroutineScope()
    val ctx = LocalContext.current
    val focus = LocalFocusManager.current
    // rememberSaveable: введённое переживает и уход приложения из памяти (например, пока человек читает SMS)
    var name by rememberSaveable { mutableStateOf("") }
    var surname by rememberSaveable { mutableStateOf("") }
    // Только 10 цифр после +7 — маска рисует «(978) 123-45-67»
    var phone by rememberSaveable { mutableStateOf(com.example.myapplication.ui.phoneDigits(prefill.takeIf { !it.contains('@') }.orEmpty())) }
    var exists by rememberSaveable { mutableStateOf(false) }
    var email by rememberSaveable { mutableStateOf("") }
    var pass by rememberSaveable { mutableStateOf("") }
    var office by rememberSaveable { mutableStateOf(StoreInfo.stores.first().abcpOfficeId) }
    var busy by remember { mutableStateOf(false) }
    var error by rememberSaveable { mutableStateOf<String?>(null) }
    var done by rememberSaveable { mutableStateOf(false) }
    var needsActivation by rememberSaveable { mutableStateOf(false) }

    fun submit() {
        if (busy) return
        exists = false
        val mobile = normalizeMobile(phone)
        // Как при входе: пробелы по краям пароля не считаются (иначе аккаунт создаётся, а войти нельзя)
        val p = pass.trim()
        error = when {
            name.isBlank() -> "Укажите имя"
            mobile == null -> "Укажите мобильный номер, например +7 978 123-45-67"
            emailProblem(email) != null -> emailProblem(email)
            passwordProblem(p) != null -> "Пароль: ${passwordProblem(p)}"
            else -> null
        }
        if (error != null || mobile == null) return
        focus.clearFocus()
        busy = true
        scope.launch {
            try {
                needsActivation = server.register(name.trim(), surname.trim(), mobile, email.trim(), p, office)
                com.example.myapplication.Analytics.event("register", mapOf("office" to office))
                done = true
                // Подтверждать нечего — не заставляем вводить пароль второй раз: экран входа войдёт сам
                if (!needsActivation) onDone(mobile, p)
            } catch (e: com.example.myapplication.server.ServerException) {
                exists = e.code == 409
                error = e.message ?: "Регистрация не прошла"
            } catch (e: Exception) {
                com.example.myapplication.Analytics.error("Регистрация", e)
                error = com.example.myapplication.ui.userMessage(e, "Регистрация не прошла. Попробуйте ещё раз или напишите менеджеру.")
            } finally {
                busy = false
            }
        }
    }

    if (done) {
        if (needsActivation) {
            Text("Аккаунт создан.", style = MaterialTheme.typography.titleMedium)
            Text("Мы отправили код подтверждения — подтвердите номер или почту, затем войдите.")
        } else {
            Text("Готово! Аккаунт создан.", style = MaterialTheme.typography.titleMedium)
            Text("Сейчас войдём с номером телефона и паролем, который вы указали.")
        }
        // Обычно сюда не доходим (ко входу уходим сами); кнопка — на случай, если экран восстановлен системой
        Button(
            onClick = { onDone(normalizeMobile(phone), if (needsActivation) null else pass.trim()) },
            modifier = Modifier.fillMaxWidth()
        ) { Text(if (needsActivation) "Ко входу" else "Войти") }
        return
    }

    val next = KeyboardOptions(imeAction = ImeAction.Next)
    OutlinedTextField(name, { name = it }, label = { Text("Имя") }, singleLine = true, keyboardOptions = next, modifier = Modifier.fillMaxWidth())
    OutlinedTextField(surname, { surname = it }, label = { Text("Фамилия") }, singleLine = true, keyboardOptions = next, modifier = Modifier.fillMaxWidth())
    OutlinedTextField(
        phone, { phone = com.example.myapplication.ui.phoneDigits(it) },
        label = { Text("Мобильный телефон") }, placeholder = { Text("(978) 123-45-67") },
        prefix = { Text("+7 ") },
        visualTransformation = com.example.myapplication.ui.RuPhoneMask,
        supportingText = { if (phone.isNotEmpty() && phone.length < 10) Text("Ещё ${10 - phone.length} цифр") },
        singleLine = true, keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Phone, imeAction = ImeAction.Next),
        modifier = Modifier.fillMaxWidth()
    )
    OutlinedTextField(
        email, { email = it }, label = { Text("Email (необязательно)") }, singleLine = true,
        keyboardOptions = KeyboardOptions(autoCorrectEnabled = false, keyboardType = KeyboardType.Email, imeAction = ImeAction.Next),
        modifier = Modifier.fillMaxWidth()
    )
    PasswordField(pass, { pass = it }, "Пароль", onDone = { submit() })
    Text("Ваш магазин", style = MaterialTheme.typography.titleSmall)
    StoreInfo.stores.forEach { s ->
        Row(verticalAlignment = androidx.compose.ui.Alignment.CenterVertically) {
            RadioButton(selected = office == s.abcpOfficeId, onClick = { office = s.abcpOfficeId })
            Text(s.address)
        }
    }
    error?.let { Text(it, color = MaterialTheme.colorScheme.error) }
    if (exists) {
        // Аккаунт уже есть — вместо дубля предлагаем войти или вспомнить пароль
        val login = normalizeMobile(phone) ?: email.trim()
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.fillMaxWidth()) {
            Button(onClick = { onDone(login, null) }, modifier = Modifier.weight(1f)) { Text("Войти") }
            OutlinedButton(onClick = { onRestore(login) }, modifier = Modifier.weight(1f)) { Text("Напомнить пароль") }
        }
    }
    Button(
        enabled = !busy,
        modifier = Modifier.fillMaxWidth().height(52.dp),
        onClick = { submit() }
    ) { Text(if (busy) "Отправляем…" else "Зарегистрироваться") }
    Text(
        "Регистрируясь, вы соглашаетесь с политикой обработки персональных данных",
        style = MaterialTheme.typography.bodySmall,
        color = MaterialTheme.colorScheme.primary,
        textDecoration = TextDecoration.Underline,
        modifier = Modifier.fillMaxWidth().clickable {
            com.example.myapplication.ui.openExternal(
                ctx, android.content.Intent(android.content.Intent.ACTION_VIEW, android.net.Uri.parse(DATA_PROTECTION_URL))
            )
        }.padding(vertical = 8.dp)
    )
}

@Composable
private fun RestoreForm(server: AppServer, prefill: String, onDone: (String?) -> Unit, onRegister: (String) -> Unit) {
    val scope = rememberCoroutineScope()
    val focus = LocalFocusManager.current
    // rememberSaveable: шаг «код отправлен» и введённое не теряются, пока человек читает SMS в другом приложении
    var login by rememberSaveable { mutableStateOf(prefill) }
    var notFound by rememberSaveable { mutableStateOf(false) }
    var code by rememberSaveable { mutableStateOf("") }
    var pass by rememberSaveable { mutableStateOf("") }
    var codeSent by rememberSaveable { mutableStateOf(false) }
    var busy by remember { mutableStateOf(false) }
    var info by rememberSaveable { mutableStateOf<String?>(null) }
    var error by rememberSaveable { mutableStateOf<String?>(null) }
    var done by rememberSaveable { mutableStateOf(false) }

    fun submit() {
        if (busy) return
        val target = normalizeMobile(login) ?: login.trim()
        // Как при входе: пробелы по краям пароля не считаются
        val p = pass.trim()
        error = null
        notFound = false
        if (target.length < 5) { error = "Укажите телефон или email"; return }
        if (codeSent && code.isBlank()) { error = "Введите код из SMS"; return }
        if (codeSent && passwordProblem(p) != null) { error = "Пароль: ${passwordProblem(p)}"; return }
        focus.clearFocus()
        busy = true
        scope.launch {
            try {
                if (!codeSent) {
                    info = server.restore(target) ?: "Код отправлен"
                    // Для email ABCP присылает ссылку — второй шаг на сайте, в приложении ждать нечего
                    codeSent = !target.contains('@')
                } else {
                    server.restore(target, code.trim(), p)
                    done = true
                }
            } catch (e: com.example.myapplication.server.ServerException) {
                notFound = e.code == 404 && !codeSent
                error = if (notFound) "Такой номер или email у нас не зарегистрирован." else e.message ?: "Не получилось"
            } catch (e: Exception) {
                com.example.myapplication.Analytics.error("Восстановление пароля", e)
                error = com.example.myapplication.ui.userMessage(e, "Не получилось. Попробуйте ещё раз или напишите менеджеру.")
            } finally {
                busy = false
            }
        }
    }

    if (done) {
        Text("Пароль изменён.", style = MaterialTheme.typography.titleMedium)
        Button(onClick = { onDone(normalizeMobile(login) ?: login.trim()) }, modifier = Modifier.fillMaxWidth()) { Text("Ко входу") }
        return
    }

    Text("Укажите телефон или email. На телефон придёт код в SMS, на email — письмо со ссылкой.")
    OutlinedTextField(
        login, { login = it }, label = { Text("Телефон или email") }, singleLine = true, enabled = !codeSent,
        keyboardOptions = KeyboardOptions(autoCorrectEnabled = false, keyboardType = KeyboardType.Email, imeAction = ImeAction.Done),
        keyboardActions = KeyboardActions(onDone = { submit() }),
        modifier = Modifier.fillMaxWidth()
    )
    if (codeSent) {
        OutlinedTextField(
            code, { code = it }, label = { Text("Код из SMS") }, singleLine = true,
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number, imeAction = ImeAction.Next),
            modifier = Modifier.fillMaxWidth()
        )
        PasswordField(pass, { pass = it }, "Новый пароль", onDone = { submit() })
    }
    info?.let { Text(it) }
    error?.let { Text(it, color = MaterialTheme.colorScheme.error) }
    if (notFound) OutlinedButton(onClick = { onRegister(login.trim()) }, modifier = Modifier.fillMaxWidth()) {
        Text("Зарегистрироваться с этим номером")
    }
    Button(
        enabled = !busy,
        modifier = Modifier.fillMaxWidth().height(52.dp),
        onClick = { submit() }
    ) { Text(if (busy) "Отправляем…" else if (codeSent) "Сохранить пароль" else "Получить код") }
}

/** Правило пароля ABCP: от 8 символов, цифры, строчные и заглавные буквы. null — пароль подходит. */
fun passwordProblem(p: String): String? {
    val missing = buildList {
        if (p.length < 8) add("не короче 8 символов")
        if (p.none { it.isDigit() }) add("цифра")
        if (p.none { it.isLowerCase() }) add("строчная буква")
        if (p.none { it.isUpperCase() }) add("заглавная буква")
    }
    return if (missing.isEmpty()) null else "нужно — " + missing.joinToString(", ")
}

/** Пароль с «глазом» и живой проверкой правил ABCP — чтобы не узнавать о них после отказа. */
@Composable
private fun PasswordField(value: String, onChange: (String) -> Unit, label: String, onDone: () -> Unit) {
    var visible by remember { mutableStateOf(false) }
    OutlinedTextField(
        value, onChange, label = { Text(label) }, singleLine = true,
        visualTransformation = if (visible) androidx.compose.ui.text.input.VisualTransformation.None else PasswordVisualTransformation(),
        // Последнее поле формы: «Готово» на клавиатуре отправляет форму
        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password, imeAction = ImeAction.Done),
        keyboardActions = KeyboardActions(onDone = { onDone() }),
        trailingIcon = { TextButton(onClick = { visible = !visible }) { Text(if (visible) "Скрыть" else "Показать") } },
        supportingText = {
            fun mark(ok: Boolean, t: String) = (if (ok) "✓ " else "○ ") + t
            Text(
                listOf(
                    mark(value.length >= 8, "8 символов"),
                    mark(value.any { it.isDigit() }, "цифра"),
                    mark(value.any { it.isLowerCase() }, "строчная"),
                    mark(value.any { it.isUpperCase() }, "заглавная")
                ).joinToString("   "),
                color = if (passwordProblem(value) == null) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurfaceVariant
            )
        },
        modifier = Modifier.fillMaxWidth()
    )
}
