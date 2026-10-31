package io.github.bryanoberholzer00bot.hlxwatch;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageInfo;
import android.net.Uri;
import android.os.Build;

import org.json.JSONObject;

import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Avviso di nuova versione: ogni 6 ore (dentro il controllo in background) si legge la release android-latest su
 * GitHub. Il suo titolo è "HLX Watch per Android (build N)", e N è anche il versionCode dell'APK (vedi app/build.gradle):
 * se N è più alto di quello installato arriva una notifica, una sola per versione, con il link per scaricarla.
 * Android non permette a un'app di aggiornarsi da sola: resta un tocco per installare.
 */
final class Updates {

    static final String APK_URL =
            "https://github.com/bryanoberholzer00-bot/hlx-watch/releases/download/android-latest/hlx-watch.apk";
    private static final String API =
            "https://api.github.com/repos/bryanoberholzer00-bot/hlx-watch/releases/tags/android-latest";
    static final String CHANNEL = "aggiornamenti";
    private static final long EVERY_MS = 6 * 3600 * 1000L;
    private static final Pattern BUILD = Pattern.compile("build (\\d+)");

    private Updates() {
    }

    @SuppressWarnings("deprecation")
    static long installed(Context c) {
        try {
            PackageInfo p = c.getPackageManager().getPackageInfo(c.getPackageName(), 0);
            return Build.VERSION.SDK_INT >= 28 ? p.getLongVersionCode() : p.versionCode;
        } catch (Exception e) {
            return 0;
        }
    }

    /** L'ultima build pubblicata letta all'ultimo controllo (0 se mai letta). */
    static long latestKnown(Context c) {
        return Prefs.get(c).getLong("update_latest", 0);
    }

    /** Legge la release su GitHub (al massimo ogni 6 ore, salvo force) e notifica una versione nuova. */
    static long check(Context c, boolean force) throws Exception {
        SharedPreferences p = Prefs.get(c);
        long now = System.currentTimeMillis();
        if (!force && now - p.getLong("update_checked", 0) < EVERY_MS) {
            return latestKnown(c);
        }
        JSONObject release = new JSONObject(CheckJob.download(API));
        Matcher m = BUILD.matcher(release.optString("name"));
        long latest = m.find() ? Long.parseLong(m.group(1)) : 0;
        p.edit().putLong("update_checked", now).putLong("update_latest", latest).apply();
        if (latest > installed(c) && latest != p.getLong("update_notified", 0)) {
            notifyUpdate(c, latest);
            p.edit().putLong("update_notified", latest).apply();
        }
        return latest;
    }

    static void createChannel(Context c) {
        if (Build.VERSION.SDK_INT >= 26) {
            NotificationChannel ch = new NotificationChannel(CHANNEL, "Aggiornamenti dell'app",
                    NotificationManager.IMPORTANCE_LOW);
            ch.setDescription("Quando su GitHub c'è una versione più recente di HLX Watch");
            c.getSystemService(NotificationManager.class).createNotificationChannel(ch);
        }
    }

    static Intent downloadIntent() {
        return new Intent(Intent.ACTION_VIEW, Uri.parse(APK_URL)).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
    }

    @SuppressWarnings("deprecation")
    private static void notifyUpdate(Context c, long latest) {
        NotificationManager nm = (NotificationManager) c.getSystemService(Context.NOTIFICATION_SERVICE);
        if (nm == null || (Build.VERSION.SDK_INT >= 24 && !nm.areNotificationsEnabled())) {
            return;
        }
        createChannel(c);
        PendingIntent pi = PendingIntent.getActivity(c, 900, downloadIntent(),
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
        String text = "È disponibile la build " + latest + " (hai la " + installed(c) + "). Tocca per scaricarla, "
                + "poi aprila e installala sopra quella attuale: le impostazioni restano.";
        Notification.Builder b = Build.VERSION.SDK_INT >= 26
                ? new Notification.Builder(c, CHANNEL) : new Notification.Builder(c);
        b.setSmallIcon(R.drawable.ic_stat_hlx)
                .setColor(0xFFE8743B)
                .setContentTitle("HLX Watch: nuova versione dell'app")
                .setContentText(text)
                .setStyle(new Notification.BigTextStyle().bigText(text))
                .setContentIntent(pi)
                .setAutoCancel(true);
        nm.notify(900, b.build());
    }
}
