package ru.sedoysadist.mumuremote

import android.app.Activity
import android.app.AlertDialog
import android.app.Dialog
import android.graphics.BitmapFactory
import android.graphics.Color
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.text.InputType
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.widget.*
import org.json.JSONArray
import org.json.JSONObject
import java.io.ByteArrayOutputStream
import java.net.HttpURLConnection
import java.net.URL
import java.util.concurrent.Executors

class MainActivity : Activity() {
    private val executor = Executors.newCachedThreadPool()
    private val handler = Handler(Looper.getMainLooper())
    private lateinit var prefs: android.content.SharedPreferences
    private lateinit var serverInput: EditText
    private lateinit var pairInput: EditText
    private lateinit var statusText: TextView
    private lateinit var accountContainer: LinearLayout
    private lateinit var refreshButton: Button
    private var autoRefresh = true

    private val bg = Color.rgb(11, 13, 16)
    private val panel = Color.rgb(24, 27, 33)
    private val panel2 = Color.rgb(32, 36, 44)
    private val text = Color.rgb(242, 244, 248)
    private val muted = Color.rgb(160, 167, 179)
    private val accent = Color.rgb(124, 77, 255)
    private val ok = Color.rgb(67, 180, 107)
    private val warn = Color.rgb(255, 171, 64)

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        prefs = getSharedPreferences("mumu_remote", MODE_PRIVATE)
        buildUi()
        handler.postDelayed(::autoRefreshTick, 1200)
    }

    override fun onDestroy() {
        autoRefresh = false
        handler.removeCallbacksAndMessages(null)
        executor.shutdownNow()
        super.onDestroy()
    }

    private fun buildUi() {
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(14), dp(14), dp(14), dp(14))
            setBackgroundColor(bg)
        }

        root.addView(TextView(this).apply {
            text = "MuMu Remote"
            textSize = 25f
            setTextColor(text)
            setTypeface(typeface, android.graphics.Typeface.BOLD)
        })
        root.addView(TextView(this).apply {
            text = "Управление MuMu Bot"
            textSize = 13f
            setTextColor(muted)
            setPadding(0, dp(2), 0, dp(12))
        })

        val connection = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(12), dp(10), dp(12), dp(10))
            setBackgroundColor(panel)
        }

        serverInput = EditText(this).apply {
            hint = "http://192.168.1.10:8765"
            setHintTextColor(muted)
            setTextColor(text)
            setSingleLine(true)
            setText(prefs.getString("server_url", "") ?: "")
            setBackgroundColor(panel2)
            setPadding(dp(10), 0, dp(10), 0)
        }
        connection.addView(serverInput, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(48)))

        val pairRow = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL }
        pairInput = EditText(this).apply {
            hint = "Код сопряжения"
            setHintTextColor(muted)
            setTextColor(text)
            inputType = InputType.TYPE_CLASS_NUMBER
            setSingleLine(true)
            setBackgroundColor(panel2)
            setPadding(dp(10), 0, dp(10), 0)
        }
        pairRow.addView(pairInput, LinearLayout.LayoutParams(0, dp(46), 1f))

        val pairButton = Button(this).apply {
            text = "Сопрячь"
            setOnClickListener { pair() }
        }
        pairRow.addView(pairButton, LinearLayout.LayoutParams(dp(110), dp(46)).apply { marginStart = dp(8) })
        connection.addView(pairRow, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply { topMargin = dp(8) })

        statusText = TextView(this).apply {
            text = "Сервер: не подключён"
            setTextColor(muted)
            textSize = 13f
            setPadding(0, dp(8), 0, 0)
        }
        connection.addView(statusText)

        root.addView(connection, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT))

        val topRow = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            setPadding(0, dp(10), 0, dp(8))
        }
        topRow.addView(TextView(this).apply {
            text = "Аккаунты"
            textSize = 20f
            setTextColor(text)
            setTypeface(typeface, android.graphics.Typeface.BOLD)
        }, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))

        refreshButton = Button(this).apply {
            text = "Обновить"
            setOnClickListener { refreshAccounts(true) }
        }
        topRow.addView(refreshButton)
        root.addView(topRow)

        val scroll = ScrollView(this)
        accountContainer = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
        }
        scroll.addView(accountContainer, ViewGroup.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT))
        root.addView(scroll, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f))

        setContentView(root)
    }

    private fun pair() {
        val url = baseUrl()
        val code = pairInput.text.toString().trim()
        if (url.isBlank() || code.isBlank()) {
            toast("Укажи адрес сервера и код сопряжения")
            return
        }
        setBusy(true)
        executor.execute {
            try {
                val result = Api.postJson(url, "/api/v1/pair", JSONObject().put("code", code), null)
                val token = result.optString("token")
                if (token.isBlank()) error("Сервер не вернул токен")
                prefs.edit().putString("server_url", url).putString("token", token).apply()
                runOnUiThread {
                    pairInput.setText("")
                    statusText.text = "Сервер: сопряжение выполнено"
                    statusText.setTextColor(ok)
                    setBusy(false)
                    refreshAccounts(true)
                }
            } catch (e: Exception) {
                runOnUiThread {
                    setBusy(false)
                    statusText.text = "Ошибка сопряжения: ${e.message}"
                    statusText.setTextColor(warn)
                }
            }
        }
    }

    private fun refreshAccounts(showErrors: Boolean) {
        val url = baseUrl()
        val token = token()
        if (url.isBlank() || token.isBlank()) {
            if (showErrors) toast("Сначала выполни сопряжение")
            return
        }
        prefs.edit().putString("server_url", url).apply()
        executor.execute {
            try {
                val result = Api.getJson(url, "/api/v1/accounts", token)
                val accounts = result.optJSONArray("accounts") ?: JSONArray()
                runOnUiThread {
                    statusText.text = "Сервер: подключён • аккаунтов: ${accounts.length()}"
                    statusText.setTextColor(ok)
                    renderAccounts(accounts)
                }
            } catch (e: Exception) {
                if (showErrors) runOnUiThread {
                    statusText.text = "Нет соединения: ${e.message}"
                    statusText.setTextColor(warn)
                }
            }
        }
    }

    private fun renderAccounts(accounts: JSONArray) {
        accountContainer.removeAllViews()
        if (accounts.length() == 0) {
            accountContainer.addView(TextView(this).apply {
                text = "MuMu не найден. Проверь, что бот и эмуляторы запущены."
                setTextColor(muted)
                setPadding(0, dp(18), 0, 0)
            })
            return
        }

        for (i in 0 until accounts.length()) {
            val a = accounts.getJSONObject(i)
            accountContainer.addView(accountCard(a), LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply {
                bottomMargin = dp(10)
            })
        }
    }

    private fun accountCard(a: JSONObject): View {
        val device = a.optString("device")
        val name = a.optString("name", device)
        val paused = a.optBoolean("paused", false)
        val pauseRemaining = a.optInt("pause_remaining_seconds", 0)
        val foreground = a.optString("foreground_app", "-")
        val activeTask = a.optString("active_task", "")
        val resources = a.optJSONObject("resources") ?: JSONObject()

        val card = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(12), dp(10), dp(12), dp(10))
            setBackgroundColor(panel)
        }

        val titleRow = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
        }
        titleRow.addView(TextView(this).apply {
            text = name
            textSize = 18f
            setTextColor(text)
            setTypeface(typeface, android.graphics.Typeface.BOLD)
        }, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))
        titleRow.addView(TextView(this).apply {
            text = if (paused) "ПАУЗА ${formatSeconds(pauseRemaining)}" else "РАБОТАЕТ"
            setTextColor(if (paused) warn else ok)
            setTypeface(typeface, android.graphics.Typeface.BOLD)
        })
        card.addView(titleRow)

        card.addView(TextView(this).apply {
            text = buildString {
                append(device)
                append("\nПриложение: ")
                append(if (foreground.isBlank()) "-" else foreground)
                if (activeTask.isNotBlank()) {
                    append("\nСейчас: ")
                    append(activeTask)
                }
            }
            textSize = 12f
            setTextColor(muted)
            setPadding(0, dp(5), 0, dp(7))
        })

        val resourceLine = listOf(
            "Сила" to resources.optString("battle_power_text"),
            "Золото" to resources.optString("gold_text"),
            "Еда" to resources.optString("food_text"),
            "Дерево" to resources.optString("wood_text"),
            "Крист." to resources.optString("crystals_text"),
            "Алмазы" to resources.optString("diamonds_text"),
            "ОД" to resources.optString("od_text")
        ).filter { it.second.isNotBlank() && it.second != "null" }
            .joinToString("   ") { "${it.first}: ${it.second}" }

        if (resourceLine.isNotBlank()) {
            card.addView(TextView(this).apply {
                text = resourceLine
                textSize = 12f
                setTextColor(text)
                setPadding(0, 0, 0, dp(8))
            })
        }

        val row1 = buttonRow()
        row1.addView(actionButton("Пауза") { showPauseDialog(device, name) }, weight())
        row1.addView(actionButton("Возобновить") { postAction(device, "resume", JSONObject()) }, weight())
        row1.addView(actionButton("Скриншот") { screenshot(device, name) }, weight())
        card.addView(row1)

        val row2 = buttonRow()
        row2.addView(actionButton("Запустить игру") { postAction(device, "game/start", JSONObject()) }, weight())
        row2.addView(actionButton("Закрыть игру") { postAction(device, "game/stop", JSONObject()) }, weight())
        row2.addView(actionButton("История") { showHistory(device, name) }, weight())
        card.addView(row2, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT).apply { topMargin = dp(6) })

        return card
    }

    private fun showPauseDialog(device: String, name: String) {
        val input = EditText(this).apply {
            inputType = InputType.TYPE_CLASS_NUMBER
            hint = "Минуты"
            setText("30")
            setSelectAllOnFocus(true)
        }
        val presets = arrayOf("15", "25", "30", "45", "60", "90", "120", "180")
        AlertDialog.Builder(this)
            .setTitle("Пауза • $name")
            .setSingleChoiceItems(presets.map { "$it мин" }.toTypedArray(), 2) { dialog, which ->
                input.setText(presets[which])
                dialog.dismiss()
                sendPause(device, presets[which].toInt())
            }
            .setView(input)
            .setPositiveButton("Своё время") { _, _ ->
                val minutes = input.text.toString().toIntOrNull()
                if (minutes == null || minutes !in 1..1440) toast("Допустимо от 1 до 1440 минут")
                else sendPause(device, minutes)
            }
            .setNegativeButton("Отмена", null)
            .show()
    }

    private fun sendPause(device: String, minutes: Int) {
        postAction(device, "pause", JSONObject().put("minutes", minutes))
    }

    private fun postAction(device: String, action: String, body: JSONObject) {
        executor.execute {
            try {
                Api.postJson(baseUrl(), "/api/v1/accounts/${Api.enc(device)}/$action", body, token())
                runOnUiThread {
                    toast("Готово")
                    handler.postDelayed({ refreshAccounts(false) }, 500)
                }
            } catch (e: Exception) {
                runOnUiThread { toast("Ошибка: ${e.message}") }
            }
        }
    }

    private fun screenshot(device: String, name: String) {
        toast("Делаю скриншот…")
        executor.execute {
            try {
                val bytes = Api.getBytes(baseUrl(), "/api/v1/accounts/${Api.enc(device)}/screenshot", token())
                val bmp = BitmapFactory.decodeByteArray(bytes, 0, bytes.size) ?: error("Не удалось открыть PNG")
                runOnUiThread {
                    val image = ImageView(this).apply {
                        setImageBitmap(bmp)
                        adjustViewBounds = true
                        setBackgroundColor(Color.BLACK)
                    }
                    Dialog(this).apply {
                        setTitle(name)
                        setContentView(image)
                        window?.setLayout(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT)
                        show()
                        window?.setLayout(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT)
                    }
                }
            } catch (e: Exception) {
                runOnUiThread { toast("Скриншот: ${e.message}") }
            }
        }
    }

    private fun showHistory(device: String, name: String) {
        executor.execute {
            try {
                val json = Api.getJson(baseUrl(), "/api/v1/accounts/${Api.enc(device)}/history?limit=30", token())
                val arr = json.optJSONArray("history") ?: JSONArray()
                val sb = StringBuilder()
                for (i in 0 until arr.length()) {
                    val item = arr.getJSONObject(i)
                    sb.append("• ").append(item.optString("time_text", ""))
                        .append("  ").append(item.optString("action", item.optString("note", "-")))
                        .append("\n")
                }
                runOnUiThread {
                    AlertDialog.Builder(this)
                        .setTitle("История • $name")
                        .setMessage(if (sb.isEmpty()) "История пока пустая" else sb.toString())
                        .setPositiveButton("Закрыть", null)
                        .show()
                }
            } catch (e: Exception) {
                runOnUiThread { toast("История: ${e.message}") }
            }
        }
    }

    private fun autoRefreshTick() {
        if (!autoRefresh) return
        refreshAccounts(false)
        handler.postDelayed(::autoRefreshTick, 5000)
    }

    private fun baseUrl(): String = serverInput.text.toString().trim().trimEnd('/')
    private fun token(): String = prefs.getString("token", "") ?: ""
    private fun setBusy(busy: Boolean) { refreshButton.isEnabled = !busy }
    private fun toast(s: String) = Toast.makeText(this, s, Toast.LENGTH_SHORT).show()
    private fun dp(v: Int) = (v * resources.displayMetrics.density).toInt()
    private fun buttonRow() = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL }
    private fun weight() = LinearLayout.LayoutParams(0, dp(44), 1f).apply { marginEnd = dp(4) }
    private fun actionButton(label: String, onClick: () -> Unit) = Button(this).apply {
        text = label
        textSize = 11f
        isAllCaps = false
        setOnClickListener { onClick() }
    }

    private fun formatSeconds(sec: Int): String {
        if (sec <= 0) return "0м"
        val m = sec / 60
        val h = m / 60
        return if (h > 0) "${h}ч ${m % 60}м" else "${m}м"
    }
}

private object Api {
    private const val TIMEOUT = 8000

    fun enc(v: String): String = java.net.URLEncoder.encode(v, "UTF-8").replace("+", "%20")

    fun getJson(base: String, path: String, token: String?): JSONObject =
        JSONObject(String(request(base, path, "GET", null, token), Charsets.UTF_8))

    fun postJson(base: String, path: String, body: JSONObject, token: String?): JSONObject =
        JSONObject(String(request(base, path, "POST", body.toString().toByteArray(Charsets.UTF_8), token), Charsets.UTF_8))

    fun getBytes(base: String, path: String, token: String?): ByteArray =
        request(base, path, "GET", null, token)

    private fun request(base: String, path: String, method: String, payload: ByteArray?, token: String?): ByteArray {
        val conn = (URL(base + path).openConnection() as HttpURLConnection).apply {
            requestMethod = method
            connectTimeout = TIMEOUT
            readTimeout = 15000
            setRequestProperty("Accept", "*/*")
            if (!token.isNullOrBlank()) setRequestProperty("Authorization", "Bearer $token")
            if (payload != null) {
                doOutput = true
                setRequestProperty("Content-Type", "application/json; charset=utf-8")
            }
        }
        if (payload != null) conn.outputStream.use { it.write(payload) }
        val code = conn.responseCode
        val stream = if (code in 200..299) conn.inputStream else conn.errorStream
        val out = ByteArrayOutputStream()
        stream?.use { input -> input.copyTo(out) }
        val data = out.toByteArray()
        conn.disconnect()
        if (code !in 200..299) {
            val msg = try { JSONObject(String(data, Charsets.UTF_8)).optString("error") } catch (_: Exception) { String(data, Charsets.UTF_8) }
            throw IllegalStateException(if (msg.isBlank()) "HTTP $code" else msg)
        }
        return data
    }
}
