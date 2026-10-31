package io.github.bryanoberholzer00bot.hlxwatch;

import android.app.Activity;
import android.app.NotificationManager;
import android.content.ActivityNotFoundException;
import android.content.Intent;
import android.content.SharedPreferences;
import android.graphics.Typeface;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.provider.Settings;
import android.util.TypedValue;
import android.view.View;
import android.view.ViewGroup;
import android.widget.AdapterView;
import android.widget.ArrayAdapter;
import android.widget.Button;
import android.widget.CheckBox;
import android.widget.LinearLayout;
import android.widget.RadioButton;
import android.widget.RadioGroup;
import android.widget.ScrollView;
import android.widget.Spinner;
import android.widget.Switch;
import android.widget.TextView;

/**
 * Impostazioni: soglia delle notifiche, orario di silenzio, tipi di voce, allarme per gli annunci e aggiornamenti
 * dell'app. Si apre dall'ingranaggio in basso a destra nell'app o tenendo premuta l'icona (scorciatoia).
 * Le scelte si salvano subito; i possibili annunci arrivano sempre, qualunque cosa si scelga.
 */
public class SettingsActivity extends Activity {

    private static final int ACCENT = 0xFFE8743B;
    private LinearLayout box;
    private float dp;
    private TextView versionText;
    private Button fullScreenButton;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        dp = getResources().getDisplayMetrics().density;
        SharedPreferences p = Prefs.get(this);

        box = new LinearLayout(this);
        box.setOrientation(LinearLayout.VERTICAL);
        int pad = (int) (18 * dp);
        box.setPadding(pad, pad, pad, (int) (40 * dp));

        TextView title = text("Impostazioni", 24);
        title.setTypeface(Typeface.DEFAULT_BOLD);
        box.addView(title);
        box.addView(note("I possibili annunci di Valve arrivano sempre, anche di notte e con qualunque filtro."));

        // ------------------------------------------------------------- soglia
        heading("Quali novità notificare");
        RadioGroup scores = new RadioGroup(this);
        int current = Prefs.minScore(this);
        for (int i = 0; i < Prefs.SCORES.length; i++) {
            RadioButton r = new RadioButton(this);
            r.setId(View.generateViewId());
            r.setText(Prefs.SCORE_LABELS[i]);
            r.setChecked(Prefs.SCORES[i] == current);
            final int value = Prefs.SCORES[i];
            r.setOnCheckedChangeListener((b, on) -> {
                if (on) p.edit().putInt("min_score", value).apply();
            });
            scores.addView(r);
        }
        box.addView(scores);

        heading("Tipi di voce");
        for (String[] cat : Prefs.CATEGORIES) {
            CheckBox c = new CheckBox(this);
            c.setText(cat[1]);
            c.setChecked(Prefs.categoryOn(this, cat[0]));
            c.setOnCheckedChangeListener((b, on) -> p.edit().putBoolean(cat[0], on).apply());
            box.addView(c);
        }

        // ------------------------------------------------------------- orario di silenzio
        heading("Orario di silenzio");
        Switch quiet = new Switch(this);
        quiet.setText("Di notte niente notifiche: le novità arrivano riassunte al mattino");
        quiet.setChecked(Prefs.quietOn(this));
        box.addView(quiet);
        LinearLayout hours = new LinearLayout(this);
        hours.setOrientation(LinearLayout.HORIZONTAL);
        hours.addView(text("dalle ", 16));
        Spinner start = hourSpinner(Prefs.quietStart(this), "quiet_start");
        hours.addView(start);
        hours.addView(text("  alle ", 16));
        Spinner end = hourSpinner(Prefs.quietEnd(this), "quiet_end");
        hours.addView(end);
        box.addView(hours);
        quiet.setOnCheckedChangeListener((b, on) -> {
            p.edit().putBoolean("quiet_on", on).apply();
            start.setEnabled(on);
            end.setEnabled(on);
        });
        start.setEnabled(quiet.isChecked());
        end.setEnabled(quiet.isChecked());

        // ------------------------------------------------------------- allarme
        heading("Allarme per gli annunci");
        Switch alarm = new Switch(this);
        alarm.setText("Un possibile annuncio suona come una sveglia, finché non lo apri, e si mostra a tutto schermo");
        alarm.setChecked(Prefs.alarmOn(this));
        alarm.setOnCheckedChangeListener((b, on) -> p.edit().putBoolean("alarm_on", on).apply());
        box.addView(alarm);
        Button test = button("Prova l'allarme adesso");
        test.setOnClickListener(v -> CheckJob.showAlarm(this, 77, "HLX Watch: PROVA dell'allarme",
                "Così suonerà un possibile annuncio. Tocca \"Ho visto\" per fermarlo.", MainActivity.HOME));
        box.addView(test);
        fullScreenButton = button("Consenti la schermata a tutto schermo");
        fullScreenButton.setOnClickListener(v -> open(new Intent("android.settings.MANAGE_APP_USE_FULL_SCREEN_INTENT",
                Uri.parse("package:" + getPackageName()))));
        box.addView(fullScreenButton);
        Button dnd = button("Suona anche con \"Non disturbare\"");
        dnd.setOnClickListener(v -> {
            if (Build.VERSION.SDK_INT >= 26) {
                CheckJob.createChannel(this);
                open(new Intent(Settings.ACTION_CHANNEL_NOTIFICATION_SETTINGS)
                        .putExtra(Settings.EXTRA_APP_PACKAGE, getPackageName())
                        .putExtra(Settings.EXTRA_CHANNEL_ID, CheckJob.ALARM_CHANNEL));
            }
        });
        box.addView(dnd);
        box.addView(note("Il suono è quello della sveglia, che \"Non disturbare\" di solito lascia passare. Per esserne "
                + "sicuro, apri il pulsante qui sopra e attiva \"Ignora Non disturbare\" (il nome cambia con il telefono)."));

        // ------------------------------------------------------------- aggiornamenti
        heading("Aggiornamenti dell'app");
        versionText = note("");
        box.addView(versionText);
        showVersion();
        Button check = button("Cerca aggiornamenti");
        check.setOnClickListener(v -> {
            versionText.setText("Controllo in corso…");
            new Thread(() -> {
                String error = null;
                try {
                    Updates.check(getApplicationContext(), true);
                } catch (Exception e) {
                    error = "GitHub non risponde: riprova più tardi.";
                }
                final String err = error;
                runOnUiThread(() -> {
                    showVersion();
                    if (err != null) versionText.append("\n" + err);
                });
            }).start();
        });
        box.addView(check);
        Button download = button("Scarica l'ultima versione");
        download.setOnClickListener(v -> open(Updates.downloadIntent()));
        box.addView(download);

        ScrollView scroll = new ScrollView(this);
        scroll.addView(box);
        setContentView(scroll);
        MainActivity.fitSystemBars(this, scroll);
    }

    @Override
    protected void onResume() {
        super.onResume();
        // da Android 14 lo schermo intero va concesso a mano: il pulsante compare solo se serve
        boolean needed = false;
        if (Build.VERSION.SDK_INT >= 34) {
            NotificationManager nm = getSystemService(NotificationManager.class);
            needed = nm != null && !nm.canUseFullScreenIntent();
        }
        fullScreenButton.setVisibility(needed ? View.VISIBLE : View.GONE);
    }

    private void showVersion() {
        long installed = Updates.installed(this);
        long latest = Updates.latestKnown(this);
        String s = "Versione installata: build " + installed;
        if (latest > installed) {
            s += "\nDisponibile la build " + latest + ": scaricala e installala sopra questa.";
        } else if (latest > 0) {
            s += " (è l'ultima)";
        }
        s += "\nL'app controlla da sola ogni 6 ore e ti avvisa con una notifica.";
        versionText.setText(s);
    }

    private Spinner hourSpinner(int value, String key) {
        String[] labels = new String[24];
        for (int h = 0; h < 24; h++) labels[h] = (h < 10 ? "0" : "") + h + ":00";
        Spinner sp = new Spinner(this);
        ArrayAdapter<String> a = new ArrayAdapter<>(this, android.R.layout.simple_spinner_item, labels);
        a.setDropDownViewResource(android.R.layout.simple_spinner_dropdown_item);
        sp.setAdapter(a);
        sp.setSelection(value);
        sp.setOnItemSelectedListener(new AdapterView.OnItemSelectedListener() {
            @Override
            public void onItemSelected(AdapterView<?> parent, View view, int position, long id) {
                Prefs.get(SettingsActivity.this).edit().putInt(key, position).apply();
            }

            @Override
            public void onNothingSelected(AdapterView<?> parent) {
            }
        });
        return sp;
    }

    private TextView text(String s, int sp) {
        TextView t = new TextView(this);
        t.setText(s);
        t.setTextSize(TypedValue.COMPLEX_UNIT_SP, sp);
        return t;
    }

    private TextView note(String s) {
        TextView t = text(s, 14);
        t.setAlpha(0.75f);
        t.setPadding(0, (int) (4 * dp), 0, (int) (4 * dp));
        return t;
    }

    private void heading(String s) {
        TextView t = text(s, 18);
        t.setTypeface(Typeface.DEFAULT_BOLD);
        t.setTextColor(ACCENT);
        t.setPadding(0, (int) (22 * dp), 0, (int) (6 * dp));
        box.addView(t);
    }

    private Button button(String s) {
        Button b = new Button(this);
        b.setText(s);
        b.setAllCaps(false);
        b.setLayoutParams(new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT));
        return b;
    }

    private void open(Intent intent) {
        try {
            startActivity(intent);
        } catch (ActivityNotFoundException e) {
            // il telefono non ha quella pagina delle impostazioni: si apre quella generale dell'app
            try {
                startActivity(new Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS,
                        Uri.parse("package:" + getPackageName())));
            } catch (ActivityNotFoundException ignored) {
                // niente da fare
            }
        }
    }
}
