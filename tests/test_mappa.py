"""Mappa dei collegamenti: luoghi, pezzi arrivati insieme, gruppi e disegno."""
from hlxwatch import mappa, sistemi

HAMMER = "game/bin/win64/tools/hammer_strings.txt"
U1 = "https://github.com/SteamTracking/GameTracking-CS2/commit/e3aad14dcf39"


def tr(label, first, file, url="", key=None):
    return {"key": key or label.lower(), "label": label, "first": first, "games": {"cs2": first}, "file": file,
            "url": url, "slug": label.lower()}


def reg():
    return {"traces": [
        tr("CHLXCoverPreviewHelper", "2026-09-23", HAMMER, U1),
        tr("GetAttributeTable_CHLXCoverPreviewHelper", "2026-09-23", HAMMER, U1, key="getattr"),
        tr("CHLXBaseProjectileEmitterHelper", "2026-09-23", HAMMER, U1),
        tr('"- Thumper (HLX): causes antlions to run away briefly<br>"', "2025-07-28", "game/core/ai_basenpc.fgd", "u2"),
        tr("hlx_physics_particle_spawner", "2026-04-21", "game/bin/win64/assetpreview_strings.txt", "u3"),
        tr("mod != hlx", "2023-03-07", "game/bin/win64/particles_strings.txt", "u4"),
        # la versione commentata dello stesso pezzo, comparsa dopo in un altro archivio: non "arriva insieme" a niente
        tr('// MPropertySuppressExpr = "mod != hlx"', "2025-05-17", "DumpSource2/schemas/particles/C_OP_RenderSprites.h", "u5"),
        tr("mod != dota && mod != hlx", "2025-05-17", "DumpSource2/schemas/particles/CBaseRendererSource2.h", "u5"),
        tr("CHLXUserCmdPB", "2022-06-09", "Protobufs/usercmd.proto", "u6"),
    ]}


def test_place_rules_on_real_paths():
    cases = {
        HAMMER: "hammer", "game/core/ai_basenpc.fgd": "hammer",
        "game/bin/linuxsteamrt64/libparticles_strings.txt": "effetti",
        "DumpSource2/schemas/particles/CBaseRendererSource2.h": "effetti",
        "game/core/pak01_dir/soundevents/soundevents_diagnostics.vsndevts": "suoni",
        "game/core/pak01_dir/soundstacks/soundstacks_core_test.vsndstck": "suoni",
        "game/core/tools/bugreporter/config.txt": "bug", "game/core/tools/demoinfo2/demoinfo2.txt": "demo",
        "game/bin/win64/toolframework2_strings.txt": "base", "game/bin/win64/assetpreview_strings.txt": "anteprima",
        "Protobufs/usercmd.proto": "rete", "game/dota/bin/linuxsteamrt64/libclient_strings.txt": "gioco-dota",
        "game/citadel/bin/win64/client_strings.txt": "gioco-deadlock",
        "game/bin/win64/resourcecompiler_strings.txt": "strumenti", "qualcosa/di/strano.bin": "altro",
    }
    for path, want in cases.items():
        assert mappa.place_of(path) == want, path


def test_short_label_keeps_the_part_that_tells_names_apart():
    a = mappa.short_label("hlx_configurable_lightweight_body_collection")
    b = mappa.short_label("hlx_configurable_lightweight_body_entity")
    assert a != b and a.endswith("collection") and len(a) <= mappa.SHORT
    assert mappa.short_label('// MPropertySuppressExpr = "mod != hlx"') == "mod != hlx"
    assert mappa.short_label('"product"\t\t"HLX"') == "HLX nei prodotti"
    assert mappa.short_label("dev\\source2\\main\\content\\hlx\\test_shlit_alpha.exr") == "test_shlit_alpha.exr"


def test_graph_links_places_and_pieces_that_arrived_together():
    files = {"hlx_physics_particle_spawner": {"game/bin/win64/assetpreview_strings.txt", HAMMER,
                                              "game/bin/win64/toolframework2_strings.txt", "DumpSource2/.stringsignore"}}
    g = mappa.graph(reg(), files)
    by = {n["short"]: n for n in g["nodes"]}
    assert len(g["nodes"]) == 7                       # stesso pezzo scritto in modi diversi: una volta sola
    assert by["hlx_physics_particle_spawner"]["places"] == ["anteprima", "base", "hammer"]   # .stringsignore escluso
    assert by["Thumper (HLX): causes antlions to…"]["places"] == ["hammer"]
    pairs = {frozenset((g["nodes"][x["a"]]["short"], g["nodes"][x["b"]]["short"])) for x in g["links"]}
    assert pairs == {frozenset(("CHLXCoverPreviewHelper", "CHLXBaseProjectileEmitterHelper"))}
    gr = mappa.groups(g)
    assert gr[0]["n"] == 4 and gr[0]["places"][0]["key"] == "hammer"        # Hammer + il pezzo ponte
    assert [x["n"] for x in gr] == [4, 2, 1]
    assert gr[1]["places"][0]["key"] == "effetti" and gr[1]["first"] == "2023-03-07"


def test_layout_two_columns_without_overlaps():
    g = mappa.graph(reg(), {})
    gr = mappa.groups(g)
    d = mappa.layout(g, gr)
    ys = sorted(n["y"] for n in g["nodes"])
    assert all(b - a >= mappa.ROW for a, b in zip(ys, ys[1:]))
    assert all(n["x"] == mappa.PIECE_X for n in g["nodes"])
    for k, pl in g["places"].items():
        assert pl["right"] < mappa.PIECE_X, k
    ps = sorted(pl["y"] for pl in g["places"].values())
    assert all(b - a >= mappa.PLACE_GAP for a, b in zip(ps, ps[1:]))
    assert d["height"] > ys[-1]
    assert len(d["brackets"]) == 1 and d["brackets"][0]["n"] == 2 and d["brackets"][0]["day"] == "2026-09-23"


class _Db:
    class conn:
        @staticmethod
        def execute(*a):
            return []


def test_summary_texts_data():
    s = mappa.summary(_Db(), reg())
    assert s["n"] == 7 and s["groups"][0]["places"][0]["name"] == "Hammer"
    assert s["biggest"]["day"] == "2026-09-23" and len(s["biggest"]["pieces"]) == 2
    assert s["enemy_linked"] is False                 # nessun nemico insieme ai suoi suoni o effetti
    assert s["tools"] == 6                            # solo CHLXUserCmdPB sta nel codice di un gioco
    assert mappa.summary(_Db(), {"traces": []}) is None


def test_physics_rule_does_not_read_property_as_rope():
    assert sistemi.system_of('// MPropertySuppressExpr = "mod != hlx"') == "particelle"
    assert sistemi.system_of("hlx_rope_bridge") == "fisica"


def test_enemy_linked_only_when_arrived_in_the_same_update():
    r = reg()
    # un effetto nello stesso programma di un nemico (Hammer) ma arrivato in un altro giorno: non basta
    r["traces"].append(tr("hlx_particle_test_effetto", "2024-01-01", HAMMER, "u7"))
    assert mappa.summary(_Db(), r)["enemy_linked"] is False
    r["traces"].append(tr("hlx_2d_basic_sound", "2026-09-23", HAMMER, U1))
    assert mappa.summary(_Db(), r)["enemy_linked"] is True


# ------------------------------------------------------------------ avviso quando la mappa cambia
def _fp(r, files=None):
    g = mappa.graph(r, files or {})
    return mappa.fingerprint(g, mappa.groups(g))


def test_same_map_no_changes():
    fp = _fp(reg())
    assert mappa.changes(fp, fp) == []


def test_known_piece_in_a_new_place():
    old = _fp(reg())
    # la chiave della traccia è il nome in minuscolo (vedi tr): il pezzo di rete ora compare anche nella base comune
    new = _fp(reg(), {"chlxusercmdpb": {"game/bin/win64/toolframework2_strings.txt"}})
    lines = mappa.changes(old, new)
    assert any("CHLXUserCmdPB" in x and "Base comune degli strumenti" in x for x in lines)


def test_two_groups_joined():
    old = _fp(reg())
    # il pezzo di rete, prima da solo, compare anche in Hammer: il suo gruppo si unisce a quello di Hammer
    new = _fp(reg(), {"chlxusercmdpb": {HAMMER}})
    lines = mappa.changes(old, new)
    assert any(x.startswith("Si sono uniti gruppi") for x in lines)


def test_new_piece_joining_a_group_is_told_new_piece_alone_is_not():
    r = reg()
    old = _fp(r)
    r2 = reg()
    r2["traces"].append(tr("CHLXNuovoHelper", "2026-11-06", HAMMER, "u9"))
    assert any("CHLXNuovoHelper" in x and "nel gruppo di" in x for x in mappa.changes(old, _fp(r2)))
    r3 = reg()
    r3["traces"].append(tr("CHLXIsolato", "2026-11-06", "qualcosa/di/strano.bin", "u10"))
    assert mappa.changes(old, _fp(r3)) == []


class _StateDb:
    def __init__(self):
        self.s = {}

    def get_state(self, key, default=None):
        return self.s.get(key, default)

    def set_state(self, key, value):
        self.s[key] = value


class _Rec:
    name = "prova"

    def __init__(self):
        self.sent = []

    def send_text(self, title, text):
        self.sent.append((title, text))
        return True

    def send_groups(self, heading, groups):
        return True


class _Cfg:
    def get(self, key, default=None):
        return default


def test_watch_first_run_silent_then_warns_once(monkeypatch):
    import hlxwatch.outcomes as outcomes
    state = {"reg": reg(), "files": {}}
    monkeypatch.setattr(outcomes, "outcome_register", lambda cfg, db: state["reg"])
    monkeypatch.setattr(mappa, "files_by_trace", lambda db: state["files"])
    db, n = _StateDb(), _Rec()
    assert mappa.watch(_Cfg(), db, [n]) == [] and n.sent == []          # primo giro: salva e basta
    assert mappa.watch(_Cfg(), db, [n]) == []
    state["files"] = {"chlxusercmdpb": {HAMMER}}
    assert mappa.watch(_Cfg(), db, [n])
    assert len(n.sent) == 1 and n.sent[0][0] == mappa.TITLE and "mappa.html" in n.sent[0][1]
    assert mappa.watch(_Cfg(), db, [n]) == [] and len(n.sent) == 1    # niente doppioni


def test_watch_rules_changed_is_silent(monkeypatch):
    import hlxwatch.outcomes as outcomes
    monkeypatch.setattr(outcomes, "outcome_register", lambda cfg, db: reg())
    monkeypatch.setattr(mappa, "files_by_trace", lambda db: {})
    db, n = _StateDb(), _Rec()
    mappa.watch(_Cfg(), db, [n])
    import json
    old = json.loads(db.get_state(mappa.STATE_KEY))
    old["regole"] = "vecchie"
    old["luoghi"] = {k: [] for k in old["luoghi"]}
    db.set_state(mappa.STATE_KEY, json.dumps(old))
    assert mappa.watch(_Cfg(), db, [n]) == [] and n.sent == []
