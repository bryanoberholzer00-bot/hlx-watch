from hlxwatch import progetti


def n(name, day, repo="dota2"):
    return {"name": name, "day": day, "repo": repo, "path": "x.txt", "line": name}


SEED = {
    "repos": {"dota2": {"title": "Dota 2", "start": "2016-12-11"}, "steam": {"title": "client di Steam", "start": "2013-03-01"},
              "steamvr": {"title": "SteamVR", "start": "2017-05-23"}},
    "projects": {
        "deadlock": {"names": [n("d3_citadel_03", "2016-12-11"), n("citadelle", "2018-06-05"),
                               n("ccitadelmodifierhandletyped", "2021-03-25"), n("citadel_snd_opvar_set_obb", "2024-02-08")]},
        "deck": {"names": [n("jupiter", "2013-10-29", "steam"), n("neptune", "2015-10-29", "steam"),
                           n("s_rgpropvaluesgyrobuttonneptune", "2019-11-19", "steam")]},
        "frame": {"names": [n("libexpat", "2013-12-14", "steam"), n("deckard", "2022-03-31", "steamvr")]},
        "cs2": {"names": [n("k_ngamecsgo", "2017-06-04", "steamvr"), n("act_csgo_fall", "2019-04-26", "steamvr"),
                          n("clightbinnercsgo", "2019-06-07")]},
    },
}


def test_extractor_names():
    names = list(progetti.extractor("citadel")("+CCitadelPlayerPawn 27CCitadelGameRules tyu5htmg581ycitadel"))
    assert names == ["ccitadelplayerpawn", "ccitadelgamerules"]


def test_summary_applies_keep_and_skip():
    rows = {r["key"]: r for r in progetti.summary(SEED, "2026-10-31")}
    assert rows["deadlock"]["first"] == "2021-03-25" and rows["deadlock"]["names"] == 2 and rows["deadlock"]["months"] == 41
    assert rows["deck"]["first"] == "2019-11-19" and rows["deck"]["months"] == 20
    assert rows["frame"]["first_name"] == "deckard" and rows["frame"]["months"] == 44
    assert rows["cs2"]["first"] == "2019-06-07" and rows["cs2"]["months"] == 45
    assert [r["key"] for r in progetti.summary(SEED, "2026-10-31")][0] == "cs2"    # dal più lungo
    assert progetti.summary(None, "2026-10-31") == []


def test_before_announce_counts_last_months():
    alyx_seed = {"announced": "2019-11-21", "names": [n("hlvr_a", "2017-06-04"), n("hlvr_b", "2019-06-13"),
                                                      n("hlalyxpromo", "2020-03-12")]}
    reg = {"traces": [{"first": "2021-03-04"}, {"first": "2026-09-23"}, {"first": "2026-09-23"}, {"first": "2026-06-25"}]}
    out = progetti.before_announce(SEED, alyx_seed, reg, "2026-11-01")
    rows = {r["title"]: r for r in out["rows"]}
    assert rows["Half-Life: Alyx"]["last6"] == 1 and rows["Half-Life: Alyx"]["last3"] == 0
    assert rows["Half-Life: Alyx"]["last_it"] == "13/06/2019" and rows["Half-Life: Alyx"]["last_months"] == 5
    assert rows["Counter-Strike 2"]["last_months"] == 45 and rows["Steam Deck"]["note"]
    assert out["hlx"]["last6"] == 3 and out["hlx"]["last3"] == 2 and out["hlx"]["last_it"] == "23/09/2026"
    assert out["quiet"] == len(out["rows"])                     # nei dati di prova nessuno ha nomi negli ultimi 3 mesi
    assert progetti.before_announce(None, None, reg, "2026-11-01") is None


def test_real_seed_is_readable():
    rows = progetti.summary(progetti.load_seed(), "2026-10-31")
    assert {r["key"] for r in rows} == {"deadlock", "cs2", "deck", "frame"}
