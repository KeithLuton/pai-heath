package com.pailuton.health.app

import android.os.Bundle
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import com.pailuton.health.R

/**
 * Activity displayed when the user clicks the Privacy Policy / Rationale link
 * in Health Connect settings. Required by Google Play policies.
 */
class PermissionsRationaleActivity : AppCompatActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val body = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(48, 64, 48, 48)
        }
        val title = TextView(this).apply {
            text = "PAi Health — Privacy & Health Connect"
            textSize = 22f
            setTypeface(typeface, android.graphics.Typeface.BOLD)
            setPadding(0, 0, 0, 24)
        }
        val rationale = TextView(this).apply {
            text = getString(R.string.privacy_rationale)
            textSize = 15f
            setLineSpacing(0f, 1.25f)
        }
        body.addView(title)
        body.addView(rationale)
        setContentView(ScrollView(this).apply { addView(body) })
    }
}
