package io.github.bryanoberholzer00bot.hlxwatch;

import android.content.Context;
import android.content.SharedPreferences;

import org.json.JSONObject;

/**
 * Le scelte dell'utente (schermata Impostazioni) e il filtro che CheckJob applica alle voci prima di notificarle.
 * Un possibile annuncio (campo "a" = 1, anche la prova generale) passa sempre: è il motivo per cui l'app esiste.
 */
final class Prefs {

    static final String FILE = "impostazioni";

    /** Tipi di voce che si possono escludere: chiave della preferenza, etichetta nella schermata. */
    static final String[][] CATEGORIES = {
            {"cat_file", "Scoperte nei file dei giochi (HLX, parole chiave)"},
            {"cat_build", "Build, app Steam e repository nuovi"},
            {"cat_news", "Post ufficiali di Valve su Steam"},
            {"cat_code", "Codice pubblico di Valve (GitHub, Proton)"},
            {"cat_web", "Domini e siti di Valve"},
            {"cat_hw", "Hardware Valve (controller, visori)"},
            {"cat_ach", "Obiettivi di Steam"},
    };

    /** Soglie di punteggio: valore, etichetta. */
    static final int[] SCORES = {0, 100, 200};
    static final String[] SCORE_LABELS = {
            "Tutte le novità rilevanti",
            "Solo quelle importanti (100 punti o più)",
            "Solo HLX forti e annunci (200 punti o più)",
    };

    private Prefs() {
    }

    static SharedPreferences get(Context c) {
        return c.getSharedPreferences(FILE, Context.MODE_PRIVATE);
    }

    static int minScore(Context c) {
        return get(c).getInt("min_score", 0);
    }

    static boolean quietOn(Context c) {
        return get(c).getBoolean("quiet_on", true);
    }

    static int quietStart(Context c) {
        return get(c).getInt("quiet_start", CheckJob.QUIET_START);
    }

    static int quietEnd(Context c) {
        return get(c).getInt("quiet_end", CheckJob.QUIET_END);
    }

    static boolean alarmOn(Context c) {
        return get(c).getBoolean("alarm_on", true);
    }

    static boolean categoryOn(Context c, String key) {
        return get(c).getBoolean(key, true);
    }

    /** Ora del telefono dentro l'orario di silenzio (da quiet_start a quiet_end, anche a cavallo della mezzanotte). */
    static boolean inQuietHours(Context c, int hour) {
        if (!quietOn(c)) return false;
        int start = quietStart(c), end = quietEnd(c);
        if (start == end) return false;
        return start < end ? hour >= start && hour < end : hour >= start || hour < end;
    }

    /** Il tipo di una voce di bot/ultime.json (campo "t" = tipo di modifica, "g" = gioco). */
    static String category(JSONObject it) {
        String t = it.optString("t");
        switch (t) {
            case "hardware_id":
                return "cat_hw";
            case "achievement":
                return "cat_ach";
            case "domain":
                return "cat_web";
            case "commit":
            case "appid":
                return "cat_code";
            case "news":
                return "cat_news";
            case "new_repo":
                return "Valve su GitHub".equals(it.optString("g")) ? "cat_code" : "cat_build";
            case "build":
            case "new_app":
            case "structure":
                return "cat_build";
            default:
                return "cat_file";
        }
    }

    /** Vero se la voce va notificata secondo le impostazioni. Gli annunci sempre. */
    static boolean wanted(Context c, JSONObject it) {
        if (it.optInt("a") == 1) return true;
        return it.optInt("s") >= minScore(c) && categoryOn(c, category(it));
    }
}
