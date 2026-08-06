package com.bmoorewiz.episodetracker

import android.os.Bundle
import android.util.Log
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import com.chaquo.python.PyObject
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import kotlin.concurrent.thread

/**
 * Prototype: prove that the unmodified CocoScrapers module runs on a Fire TV
 * stick with no Kodi installed, driven from ordinary Android code.
 *
 * It scrapes one episode and one movie and dumps the ranked results. There is
 * deliberately no browsing UI, no Trakt and no Real-Debrid here - those are
 * plain HTTP and carry no technical risk. Scraping was the open question.
 */
class MainActivity : AppCompatActivity() {

    private lateinit var output: TextView
    private val lines = StringBuilder()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)
        output = findViewById(R.id.output)

        if (!Python.isStarted()) Python.start(AndroidPlatform(this))

        thread { runScrape() }
    }

    private fun log(text: String) {
        Log.i(TAG, text)
        lines.append(text).append('\n')
        runOnUiThread { output.text = lines.toString() }
    }

    private fun runScrape() {
        try {
            val py = Python.getInstance()
            val scraper: PyObject = py.getModule("et_scrape")

            // Chaquopy unpacks bundled Python onto disk; that directory is
            // where the `cocoscrapers` package and the Kodi shims live.
            val pythonRoot = File(filesDir, "chaquopy/AssetFinder/app").absolutePath
            val dataDir = File(filesDir, "episodetracker").absolutePath

            log("configure()")
            log("  " + scraper.callAttr("configure", dataDir, pythonRoot).toString())

            val providers = JSONArray(scraper.callAttr("list_providers").toString())
            log("providers on disk: ${providers.length()}")

            // Enable every torrent provider for the test.
            val names = ArrayList<String>()
            for (i in 0 until providers.length()) {
                val p = providers.getJSONObject(i)
                if (p.getString("group") == "torrents") names.add(p.getString("name"))
            }
            scraper.callAttr("set_providers", names.joinToString(","))
            log("enabled ${names.size} torrent providers\n")

            log("Breaking Bad S01E01 ...")
            report(
                scraper.callAttr(
                    "scrape_episode", "tt0903747", 1, 1, "Breaking Bad", "Pilot", 2008
                ).toString()
            )

            log("\nInception (2010) ...")
            report(scraper.callAttr("scrape_movie", "tt1375666", "Inception", 2010).toString())
        } catch (t: Throwable) {
            Log.e(TAG, "scrape failed", t)
            log("\nFAILED: ${t.message}")
        }
    }

    private fun report(json: String) {
        val result = JSONObject(json)
        val sources = result.getJSONArray("sources")
        log("  ${result.getInt("providers")} providers ran, ${sources.length()} sources")
        result.optString("error").takeIf { it.isNotEmpty() }?.let { log("  error: $it") }
        for (i in 0 until minOf(8, sources.length())) {
            val s = sources.getJSONObject(i)
            log(
                "  %-6s %7.2fGB S:%-5d %-16s %s".format(
                    s.getString("quality"), s.getDouble("size"),
                    s.getInt("seeders"), s.getString("provider"),
                    s.getString("name").take(44)
                )
            )
        }
    }

    companion object {
        private const val TAG = "EpisodeTrackerProto"
    }
}
