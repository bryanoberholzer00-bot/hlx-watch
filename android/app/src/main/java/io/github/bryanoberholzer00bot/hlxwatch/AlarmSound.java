package io.github.bryanoberholzer00bot.hlxwatch;

import android.app.NotificationManager;
import android.content.Context;
import android.media.AudioAttributes;
import android.media.AudioManager;
import android.media.Ringtone;
import android.media.RingtoneManager;
import android.net.Uri;
import android.os.Build;
import android.service.notification.StatusBarNotification;
import android.util.Log;

/**
 * Il suono dell'allarme, suonato dall'app e non dal canale delle notifiche: molti telefoni (Xiaomi, Samsung...)
 * silenziano o cambiano il suono dei canali delle app installate fuori dal Play Store. Qui si usa la suoneria della
 * sveglia con l'uso "allarme" (che "Non disturbare" di solito lascia passare), ripetuta finché la notifica
 * dell'allarme esiste, al massimo MAX_MS. "Ho visto", "Apri" o chiudere la notifica la fermano.
 */
final class AlarmSound {

    private static final String TAG = "HLXWatch";
    static final long MAX_MS = 3 * 60 * 1000L;
    private static Ringtone current;
    private static volatile boolean stopRequested;

    private AlarmSound() {
    }

    static synchronized void stop() {
        stopRequested = true;
        if (current != null) {
            try {
                current.stop();
            } catch (RuntimeException ignored) {
                // già fermo
            }
            current = null;
        }
    }

    private static synchronized Ringtone start(Context c) {
        stop();
        stopRequested = false;
        Uri uri = CheckJob.alarmSound();
        Ringtone r = uri == null ? null : RingtoneManager.getRingtone(c.getApplicationContext(), uri);
        if (r == null) {
            Log.w(TAG, "nessuna suoneria disponibile per l'allarme");
            return null;
        }
        r.setAudioAttributes(new AudioAttributes.Builder()
                .setUsage(AudioAttributes.USAGE_ALARM)
                .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION).build());
        if (Build.VERSION.SDK_INT >= 28) r.setLooping(true);
        r.play();
        current = r;
        return r;
    }

    /**
     * Suona finché la notifica `id` è visibile (o fino a MAX_MS). Blocca il thread che la chiama: va chiamata dal
     * controllo in background (che così resta vivo mentre suona) o da un thread apposta.
     */
    static void ringWhileShown(Context c, int id) {
        Ringtone r = start(c);
        if (r == null) return;
        NotificationManager nm = (NotificationManager) c.getSystemService(Context.NOTIFICATION_SERVICE);
        long until = System.currentTimeMillis() + MAX_MS;
        try {
            while (!stopRequested && System.currentTimeMillis() < until && shown(nm, id)) {
                synchronized (AlarmSound.class) {
                    if (current != r) return;                  // un altro allarme l'ha sostituito
                    if (!r.isPlaying()) r.play();              // suonerie corte o senza ripetizione: si riparte
                }
                Thread.sleep(1000);
            }
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        } finally {
            synchronized (AlarmSound.class) {
                if (current == r) stop();
            }
        }
    }

    private static boolean shown(NotificationManager nm, int id) {
        if (nm == null) return true;
        try {
            for (StatusBarNotification n : nm.getActiveNotifications()) {
                if (n.getId() == id) return true;
            }
            return false;
        } catch (RuntimeException e) {
            return true;
        }
    }

    /** Volume della sveglia a zero: l'allarme non si sentirebbe. */
    static boolean alarmVolumeZero(Context c) {
        AudioManager am = (AudioManager) c.getSystemService(Context.AUDIO_SERVICE);
        return am != null && am.getStreamVolume(AudioManager.STREAM_ALARM) == 0;
    }
}
