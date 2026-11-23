package ru.definitly.myauchan

import android.app.Activity
import android.app.AlertDialog
import android.os.Bundle
import android.graphics.Typeface
import android.view.Gravity
import android.view.View
import android.widget.*
import org.json.JSONArray
import org.json.JSONObject
import java.io.BufferedReader
import java.io.InputStreamReader
import java.io.OutputStreamWriter
import java.net.HttpURLConnection
import java.net.URL
import java.util.UUID
import java.util.concurrent.Executors

private const val CABINET = "https://mobile.auchan.ru/lk"
private const val LOYMAX = "https://mobile.auchan.ru/ecom"
private const val TOKEN20_PASSWORD = "LqX~A4gR"
private const val PREFS = "auchan_settings"

private data class AppPalette(val background: Int, val surface: Int, val text: Int, val secondary: Int, val accent: Int, val accentText: Int)
private fun paletteFor(name: String): AppPalette = when (name) {
    "dark" -> AppPalette(0xFF17191D.toInt(),0xFF242830.toInt(),0xFFF1F3F5.toInt(),0xFFB4BBC5.toInt(),0xFF536D9B.toInt(),0xFFFFFFFF.toInt())
    "night" -> AppPalette(0xFF05070B.toInt(),0xFF101722.toInt(),0xFFDCE8F5.toInt(),0xFF8293A8.toInt(),0xFF183B62.toInt(),0xFFDCE8F5.toInt())
    "auchan" -> AppPalette(0xFFF4F6FA.toInt(),0xFFFFFFFF.toInt(),0xFF18243A.toInt(),0xFF59677D.toInt(),0xFFE31837.toInt(),0xFFFFFFFF.toInt())
    else -> AppPalette(0xFFF4F6F8.toInt(),0xFFFFFFFF.toInt(),0xFF202630.toInt(),0xFF687383.toInt(),0xFF3569A8.toInt(),0xFFFFFFFF.toInt())
}

class MainActivity : Activity() {
    private val executor = Executors.newSingleThreadExecutor()
    private lateinit var phoneInput: EditText
    private lateinit var allCheck: CheckBox
    private lateinit var status: TextView
    private lateinit var list: LinearLayout
    private lateinit var loadButton: Button
    private lateinit var loginButton: Button
    private val prefs by lazy { getSharedPreferences(PREFS, MODE_PRIVATE) }
    private val vault by lazy { SessionVault(this) }
    private val palette get() = paletteFor(prefs.getString("app_theme", "light") ?: "light")
    private var lastItems: List<JSONObject> = emptyList()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        buildScreen()
        val savedPhone = vault.read()?.optString("phone").orEmpty()
        if (savedPhone.isNotBlank()) {
            phoneInput.setText(savedPhone)
            status.text = "Сохранённая авторизация найдена."
        } else {
            status.text = "Введите номер телефона и нажмите «Войти по SMS»."
        }
    }

    private fun buildScreen() {
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(16), dp(16), dp(16), dp(12))
            setBackgroundColor(palette.background)
        }
        val title = TextView(this).apply {
            text = "Мои покупки Auchan"
            textSize = 23f
            setTypeface(null, Typeface.BOLD)
            setTextColor(palette.text)
        }
        root.addView(title)
        val themeSelector = Spinner(this)
        val themeNames = listOf("Светлая", "Тёмная", "Ночная", "Auchan")
        val themeKeys = listOf("light", "dark", "night", "auchan")
        val adapter = ArrayAdapter(this, android.R.layout.simple_spinner_item, themeNames).apply { setDropDownViewResource(android.R.layout.simple_spinner_dropdown_item) }
        themeSelector.adapter = adapter
        themeSelector.setSelection(themeKeys.indexOf(prefs.getString("app_theme", "light")).coerceAtLeast(0))
        root.addView(themeSelector, LinearLayout.LayoutParams(-1, dp(48)))
        themeSelector.onItemSelectedListener = object : AdapterView.OnItemSelectedListener {
            override fun onNothingSelected(parent: AdapterView<*>?) = Unit
            override fun onItemSelected(parent: AdapterView<*>?, view: View?, position: Int, id: Long) {
                val selected = themeKeys[position]
                if (selected != (prefs.getString("app_theme", "light") ?: "light")) {
                    val oldPhone = phoneInput.text.toString()
                    val oldStatus = status.text.toString()
                    prefs.edit().putString("app_theme", selected).apply()
                    buildScreen()
                    phoneInput.setText(oldPhone)
                    status.text = oldStatus
                    renderItems(lastItems)
                }
            }
        }
        root.addView(TextView(this).apply {
            text = "История покупок и состав чеков"
            textSize = 14f
            setTextColor(palette.secondary)
            setPadding(0, dp(4), 0, dp(14))
        })
        phoneInput = EditText(this).apply {
            hint = "Телефон, например +79991234567"
            inputType = 3
            textSize = 16f
            setSingleLine(true)
            setTextColor(palette.text)
            setHintTextColor(palette.secondary)
            setBackgroundTintList(android.content.res.ColorStateList.valueOf(palette.accent))
        }
        root.addView(phoneInput, LinearLayout.LayoutParams(-1, dp(52)))
        loginButton = Button(this).apply {
            text = "Войти по SMS"
            setOnClickListener { beginLogin() }
            backgroundTintList = android.content.res.ColorStateList.valueOf(palette.accent)
            setTextColor(palette.accentText)
        }
        root.addView(loginButton, LinearLayout.LayoutParams(-1, dp(48)))
        loadButton = Button(this).apply {
            text = "Загрузить покупки"
            setOnClickListener { loadHistory() }
            backgroundTintList = android.content.res.ColorStateList.valueOf(palette.accent)
            setTextColor(palette.accentText)
        }
        root.addView(loadButton, LinearLayout.LayoutParams(-1, dp(48)))
        allCheck = CheckBox(this).apply {
            text = "Загрузить всю историю (все страницы)"
            isChecked = true
            setTextColor(palette.text)
            buttonTintList = android.content.res.ColorStateList.valueOf(palette.accent)
        }
        root.addView(allCheck)
        status = TextView(this).apply {
            textSize = 14f
            setTextColor(palette.text)
            setPadding(0, dp(8), 0, dp(10))
        }
        root.addView(status)
        val scroll = ScrollView(this)
        list = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
        }
        scroll.addView(list)
        root.addView(scroll, LinearLayout.LayoutParams(-1, 0, 1f))
        setContentView(root)
    }

    private fun beginLogin() {
        val phone = phoneInput.text.toString().filter { it.isDigit() || it == '+' }
        if (!phone.matches(Regex("\\+?\\d{10,15}"))) {
            message("Проверьте номер телефона. Используйте международный формат.")
            return
        }
        setBusy(true, "Запрашиваю SMS…")
        executor.execute {
            try {
                val deviceId = prefs.getString("device_id", null) ?: UUID.randomUUID().toString().also {
                    prefs.edit().putString("device_id", it).apply()
                }
                val token20 = tokenFrom(request("GET", "$CABINET/clientauth/token",
                    headers = mapOf("login" to "checkmail_user", "password" to TOKEN20_PASSWORD)))
                val check = request("GET", "$CABINET/clientprofile/checkphone",
                    headers = mapOf("access_token" to token20, "phone" to phone,
                        "deviceid" to deviceId, "source" to "1"),
                    query = "needHash=true", allow400 = true)
                val result = check.opt("result")?.toString().orEmpty()
                val msg = first(check, "msg", "Msg", "resultmessage", "ResultMessage")
                val alreadySent = result == "2" ||
                    msg.contains("смс с кодом уже отправлено", true) ||
                    msg.contains("sms already sent", true)
                val checkError = first(check, "error", "Error")
                if (checkError.isNotBlank() && checkError !in listOf("0", "null") && !alreadySent) {
                    throw AuchanException("CheckPhone: ${msg.ifBlank { check.toString() }}")
                }
                runOnUiThread {
                    setBusy(false, if (alreadySent) "SMS уже отправлено. Введите код из этого сообщения." else "SMS запрошено. Введите код из сообщения.")
                    askPin(phone, deviceId)
                }
            } catch (e: Exception) {
                runOnUiThread { setBusy(false, readable(e)) }
            }
        }
    }

    private fun askPin(phone: String, deviceId: String) {
        val pinInput = EditText(this).apply {
            hint = "4 цифры"
            inputType = 2
            filters = arrayOf(android.text.InputFilter.LengthFilter(4))
        }
        AlertDialog.Builder(this)
            .setTitle("Код из SMS")
            .setMessage("Введите четырёхзначный код, отправленный Auchan.")
            .setView(pinInput)
            .setNegativeButton("Отмена", null)
            .setPositiveButton("Подтвердить") { _, _ ->
                val pin = pinInput.text.toString()
                if (!pin.matches(Regex("\\d{4}"))) {
                    message("Ожидался четырёхзначный SMS-код.")
                } else {
                    completeLogin(phone, deviceId, pin)
                }
            }.show()
    }

    private fun completeLogin(phone: String, deviceId: String, pin: String) {
        setBusy(true, "Проверяю код и сохраняю авторизацию…")
        executor.execute {
            try {
                val payload = request("GET", "$CABINET/clientauth/token",
                    headers = mapOf("phone" to phone, "password" to pin, "deviceid" to deviceId))
                val token = tokenFrom(payload)
                vault.write(JSONObject().put("token80", token).put("phone", phone))
                runOnUiThread {
                    phoneInput.setText(phone)
                    setBusy(false, "Авторизация сохранена на устройстве.")
                    loadHistory()
                }
            } catch (e: Exception) {
                runOnUiThread { setBusy(false, readable(e)) }
            }
        }
    }

    private fun loadHistory() {
        val session = vault.read()
        if (session == null) {
            message("Сначала выполните вход по SMS.")
            return
        }
        val token = session.optString("token80")
        if (token.isBlank()) {
            message("Сохранённая сессия повреждена. Выполните вход заново.")
            return
        }
        val all = allCheck.isChecked
        setBusy(true, "Загружаю историю покупок…")
        list.removeAllViews()
        executor.execute {
            try {
                val items = mutableListOf<JSONObject>()
                var offset = 0
                var total = -1
                do {
                    val body = JSONObject()
                        .put("channel", "M")
                        .put("pagination", JSONObject().put("offset", offset).put("limit", 10))
                        .put("filter", JSONObject().put("id", JSONObject.NULL))
                    val page = request("POST", "$LOYMAX/lk/loyalty/history",
                        headers = mapOf("X-Token-CMD" to token, "Content-Type" to "application/json"),
                        body = body.toString())
                    val pageItems = unwrap(page)
                    if (total < 0) total = historyTotal(page)
                    items.addAll(pageItems)
                    offset += pageItems.size
                    if (!all || pageItems.isEmpty() || (total >= 0 && offset >= total) || pageItems.size < 10) break
                } while (offset < 10000)
                runOnUiThread {
                    setBusy(false, "Получено записей: ${items.size}" + if (total >= 0) " из $total" else "")
                    renderItems(items)
                }
            } catch (e: Exception) {
                runOnUiThread {
                    if (e is AuchanException && e.status == 401) {
                        vault.clear()
                        setBusy(false, "Сессия истекла. Нажмите «Войти по SMS».")
                    } else setBusy(false, readable(e))
                }
            }
        }
    }

    private fun renderItems(items: List<JSONObject>) {
        lastItems = items
        list.removeAllViews()
        if (items.isEmpty()) {
            list.addView(TextView(this).apply { text = "Покупок не найдено."; textSize = 16f })
            return
        }
        items.forEachIndexed { index, item ->
            val card = LinearLayout(this).apply {
                orientation = LinearLayout.VERTICAL
                setPadding(dp(14), dp(12), dp(14), dp(12))
                setBackgroundColor(palette.surface)
                elevation = dp(2).toFloat()
            }
            val date = first(item, "date", "Date")
            val store = first(item, "locationName", "LocationName", "location", "Location").ifBlank { "Магазин не указан" }
            val amount = first(item, "totalValue", "TotalValue").ifBlank { "—" }
            val receipt = first(item, "trnNo", "TrnNo", "transactionNumber", "TransactionNumber").ifBlank { "—" }
            val till = first(item, "till", "Till").ifBlank { "—" }
            val operation = first(item, "typeName", "TypeName", "operationType", "OperationType").ifBlank { "Покупка" }
            card.addView(TextView(this).apply {
                text = "$date · $amount ₽"
                textSize = 18f
                setTypeface(null, Typeface.BOLD)
                setTextColor(palette.text)
            })
            card.addView(TextView(this).apply {
                text = store
                textSize = 16f
                setPadding(0, dp(3), 0, dp(3))
                setTextColor(palette.text)
            })
            card.addView(TextView(this).apply {
                text = "Чек $receipt · касса $till · $operation"
                textSize = 13f
                setTextColor(palette.secondary)
            })
            val order = item.optJSONObject("order_details") ?: item.optJSONObject("OrderDetails") ?: item.optJSONObject("orderDetails")
            if (order != null) {
                val discount = first(order, "discountValue", "DiscountValue")
                if (discount.isNotBlank() && discount != "0") {
                    card.addView(detail("Скидка: $discount ₽"))
                }
                val products = order.optJSONArray("products") ?: order.optJSONArray("Products")
                if (products != null && products.length() > 0) {
                    card.addView(detail("Товары: ${products.length()}"))
                    for (i in 0 until products.length()) {
                        val p = products.optJSONObject(i) ?: continue
                        val name = first(p, "name", "Name").ifBlank { "Без названия" }
                        val quantity = first(p, "quantity", "Quantity").ifBlank { "—" }
                        val value = first(p, "value", "Value").ifBlank { "—" }
                        card.addView(detail("• $name — $quantity шт. · $value ₽"))
                    }
                }
            }
            list.addView(card, LinearLayout.LayoutParams(-1, -2).apply {
                bottomMargin = dp(10)
            })
        }
    }

    private fun detail(text: String) = TextView(this).apply {
        this.text = text
        textSize = 13f
        setTextColor(palette.secondary)
        setPadding(0, dp(3), 0, 0)
    }

    private fun request(
        method: String, url: String, headers: Map<String, String> = emptyMap(),
        query: String? = null, body: String? = null, allow400: Boolean = false
    ): JSONObject {
        val fullUrl = if (query != null) "$url?$query" else url
        val connection = (URL(fullUrl).openConnection() as HttpURLConnection).apply {
            requestMethod = method
            connectTimeout = 35000
            readTimeout = 35000
            headers.forEach { (k, v) -> setRequestProperty(k, v) }
            setRequestProperty("User-Agent", "MyAuchan/4.1.2")
            setRequestProperty("Accept", "application/json")
            if (body != null) {
                doOutput = true
                setRequestProperty("Content-Type", "application/json")
            }
        }
        try {
            if (body != null) OutputStreamWriter(connection.outputStream, Charsets.UTF_8).use { it.write(body) }
            val code = connection.responseCode
            val stream = if (code in 200..299 || (allow400 && code == 400)) connection.inputStreamOrError() else connection.errorStream
            val response = stream?.bufferedReader(Charsets.UTF_8)?.use { it.readText() }.orEmpty()
            if (code !in 200..299 && !(allow400 && code == 400)) {
                throw AuchanException("HTTP $code: ${serverMessage(response)}", code)
            }
            if (response.isBlank()) return JSONObject()
            return try { JSONObject(response) } catch (_: Exception) {
                throw AuchanException("Сервер вернул неожиданный ответ: ${response.take(300)}")
            }
        } finally {
            connection.disconnect()
        }
    }

    private fun HttpURLConnection.inputStreamOrError() =
        try { inputStream } catch (_: Exception) { errorStream }

    private fun serverMessage(raw: String): String {
        return try {
            val j = JSONObject(raw)
            first(j, "resultmessage", "ResultMessage", "msg", "Msg", "message", "Message", "error", "Error")
                .ifBlank { raw.take(500) }
        } catch (_: Exception) { raw.take(500) }
    }

    private fun tokenFrom(json: JSONObject): String {
        val direct = first(json, "token", "Token", "access_token", "accessToken", "AccessToken")
        if (direct.isNotBlank()) return direct
        listOf("data", "Data", "result", "Result").forEach { key ->
            val nested = json.optJSONObject(key) ?: return@forEach
            val value = first(nested, "token", "Token", "access_token", "accessToken", "AccessToken")
            if (value.isNotBlank()) return value
        }
        throw AuchanException("В ответе сервера не найден TokenDto.token: ${json.toString().take(400)}")
    }

    private fun unwrap(json: JSONObject): List<JSONObject> {
        val data = json.optJSONObject("data") ?: json.optJSONObject("Data") ?: return emptyList()
        val array = data.optJSONArray("items") ?: data.optJSONArray("Items") ?: return emptyList()
        return (0 until array.length()).mapNotNull { array.optJSONObject(it) }
    }

    private fun historyTotal(json: JSONObject): Int {
        val meta = json.optJSONObject("meta") ?: json.optJSONObject("Meta") ?: return -1
        val pagination = meta.optJSONObject("pagination") ?: meta.optJSONObject("Pagination") ?: return -1
        return pagination.optInt("total", pagination.optInt("Total", -1))
    }

    private fun first(json: JSONObject, vararg keys: String): String {
        keys.forEach { key ->
            val value = json.opt(key)
            if (value != null && value != JSONObject.NULL && value.toString().isNotBlank()) return value.toString()
        }
        return ""
    }

    private fun setBusy(busy: Boolean, text: String) {
        status.text = text
        loadButton.isEnabled = !busy
        loginButton.isEnabled = !busy
        phoneInput.isEnabled = !busy
        allCheck.isEnabled = !busy
    }

    private fun message(text: String) = AlertDialog.Builder(this).setMessage(text).setPositiveButton("OK", null).show()
    private fun readable(e: Exception): String = when (e) {
        is AuchanException -> e.message ?: "Ошибка Auchan"
        else -> "Ошибка сети: ${e.message ?: e.javaClass.simpleName}"
    }
    private fun dp(value: Int) = (value * resources.displayMetrics.density).toInt()
    override fun onDestroy() {
        executor.shutdownNow()
        super.onDestroy()
    }
}

private class AuchanException(message: String, val status: Int = -1) : Exception(message)
