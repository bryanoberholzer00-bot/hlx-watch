package io.github.bryanoberholzer00bot.hlxwatch;

import android.app.Activity;
import android.content.ActivityNotFoundException;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.graphics.Bitmap;
import android.graphics.drawable.GradientDrawable;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.view.View;
import android.view.ViewGroup;
import android.view.WindowInsets;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.view.Gravity;
import android.widget.FrameLayout;
import android.widget.ImageButton;
import android.widget.ProgressBar;

import androidx.swiperefreshlayout.widget.SwipeRefreshLayout;

/**
 * HLX Watch per Android: il sito pubblicato su GitHub Pages in una WebView a schermo intero.
 * Il contenuto resta quello del sito, quindi l'app non va reinstallata quando i dati cambiano.
 * Le pagine del sito si aprono nell'app; GitHub, Steam e gli altri link esterni nel browser.
 * Le notifiche di sistema arrivano da CheckJob, che controlla le novità in background.
 */
public class MainActivity extends Activity {

    static final String HOME = "https://bryanoberholzer00-bot.github.io/hlx-watch/";
    static final String EXTRA_URL = "url";
    private static final int ASK_NOTIFICATIONS = 7;
    private static final String HOST = "bryanoberholzer00-bot.github.io";
    private static final String PATH = "/hlx-watch";

    private WebView web;
    private SwipeRefreshLayout swipe;
    private ProgressBar progress;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        web = new WebView(this);
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);          // la ricerca del sito filtra i dati nel browser
        s.setDomStorageEnabled(true);
        s.setCacheMode(WebSettings.LOAD_DEFAULT);
        s.setLoadWithOverviewMode(true);
        s.setUseWideViewPort(true);
        s.setBuiltInZoomControls(true);
        s.setDisplayZoomControls(false);

        web.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                Uri uri = request.getUrl();
                if (HOST.equals(uri.getHost()) && uri.getPath() != null && uri.getPath().startsWith(PATH)) {
                    return false;                 // pagina del sito: resta nell'app
                }
                openExternal(uri);                // commit su GitHub, Steam, CSV...: nel browser
                return true;
            }

            @Override
            public void onPageStarted(WebView view, String url, Bitmap favicon) {
                progress.setVisibility(View.VISIBLE);
            }

            @Override
            public void onPageFinished(WebView view, String url) {
                progress.setVisibility(View.GONE);
                swipe.setRefreshing(false);
            }

            @Override
            public void onReceivedError(WebView view, WebResourceRequest request, WebResourceError error) {
                if (request.isForMainFrame()) {
                    swipe.setRefreshing(false);
                    view.loadDataWithBaseURL(HOME, offlinePage(), "text/html", "utf-8", null);
                }
            }
        });
        web.setWebChromeClient(new WebChromeClient() {
            @Override
            public void onProgressChanged(WebView view, int newProgress) {
                progress.setProgress(newProgress);
            }
        });

        swipe = new SwipeRefreshLayout(this);
        swipe.setColorSchemeColors(0xFFE8743B);
        swipe.addView(web, new ViewGroup.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT));
        // trascinare verso il basso aggiorna solo quando la pagina è già in cima
        swipe.setOnChildScrollUpCallback((parent, child) -> web.getScrollY() > 0);
        swipe.setOnRefreshListener(() -> web.reload());

        progress = new ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal);
        progress.setMax(100);
        FrameLayout.LayoutParams barParams = new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, (int) (4 * getResources().getDisplayMetrics().density));

        FrameLayout root = new FrameLayout(this);
        root.setBackgroundColor(0xFF1F2430);       // il colore dell'intestazione del sito dietro le barre di sistema
        root.addView(swipe, new FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT));
        root.addView(progress, barParams);
        root.addView(settingsButton(), settingsParams());
        setContentView(root);
        fitSystemBars(this, root);

        String target = siteUrl(getIntent());
        if (target != null) {
            web.loadUrl(target);                   // aperta da una notifica: si va subito alla voce
        } else if (savedInstanceState != null) {
            web.restoreState(savedInstanceState);
        } else {
            web.loadUrl(HOME);
        }
        setUpNotifications();
    }

    /** Pianifica il controllo in background e, da Android 13, chiede il permesso per le notifiche (una volta). */
    private void setUpNotifications() {
        CheckJob.createChannel(this);
        CheckJob.schedule(this);
        if (Build.VERSION.SDK_INT >= 33
                && checkSelfPermission("android.permission.POST_NOTIFICATIONS") != PackageManager.PERMISSION_GRANTED) {
            android.content.SharedPreferences prefs = getSharedPreferences("app", MODE_PRIVATE);
            if (!prefs.getBoolean("asked_notifications", false)) {
                prefs.edit().putBoolean("asked_notifications", true).apply();
                requestPermissions(new String[]{"android.permission.POST_NOTIFICATIONS"}, ASK_NOTIFICATIONS);
            }
            return;
        }
        checkNow();
    }

    @Override
    public void onRequestPermissionsResult(int requestCode, String[] permissions, int[] results) {
        super.onRequestPermissionsResult(requestCode, permissions, results);
        if (requestCode == ASK_NOTIFICATIONS && results.length > 0 && results[0] == PackageManager.PERMISSION_GRANTED) {
            checkNow();
        }
    }

    /** Un controllo subito, senza aspettare il primo giro programmato (al primo avvio manda la conferma). */
    private void checkNow() {
        new Thread(() -> {
            try {
                CheckJob.check(getApplicationContext());
            } catch (Exception ignored) {
                // senza rete: ci pensa il controllo programmato
            }
        }).start();
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        String target = siteUrl(intent);
        if (target != null) {
            web.loadUrl(target);
        }
    }

    /** L'indirizzo passato da una notifica, solo se è una pagina del sito. */
    private static String siteUrl(Intent intent) {
        String url = intent == null ? null : intent.getStringExtra(EXTRA_URL);
        return url != null && url.startsWith(HOME) ? url : null;
    }

    /**
     * Da Android 15 le app vengono disegnate da bordo a bordo, anche sotto la barra di stato e quella di
     * navigazione. Lo chiediamo esplicitamente su tutte le versioni (così il comportamento è uguale ovunque)
     * e lasciamo come margine lo spazio occupato dalle barre e dall'eventuale foro della fotocamera.
     */
    @SuppressWarnings("deprecation")
    static void fitSystemBars(Activity activity, View root) {
        if (Build.VERSION.SDK_INT >= 30) {
            activity.getWindow().setDecorFitsSystemWindows(false);
        } else {
            root.setSystemUiVisibility(View.SYSTEM_UI_FLAG_LAYOUT_STABLE
                    | View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN | View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION);
        }
        activity.getWindow().setStatusBarColor(0x00000000);
        activity.getWindow().setNavigationBarColor(0x00000000);
        root.setOnApplyWindowInsetsListener((v, insets) -> {
            int left, top, right, bottom;
            if (Build.VERSION.SDK_INT >= 30) {
                android.graphics.Insets bars = insets.getInsets(
                        WindowInsets.Type.systemBars() | WindowInsets.Type.displayCutout());
                left = bars.left; top = bars.top; right = bars.right; bottom = bars.bottom;
            } else {
                left = insets.getSystemWindowInsetLeft(); top = insets.getSystemWindowInsetTop();
                right = insets.getSystemWindowInsetRight(); bottom = insets.getSystemWindowInsetBottom();
            }
            v.setPadding(left, top, right, bottom);
            return insets;
        });
        root.requestApplyInsets();
    }

    /** Ingranaggio in basso a destra: apre le Impostazioni (soglia, orari, tipi di voce, allarme, aggiornamenti). */
    private View settingsButton() {
        ImageButton b = new ImageButton(this);
        b.setImageResource(android.R.drawable.ic_menu_preferences);
        b.setColorFilter(0xFFFFFFFF);
        GradientDrawable bg = new GradientDrawable();
        bg.setShape(GradientDrawable.OVAL);
        bg.setColor(0xCC1F2430);
        b.setBackground(bg);
        b.setContentDescription("Impostazioni");
        b.setOnClickListener(v -> startActivity(new Intent(this, SettingsActivity.class)));
        return b;
    }

    private FrameLayout.LayoutParams settingsParams() {
        float dp = getResources().getDisplayMetrics().density;
        FrameLayout.LayoutParams lp = new FrameLayout.LayoutParams((int) (44 * dp), (int) (44 * dp),
                Gravity.BOTTOM | Gravity.END);
        lp.setMargins(0, 0, (int) (14 * dp), (int) (14 * dp));
        return lp;
    }

    private void openExternal(Uri uri) {
        try {
            startActivity(new Intent(Intent.ACTION_VIEW, uri));
        } catch (ActivityNotFoundException ignored) {
            // nessuna app per aprire il link: non succede nulla
        }
    }

    private static String offlinePage() {
        return "<!doctype html><html lang=it><head><meta name=viewport content='width=device-width,initial-scale=1'>"
                + "<style>body{font-family:sans-serif;background:#15181e;color:#e5e7eb;display:flex;"
                + "flex-direction:column;align-items:center;justify-content:center;height:90vh;text-align:center;padding:1rem}"
                + "a{background:#e8743b;color:#fff;padding:.7rem 1.4rem;border-radius:6px;text-decoration:none}</style></head>"
                + "<body><h2>Nessuna connessione</h2><p>HLX Watch ha bisogno di internet per mostrare i dati aggiornati.</p>"
                + "<p><a href='" + HOME + "'>Riprova</a></p></body></html>";
    }

    @Override
    protected void onSaveInstanceState(Bundle outState) {
        super.onSaveInstanceState(outState);
        web.saveState(outState);
    }

    @Override
    @SuppressWarnings("deprecation")
    public void onBackPressed() {
        if (web.canGoBack()) {
            web.goBack();
        } else {
            super.onBackPressed();
        }
    }
}
