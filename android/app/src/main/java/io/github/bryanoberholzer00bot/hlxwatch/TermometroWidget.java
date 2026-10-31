package io.github.bryanoberholzer00bot.hlxwatch;

import android.app.PendingIntent;
import android.appwidget.AppWidgetManager;
import android.appwidget.AppWidgetProvider;
import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.graphics.Color;
import android.util.Log;
import android.widget.RemoteViews;

import org.json.JSONObject;

/**
 * Widget "Termometro HLX" per la schermata home. I dati li calcola il giro su GitHub (bot/termometro.json, vedi
 * hlxwatch/reports/termometro.py): indice 0-100, livello e colore, ultima traccia HLX, ultima voce. Il widget si
 * aggiorna con il controllo in background dell'app (ogni 30 minuti) e quando lo si aggiunge; nessun timer suo.
 * Un possibile annuncio arrivato all'app lo rende rosso subito, senza aspettare il sito (per 3 giorni).
 */
public class TermometroWidget extends AppWidgetProvider {

    private static final String TAG = "HLXWatch";
    static final String DATA_URL = MainActivity.HOME + "bot/termometro.json";
    private static final String CACHE = "termometro_json";
    private static final String ALARM_AT = "termometro_annuncio_at";
    private static final long ANNOUNCEMENT_MS = 3 * 24 * 3600 * 1000L;

    @Override
    public void onUpdate(Context context, AppWidgetManager manager, int[] ids) {
        render(context);                                   // subito con i dati salvati
        PendingResult pending = goAsync();
        new Thread(() -> {
            try {
                refresh(context);                          // poi con quelli freschi
            } catch (Exception e) {
                Log.w(TAG, "termometro non aggiornato: " + e);
            } finally {
                pending.finish();
            }
        }).start();
    }

    static boolean hasWidgets(Context c) {
        AppWidgetManager m = AppWidgetManager.getInstance(c);
        return m != null && m.getAppWidgetIds(new ComponentName(c, TermometroWidget.class)).length > 0;
    }

    /** Scarica bot/termometro.json, lo salva e ridisegna i widget (blocca: non chiamarla dalla UI). */
    static void refresh(Context c) throws Exception {
        String json = CheckJob.download(DATA_URL);
        new JSONObject(json);                              // controlla che sia valido prima di salvarlo
        Prefs.get(c).edit().putString(CACHE, json).apply();
        render(c);
    }

    /** Un possibile annuncio (non la prova) appena notificato: il widget diventa rosso subito. */
    static void markAnnouncement(Context c) {
        Prefs.get(c).edit().putLong(ALARM_AT, System.currentTimeMillis()).apply();
        render(c);
    }

    static void render(Context c) {
        AppWidgetManager m = AppWidgetManager.getInstance(c);
        if (m == null) return;
        int[] ids = m.getAppWidgetIds(new ComponentName(c, TermometroWidget.class));
        if (ids.length == 0) return;
        RemoteViews v = new RemoteViews(c.getPackageName(), R.layout.widget_termometro);
        JSONObject t;
        try {
            t = new JSONObject(Prefs.get(c).getString(CACHE, "{}"));
        } catch (Exception e) {
            t = new JSONObject();
        }
        boolean alarm = System.currentTimeMillis() - Prefs.get(c).getLong(ALARM_AT, 0) < ANNOUNCEMENT_MS;
        if (!t.has("indice") && !alarm) {
            v.setTextViewText(R.id.widget_index, "--");
            v.setTextViewText(R.id.widget_level, "In attesa dei dati");
            v.setTextViewText(R.id.widget_trace, "Si aggiorna al prossimo controllo (entro 30 minuti)");
            v.setTextViewText(R.id.widget_latest, "");
            v.setTextViewText(R.id.widget_updated, "");
            v.setInt(R.id.widget_bg, "setColorFilter", 0xFF3A4150);
        } else {
            int index = alarm ? 100 : t.optInt("indice");
            String level = alarm && !t.optBoolean("annuncio") ? "POSSIBILE ANNUNCIO" : t.optString("etichetta");
            int color = alarm ? 0xFFB71C1C : parseColor(t.optString("colore"), 0xFF3A4150);
            v.setTextViewText(R.id.widget_index, String.valueOf(index));
            v.setTextViewText(R.id.widget_level, level);
            v.setInt(R.id.widget_bg, "setColorFilter", color);
            JSONObject trace = t.optJSONObject("ultima_traccia");
            v.setTextViewText(R.id.widget_trace, trace == null ? "Nessuna traccia HLX forte in archivio"
                    : "Ultima traccia HLX: " + daysAgo(trace.optInt("giorni")) + " · " + trace.optString("gioco"));
            JSONObject latest = t.optJSONObject("ultima_voce");
            v.setTextViewText(R.id.widget_latest, latest == null ? ""
                    : "Ultima voce: " + latest.optString("gioco") + " · " + latest.optString("testo"));
            String at = t.optString("aggiornato");
            v.setTextViewText(R.id.widget_updated, at.length() >= 16
                    ? "dati del " + at.substring(8, 10) + "/" + at.substring(5, 7) + " alle " + at.substring(11, 16)
                    + " · tracce HLX in 30 giorni: " + t.optInt("tracce_30g") : "");
        }
        String url = alarm ? MainActivity.HOME + "search.html?min=200" : MainActivity.HOME;
        Intent open = new Intent(c, MainActivity.class)
                .setFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_SINGLE_TOP)
                .putExtra(MainActivity.EXTRA_URL, url);
        v.setOnClickPendingIntent(R.id.widget_root, PendingIntent.getActivity(c, 501, open,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE));
        m.updateAppWidget(ids, v);
    }

    static String daysAgo(int days) {
        if (days <= 0) return "oggi";
        if (days == 1) return "ieri";
        return days + " giorni fa";
    }

    private static int parseColor(String s, int fallback) {
        try {
            return Color.parseColor(s);
        } catch (RuntimeException e) {
            return fallback;
        }
    }
}
