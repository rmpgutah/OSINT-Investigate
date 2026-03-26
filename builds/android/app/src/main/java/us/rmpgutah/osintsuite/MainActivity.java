package us.rmpgutah.osintsuite;

import android.app.Activity;
import android.content.SharedPreferences;
import android.os.Bundle;
import android.view.View;
import android.webkit.WebChromeClient;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;

/**
 * OSINT Suite Android WebView wrapper.
 * Connects to the cloud-hosted web dashboard.
 * Users configure their server URL on first launch.
 */
public class MainActivity extends Activity {

    private static final String PREFS_NAME = "OSINTSuitePrefs";
    private static final String KEY_SERVER_URL = "server_url";
    private static final String DEFAULT_URL = "http://127.0.0.1:8000";

    private WebView webView;
    private LinearLayout setupLayout;
    private EditText urlInput;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        SharedPreferences prefs = getSharedPreferences(PREFS_NAME, MODE_PRIVATE);
        String serverUrl = prefs.getString(KEY_SERVER_URL, "");

        if (serverUrl.isEmpty()) {
            showSetup();
        } else {
            showWebView(serverUrl);
        }
    }

    private void showSetup() {
        setupLayout = new LinearLayout(this);
        setupLayout.setOrientation(LinearLayout.VERTICAL);
        setupLayout.setPadding(48, 120, 48, 48);

        android.widget.TextView title = new android.widget.TextView(this);
        title.setText("OSINT Investigation Suite");
        title.setTextSize(24);
        title.setPadding(0, 0, 0, 16);
        setupLayout.addView(title);

        android.widget.TextView label = new android.widget.TextView(this);
        label.setText("Enter your OSINT Suite server URL:");
        label.setPadding(0, 0, 0, 8);
        setupLayout.addView(label);

        urlInput = new EditText(this);
        urlInput.setHint("https://your-server.example.com");
        urlInput.setText(DEFAULT_URL);
        urlInput.setSingleLine(true);
        setupLayout.addView(urlInput);

        Button connectBtn = new Button(this);
        connectBtn.setText("Connect");
        connectBtn.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                String url = urlInput.getText().toString().trim();
                if (!url.isEmpty()) {
                    if (!url.startsWith("http")) {
                        url = "https://" + url;
                    }
                    SharedPreferences prefs = getSharedPreferences(PREFS_NAME, MODE_PRIVATE);
                    prefs.edit().putString(KEY_SERVER_URL, url).apply();
                    showWebView(url);
                }
            }
        });
        setupLayout.addView(connectBtn);

        setContentView(setupLayout);
    }

    private void showWebView(String url) {
        webView = new WebView(this);

        WebSettings settings = webView.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setLoadWithOverviewMode(true);
        settings.setUseWideViewPort(true);
        settings.setCacheMode(WebSettings.LOAD_DEFAULT);

        webView.setWebViewClient(new WebViewClient() {
            @Override
            public void onReceivedError(WebView view, int errorCode,
                                         String description, String failingUrl) {
                // Show connection error with retry
                view.loadData(
                    "<html><body style='font-family:sans-serif;padding:40px;text-align:center;background:#0f1117;color:#e4e6eb'>" +
                    "<h2>Connection Failed</h2>" +
                    "<p style='color:#9ca3b0'>Could not connect to: " + failingUrl + "</p>" +
                    "<p style='color:#9ca3b0'>" + description + "</p>" +
                    "<button onclick='window.location.reload()' style='padding:12px 24px;background:#3b82f6;color:#fff;border:none;border-radius:8px;font-size:16px;margin:16px'>Retry</button>" +
                    "<br><button onclick='window.location=\"osint://settings\"' style='padding:12px 24px;background:#21252f;color:#e4e6eb;border:1px solid #2d3343;border-radius:8px;font-size:16px;margin:8px'>Change Server</button>" +
                    "</body></html>",
                    "text/html", "UTF-8"
                );
            }
        });

        webView.setWebChromeClient(new WebChromeClient());
        webView.loadUrl(url);
        setContentView(webView);
    }

    @Override
    public void onBackPressed() {
        if (webView != null && webView.canGoBack()) {
            webView.goBack();
        } else {
            super.onBackPressed();
        }
    }
}
