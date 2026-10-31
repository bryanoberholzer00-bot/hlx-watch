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
import android.media.AudioAttributes;
import android.media.AudioManager;
import android.media.RingtoneManager;
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
    // canale separato per i possibili annunci: suona come una sveglia (uso "allarme", che "Non disturbare" di
    // solito lascia passare) e apre AlarmActivity a tutto schermo. Il suono di un canale non si cambia dopo averlo
    // creato: per cambiarlo serve un id nuovo
    static final String ALARM_CHANNEL = "annuncio_allarme";
    private static final String PREFS = "check";
    private static final String DATA_URL = MainActivity.HOME + "bot/ultime.json";
    // controllo veloce dell'annuncio nel bot su Cloudflare (ogni 10 minuti, telegram-welcome/src/fast.js):
    // un possibile annuncio arriva da qui anche se il giro su GitHub, che aggiorna il sito, è in ritardo di ore
    private static final String FAST_URL = "https://hlx-watch-bot.bryan-oberholzer00.workers.dev/fast/annuncio.json";

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
            NotificationManager nm = context.getSystemService(NotificationManager.class);
            NotificationChannel ch = new NotificationChannel(CHANNEL, "Nuove scoperte",
                    NotificationManager.IMPORTANCE_DEFAULT);
            ch.setDescription("Una notifica quando compare una novità rilevante (HLX, Half-Life 3, nuove build...)");
            nm.createNotificationChannel(ch);
            NotificationChannel alarm = new NotificationChannel(ALARM_CHANNEL, "Allarme annuncio",
                    NotificationManager.IMPORTANCE_HIGH);
            alarm.setDescription("Suona come una sveglia quando Valve potrebbe aver annunciato un Half-Life nuovo "
                    + "(e per la prova generale)");
            alarm.setSound(alarmSound(), new AudioAttributes.Builder()
                    .setUsage(AudioAttributes.USAGE_ALARM)
                    .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION).build());
            alarm.enableVibration(true);
            alarm.setVibrationPattern(new long[]{0, 900, 400, 900, 400, 900});
            alarm.setLockscreenVisibility(Notification.VISIBILITY_PUBLIC);
            alarm.setBypassDnd(true);       // vale solo se l'utente lo consente nelle impostazioni del canale
            nm.createNotificationChannel(alarm);
        }
        Updates.createChannel(context);
    }

    static Uri alarmSound() {
        Uri u = RingtoneManager.getDefaultUri(RingtoneManager.TYPE_ALARM);
        if (u == null) u = RingtoneManager.getDefaultUri(RingtoneManager.TYPE_RINGTONE);
        if (u == null) u = RingtoneManager.getDefaultUri(RingtoneManager.TYPE_NOTIFICATION);
        return u;
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
        try {
            Updates.check(context, false);          // nuova versione dell'app? (al massimo ogni 6 ore)
        } catch (Exception e) {
            Log.w(TAG, "controllo degli aggiornamenti fallito: " + e);
        }
        JSONArray items = new JSONArray(download(DATA_URL));
        try {
            JSONArray fast = new JSONArray(download(FAST_URL));
            for (int i = 0; i < fast.length(); i++) items.put(fast.getJSONObject(i));
        } catch (Exception e) {
            Log.w(TAG, "controllo veloce non raggiungibile: " + e);    // il sito basta per tutto il resto
        }
        SharedPreferences prefs = context.getSharedPreferences(PREFS, MODE_PRIVATE);
        boolean firstRun = !prefs.contains("seen");
        Set<String> seen = new HashSet<>(prefs.getStringSet("seen", new HashSet<>()));
        Set<String> current = new HashSet<>();
        List<JSONObject> fresh = new ArrayList<>();
        long now = System.currentTimeMillis();
        for (int i = 0; i < items.length(); i++) {
            JSONObject it = items.getJSONObject(i);
            String key = keyOf(it);
            current.add(key);
            // le voci escluse dalle impostazioni si segnano come viste ma non si notificano
            if (!seen.contains(key) && now - parseUtc(it.optString("c")) < MAX_AGE_MS && Prefs.wanted(context, it)) {
                fresh.add(it);
            }
        }
        createChannel(context);
        // conferma una sola volta, ma solo quando le notifiche sono davvero permesse: il primo controllo
        // può partire prima che l'utente risponda alla richiesta del permesso (la conferma arriva anche di notte)
        if (!prefs.getBoolean("welcomed", false) && notificationsAllowed(context)) {
            show(context, 1, "HLX Watch: notifiche attive",
                    "Riceverai una notifica quando compare una novità rilevante (di notte le trovi riassunte al "
                            + "mattino; un possibile annuncio suona subito, come una sveglia). Soglia, orari e tipi "
                            + "di voce si scelgono con l'ingranaggio in basso a destra nell'app.", MainActivity.HOME);
            prefs.edit().putBoolean("welcomed", true).apply();
        }
        // orario di silenzio: le novità della notte non si segnano come viste, così alla prima
        // verifica dopo le 8 arrivano tutte insieme in una sola notifica. Fa eccezione un possibile annuncio
        // di Valve (campo "a"): arriva subito, e solo quello viene segnato come visto
        if (!firstRun && inQuietHours(context)) {
            List<JSONObject> urgent = new ArrayList<>();
            for (JSONObject it : fresh) {
                if (it.optInt("a") == 1) urgent.add(it);
            }
            if (urgent.isEmpty()) {
                return;
            }
            Set<String> keepQuiet = new HashSet<>(seen);
            for (JSONObject it : urgent) keepQuiet.add(keyOf(it));
            prefs.edit().putStringSet("seen", keepQuiet).apply();
            fresh = urgent;
        } else {
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
        boolean announcement = false;
        boolean drillOnly = true;           // prova generale (campo "p"): stesso percorso, ma il titolo lo dice
        for (JSONObject it : fresh) {
            if (it.optInt("a") == 1) {
                announcement = true;
                if (it.optInt("p") != 1) drillOnly = false;
            }
        }
        if (announcement && drillOnly) {
            title = "HLX Watch: PROVA GENERALE dell'annuncio";
            url = MainActivity.HOME + "metodologia.html#prova";
        } else if (announcement) {
            title = "HLX Watch: POSSIBILE ANNUNCIO di Valve";
            url = MainActivity.HOME + "search.html?min=200";
        } else if (fresh.size() == 1) {
            title = "HLX Watch: novità in " + top.optString("g");
            String q = top.optString("k").isEmpty() ? top.optString("x") : top.optString("k");
            url = MainActivity.HOME + "search.html?q=" + Uri.encode(q.length() > 60 ? q.substring(0, 60) : q);
        } else {
            title = "HLX Watch: " + fresh.size() + " novità (max " + top.optInt("s") + ")";
            url = MainActivity.HOME + "search.html?new=1&min=50";
        }
        if (announcement && Prefs.alarmOn(context)) {
            showAlarm(context, (int) (now / 1000), title, body.toString().trim(), url);
        } else {
            show(context, (int) (now / 1000), title, body.toString().trim(), url);
        }
    }

    private static String keyOf(JSONObject it) {
        return it.optString("g") + "|" + it.optString("t") + "|" + it.optString("k") + "|" + it.optString("x");
    }

    static final int QUIET_START = 23;      // predefinito: dalle 23...
    static final int QUIET_END = 8;         // ...alle 8, ora del telefono (si cambia nelle Impostazioni)

    static boolean inQuietHours(Context context) {
        int h = java.util.Calendar.getInstance().get(java.util.Calendar.HOUR_OF_DAY);
        return Prefs.inQuietHours(context, h);
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

    /**
     * Possibile annuncio: notifica sul canale dell'allarme, che suona finché non la si chiude (FLAG_INSISTENT), e
     * schermata a tutto schermo anche a telefono bloccato. Se Android non concede lo schermo intero (Android 14+, dalle
     * impostazioni) resta comunque la notifica che suona.
     */
    @SuppressWarnings("deprecation")
    static void showAlarm(Context context, int id, String title, String text, String url) {
        NotificationManager nm = (NotificationManager) context.getSystemService(Context.NOTIFICATION_SERVICE);
        if (nm == null || (Build.VERSION.SDK_INT >= 24 && !nm.areNotificationsEnabled())) {
            return;
        }
        createChannel(context);
        Intent full = new Intent(context, AlarmActivity.class)
                .setFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_NO_USER_ACTION)
                .putExtra(AlarmActivity.EXTRA_TITLE, title)
                .putExtra(AlarmActivity.EXTRA_TEXT, text)
                .putExtra(AlarmActivity.EXTRA_ID, id)
                .putExtra(MainActivity.EXTRA_URL, url);
        PendingIntent pi = PendingIntent.getActivity(context, id, full,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
        Notification.Builder b = Build.VERSION.SDK_INT >= 26
                ? new Notification.Builder(context, ALARM_CHANNEL) : new Notification.Builder(context);
        b.setSmallIcon(R.drawable.ic_stat_hlx)
                .setColor(0xFFD32F2F)
                .setContentTitle(title)
                .setContentText(text)
                .setStyle(new Notification.BigTextStyle().bigText(text))
                .setCategory(Notification.CATEGORY_ALARM)
                .setVisibility(Notification.VISIBILITY_PUBLIC)
                .setContentIntent(pi)
                .setFullScreenIntent(pi, true)
                .setAutoCancel(true);
        if (Build.VERSION.SDK_INT < 26) {
            b.setPriority(Notification.PRIORITY_MAX)
                    .setSound(alarmSound(), AudioManager.STREAM_ALARM)
                    .setVibrate(new long[]{0, 900, 400, 900, 400, 900});
        }
        Notification n = b.build();
        n.flags |= Notification.FLAG_INSISTENT;        // il suono si ripete finché la notifica non si chiude
        nm.notify(id, n);
    }

    static String download(String address) throws Exception {
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
