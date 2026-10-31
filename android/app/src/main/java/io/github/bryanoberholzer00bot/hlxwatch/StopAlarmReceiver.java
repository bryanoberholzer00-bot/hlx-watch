package io.github.bryanoberholzer00bot.hlxwatch;

import android.app.NotificationManager;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;

/** Pulsante "Ho visto" nella notifica dell'allarme: ferma il suono e chiude la notifica. */
public class StopAlarmReceiver extends BroadcastReceiver {

    @Override
    public void onReceive(Context context, Intent intent) {
        AlarmSound.stop();
        int id = intent.getIntExtra(AlarmActivity.EXTRA_ID, 0);
        NotificationManager nm = (NotificationManager) context.getSystemService(Context.NOTIFICATION_SERVICE);
        if (nm != null && id != 0) nm.cancel(id);
    }
}
