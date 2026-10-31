package io.github.bryanoberholzer00bot.hlxwatch;

import android.app.Activity;
import android.app.NotificationManager;
import android.content.Intent;
import android.graphics.Typeface;
import android.os.Build;
import android.os.Bundle;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.WindowManager;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

/**
 * Schermata dell'allarme: si apre a tutto schermo, anche a telefono bloccato, quando arriva un possibile annuncio
 * (o la prova generale). La notifica suona come una sveglia finché non la si chiude: "Apri" porta alla voce sul sito,
 * "Ho visto" ferma soltanto il suono.
 */
public class AlarmActivity extends Activity {

    static final String EXTRA_TITLE = "title";
    static final String EXTRA_TEXT = "text";
    static final String EXTRA_ID = "id";

    @Override
    @SuppressWarnings("deprecation")
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        if (Build.VERSION.SDK_INT >= 27) {
            setShowWhenLocked(true);
            setTurnScreenOn(true);
        } else {
            getWindow().addFlags(WindowManager.LayoutParams.FLAG_SHOW_WHEN_LOCKED
                    | WindowManager.LayoutParams.FLAG_TURN_SCREEN_ON);
        }
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);

        Intent in = getIntent();
        String title = in.getStringExtra(EXTRA_TITLE);
        String text = in.getStringExtra(EXTRA_TEXT);
        String url = in.getStringExtra(MainActivity.EXTRA_URL);
        int id = in.getIntExtra(EXTRA_ID, 0);
        boolean drill = title != null && title.contains("PROVA");

        float dp = getResources().getDisplayMetrics().density;
        LinearLayout box = new LinearLayout(this);
        box.setOrientation(LinearLayout.VERTICAL);
        box.setGravity(Gravity.CENTER_HORIZONTAL);
        int pad = (int) (24 * dp);
        box.setPadding(pad, (int) (64 * dp), pad, pad);
        box.setBackgroundColor(drill ? 0xFF1F2430 : 0xFF5A1010);

        TextView head = new TextView(this);
        head.setText(drill ? "PROVA GENERALE" : "POSSIBILE ANNUNCIO");
        head.setTextColor(0xFFFFFFFF);
        head.setTypeface(Typeface.DEFAULT_BOLD);
        head.setTextSize(TypedValue.COMPLEX_UNIT_SP, 30);
        head.setGravity(Gravity.CENTER);
        box.addView(head);

        TextView sub = new TextView(this);
        sub.setText(title == null ? "HLX Watch" : title);
        sub.setTextColor(0xFFFFD7C2);
        sub.setTextSize(TypedValue.COMPLEX_UNIT_SP, 17);
        sub.setGravity(Gravity.CENTER);
        sub.setPadding(0, (int) (12 * dp), 0, (int) (20 * dp));
        box.addView(sub);

        TextView body = new TextView(this);
        body.setText(text == null ? "" : text);
        body.setTextColor(0xFFE5E7EB);
        body.setTextSize(TypedValue.COMPLEX_UNIT_SP, 16);
        box.addView(body);

        Button open = new Button(this);
        open.setText("Apri");
        open.setOnClickListener(v -> {
            stopAlarm(id);
            Intent site = new Intent(this, MainActivity.class)
                    .setFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_SINGLE_TOP);
            if (url != null) site.putExtra(MainActivity.EXTRA_URL, url);
            startActivity(site);
            finish();
        });
        Button ok = new Button(this);
        ok.setText("Ho visto (silenzia)");
        ok.setOnClickListener(v -> {
            stopAlarm(id);
            finish();
        });
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT);
        lp.topMargin = (int) (28 * dp);
        box.addView(open, lp);
        LinearLayout.LayoutParams lp2 = new LinearLayout.LayoutParams(lp);
        lp2.topMargin = (int) (12 * dp);
        box.addView(ok, lp2);

        ScrollView scroll = new ScrollView(this);
        scroll.setFillViewport(true);
        scroll.setBackgroundColor(drill ? 0xFF1F2430 : 0xFF5A1010);
        scroll.addView(box);
        setContentView(scroll);
    }

    /** Chiudere la notifica ferma il suono (la notifica dell'allarme suona finché esiste). */
    private void stopAlarm(int id) {
        AlarmSound.stop();
        NotificationManager nm = (NotificationManager) getSystemService(NOTIFICATION_SERVICE);
        if (nm != null && id != 0) nm.cancel(id);
    }
}
