package io.github.bryanoberholzer00bot.hlxwatch;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.job.JobInfo;
import android.app.job.JobParameters;
import android.app.job.JobScheduler;
import android.app.job.JobService;
import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.net.Uri;
import android.os.Build;
import android.util.Log;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Locale;
import java.util.Set;
import java.util.TimeZone;

/**
 * Controllo in background: circa ogni 30 minuti legge bot/ultime.json dal sito (le ultime scoperte
 * rilevanti, già filtrate dal rumore) e mostra una notifica di sistema per quelle mai viste.
 * Non serve nessun server né Firebase: l'app interroga lo stesso file pubblico che usa il bot Telegram.
 * Al primo controllo registra le voci presenti senza notificarle e manda solo un messaggio di conferma.
 */
public class CheckJob extends JobService {

    private static final String TAG = "HLXWatch";
    private static final int JOB_ID = 1;
    private static final long PERIOD_MS = 30 * 60 * 1000L;
    private static final long MAX_AGE_MS = 3 * 24 * 3600 * 1000L;   // voci più vecchie: mai notificate
    static final String CHANNEL = "novita";
    private static final String PREFS = "check";
    private static final String DATA_URL = MainActivity.HOME + "bot/ultime.json";

    /** Pianifica il controllo periodico (se c'è già, non lo tocca). Sopravvive ai riavvii del telefono. */
    static void schedule(Context context) {
        JobScheduler js = (JobScheduler) context.getSystemService(Context.JOB_SCHEDULER_SERVICE);
        if (js == null || js.getPendingJob(JOB_ID) != null) {
            return;
        }
        JobInfo job = new JobInfo.Builder(JOB_ID, new ComponentName(context, CheckJob.class))
                .setPeriodic(PERIOD_MS)
                .setRequiredNetworkType(JobInfo.NETWORK_TYPE_ANY)
                .setPersisted(true)
                .build();
        try {
            js.schedule(job);
        } catch (RuntimeException e) {
            // se il sistema rifiuta la pianificazione l'app deve comunque aprirsi: niente controllo in background
            Log.w(TAG, "controllo in background non pianificato: " + e);
        }
    }

    static void createChannel(Context context) {
        if (Build.VERSION.SDK_INT >= 26) {
            NotificationChannel ch = new NotificationChannel(CHANNEL, "Nuove scoperte",
                    NotificationManager.IMPORTANCE_DEFAULT);
            ch.setDescription("Una notifica quando compare una novità rilevante (HLX, Half-Life 3, nuove build...)");
            context.getSystemService(NotificationManager.class).createNotificationChannel(ch);
        }
    }

    @Override
    public boolean onStartJob(JobParameters params) {
        new Thread(() -> {
            try {
                check(this);
            } catch (Exception e) {
                Log.w(TAG, "controllo fallito: " + e);
            }
            jobFinished(params, false);
        }).start();
        return true;
    }

    @Override
    public boolean onStopJob(JobParameters params) {
        return true;    // interrotto dal sistema: riprova più tardi
    }

    static void check(Context context) throws Exception {
        JSONArray items = new JSONArray(download(DATA_URL));
        SharedPreferences prefs = context.getSharedPreferences(PREFS, MODE_PRIVATE);
        boolean firstRun = !prefs.contains("seen");
        Set<String> seen = new HashSet<>(prefs.getStringSet("seen", new HashSet<>()));
        Set<String> current = new HashSet<>();
        List<JSONObject> fresh = new ArrayList<>();
        long now = System.currentTimeMillis();
        for (int i = 0; i < items.length(); i++) {
            JSONObject it = items.getJSONObject(i);
            String key = it.optString("g") + "|" + it.optString("t") + "|" + it.optString("k") + "|" + it.optString("x");
            current.add(key);
            if (!seen.contains(key) && now - parseUtc(it.optString("c")) < MAX_AGE_MS) {
                fresh.add(it);
            }
        }
        createChannel(context);
        // conferma una sola volta, ma solo quando le notifiche sono davvero permesse: il primo controllo
        // può partire prima che l'utente risponda alla richiesta del permesso (la conferma arriva anche di notte)
        if (!prefs.getBoolean("welcomed", false) && notificationsAllowed(context)) {
            show(context, 1, "HLX Watch: notifiche attive",
                    "Riceverai una notifica quando compare una novità rilevante (di notte, dalle 23 alle 8, "
                            + "le trovi riassunte al mattino). Si disattivano dalle impostazioni di Android, "
                            + "alla voce notifiche dell'app.", MainActivity.HOME);
            prefs.edit().putBoolean("welcomed", true).apply();
        }
        // orario di silenzio: le novità della notte non si segnano come viste, così alla prima
        // verifica dopo le 8 arrivano tutte insieme in una sola notifica
        if (!firstRun && inQuietHours()) {
            return;
        }

        // si ricordano le voci attuali più quelle già viste (con un limite, per non crescere all'infinito)
        Set<String> keep = new HashSet<>(current);
        for (String s : seen) {
            if (keep.size() >= 200) break;
            keep.add(s);
        }
        prefs.edit().putStringSet("seen", keep).apply();

        if (firstRun) {
            return;                 // la prima volta si registrano solo le voci presenti, senza notificarle
        }
        if (fresh.isEmpty()) {
            return;
        }
        JSONObject top = fresh.get(0);
        for (JSONObject it : fresh) {
            if (it.optInt("s") > top.optInt("s")) top = it;
        }
        StringBuilder body = new StringBuilder();
        int shown = 0;
        for (JSONObject it : fresh) {
            if (shown == 4) {
                body.append("… e altre ").append(fresh.size() - 4);
                break;
            }
            int s = it.optInt("s");
            String label = it.optString("k").isEmpty() ? it.optString("x") : it.optString("k");
            if (label.length() > 70) label = label.substring(0, 70);
            body.append(s >= 100 ? "🔴 " : "🟠 ").append("[").append(s).append("] ")
                    .append(it.optString("g")).append(": ").append(label).append("\n");
            shown++;
        }
        String title;
        String url;
        if (fresh.size() == 1) {
            title = "HLX Watch: novità in " + top.optString("g");
            String q = top.optString("k").isEmpty() ? top.optString("x") : top.optString("k");
            url = MainActivity.HOME + "search.html?q=" + Uri.encode(q.length() > 60 ? q.substring(0, 60) : q);
        } else {
            title = "HLX Watch: " + fresh.size() + " novità (max " + top.optInt("s") + ")";
            url = MainActivity.HOME + "search.html?new=1&min=50";
        }
        show(context, (int) (now / 1000), title, body.toString().trim(), url);
    }

    static final int QUIET_START = 23;      // dalle 23...
    static final int QUIET_END = 8;         // ...alle 8, ora del telefono

    static boolean inQuietHours() {
        int h = java.util.Calendar.getInstance().get(java.util.Calendar.HOUR_OF_DAY);
        return h >= QUIET_START || h < QUIET_END;
    }

    private static boolean notificationsAllowed(Context context) {
        NotificationManager nm = (NotificationManager) context.getSystemService(Context.NOTIFICATION_SERVICE);
        return nm != null && nm.areNotificationsEnabled();
    }

    @SuppressWarnings("deprecation")
    private static void show(Context context, int id, String title, String text, String url) {
        NotificationManager nm = (NotificationManager) context.getSystemService(Context.NOTIFICATION_SERVICE);
        if (nm == null || (Build.VERSION.SDK_INT >= 24 && !nm.areNotificationsEnabled())) {
            return;
        }
        Intent open = new Intent(context, MainActivity.class)
                .setFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_SINGLE_TOP)
                .putExtra(MainActivity.EXTRA_URL, url);
        PendingIntent pi = PendingIntent.getActivity(context, id, open,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
        Notification.Builder b = Build.VERSION.SDK_INT >= 26
                ? new Notification.Builder(context, CHANNEL) : new Notification.Builder(context);
        b.setSmallIcon(R.drawable.ic_stat_hlx)
                .setColor(0xFFE8743B)
                .setContentTitle(title)
                .setContentText(text)
                .setStyle(new Notification.BigTextStyle().bigText(text))
                .setContentIntent(pi)
                .setAutoCancel(true);
        nm.notify(id, b.build());
    }

    private static String download(String address) throws Exception {
        HttpURLConnection c = (HttpURLConnection) new URL(address + "?t=" + System.currentTimeMillis()).openConnection();
        c.setConnectTimeout(15000);
        c.setReadTimeout(20000);
        c.setUseCaches(false);
        c.setRequestProperty("Cache-Control", "no-cache");
        try (InputStream in = c.getInputStream()) {
            ByteArrayOutputStream out = new ByteArrayOutputStream();
            byte[] buf = new byte[8192];
            int n;
            while ((n = in.read(buf)) > 0) out.write(buf, 0, n);
            return new String(out.toByteArray(), StandardCharsets.UTF_8);
        } finally {
            c.disconnect();
        }
    }

    /** "2026-10-24T18:05:00Z" (o con +00:00) in millisecondi; 0 se manca o non si legge. */
    private static long parseUtc(String text) {
        if (text == null || text.length() < 19) return 0;
        try {
            SimpleDateFormat f = new SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss", Locale.ROOT);
            f.setTimeZone(TimeZone.getTimeZone("UTC"));
            return f.parse(text.substring(0, 19)).getTime();
        } catch (Exception e) {
            return 0;
        }
    }
}
