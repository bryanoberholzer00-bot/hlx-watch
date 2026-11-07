"""Le opzioni riservate a HLX negli schemi: cosa sblocca ogni "mod != hlx"."""
import json

from hlxwatch import opzioni

SCHEMA = '''// MGetKV3ClassDefaults = {
class CBaseRendererSource2 : public CParticleFunctionRenderer
{
	// MPropertyFriendlyName = "Fog Scale"
	// MPropertySortPriority = 300
	// MPropertySuppressExpr = "mod != hlx"
	CParticleCollectionRendererFloatInput m_flFogAmount;
	// MPropertyFriendlyName = "Only Render in effects water pass"
	// MPropertySuppressExpr = "mod != csgo && mod != hlx"
	bool m_bOnlyRenderInEffectsWaterPass;
	// MPropertyFriendlyName = "refract in 2 passes"
	// MPropertySuppressExpr = "mod != hlx || !m_bRefract"
	bool m_bRefract2Passes;
	// MPropertyFriendlyName = "Apply fog of war to color"
	// MPropertySuppressExpr = "mod != dota"
	bool m_bApplyFogOfWar;
	// MPropertyFriendlyName = "senza commenti HLX"
	bool m_bNormal;
};
'''


def test_parse_finds_only_hlx_options():
    found = opzioni.parse(SCHEMA, "DumpSource2/schemas/particles/CBaseRendererSource2.h")
    assert [(o["classe"], o["campo"], o["nome"]) for o in found] == [
        ("CBaseRendererSource2", "m_flFogAmount", "Fog Scale"),
        ("CBaseRendererSource2", "m_bOnlyRenderInEffectsWaterPass", "Only Render in effects water pass"),
        ("CBaseRendererSource2", "m_bRefract2Passes", "refract in 2 passes")]


def test_scope():
    assert opzioni.scope("mod != hlx") == "solo HLX"
    assert opzioni.scope("mod != csgo && mod != hlx") == "HLX e CS2"
    assert opzioni.scope("mod != dota && mod != hlx") == "HLX e Dota 2"
    assert opzioni.scope("mod != hlx || !m_bRefract") == "solo HLX (con una condizione)"


class _Proc:
    def __init__(self, out, code=0):
        self.stdout, self.returncode = out.encode(), code


class _Db:
    def __init__(self):
        import sqlite3
        self.s = {}
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute("CREATE TABLE state (key TEXT PRIMARY KEY, value TEXT)")
        self.conn.execute("CREATE TABLE first_seen (game TEXT, file_path TEXT, line_hash TEXT, commit_date TEXT, at_least INT)")

    def get_state(self, key, default=None):
        r = self.conn.execute("SELECT value FROM state WHERE key = ?", (key,)).fetchone()
        return r[0] if r else default

    def set_state(self, key, value):
        self.conn.execute("INSERT OR REPLACE INTO state VALUES (?, ?)", (key, value))


def test_collect_and_summary_merge_games():
    from hlxwatch.sources.first_seen import line_hash
    path = "DumpSource2/schemas/particles/CBaseRendererSource2.h"

    def git(args):
        if args[0] == "grep":
            return _Proc(f"HEAD:{path}\n")
        return _Proc(SCHEMA)
    db = _Db()
    assert len(opzioni.collect(db, "cs2", git)) == 3
    opzioni.collect(db, "dota2", git)
    db.conn.execute("INSERT INTO first_seen VALUES ('cs2', ?, ?, '2026-06-25T10:00:00Z', 0)",
                    (path, line_hash('// MPropertySuppressExpr = "mod != csgo && mod != hlx"')))
    s = {o["campo"]: o for o in opzioni.summary(db)}
    water = s["m_bOnlyRenderInEffectsWaterPass"]
    assert water["giochi"] == ["cs2", "dota2"] and water["per"] == "HLX e CS2" and water["dal"].startswith("2026-06-25")
    assert water["spiegazione"] and not water["solo"] and s["m_flFogAmount"]["solo"]
    assert {o["campo"] for o in opzioni.summary(db)[:2]} == {"m_flFogAmount", "m_bRefract2Passes"}   # prima le "solo HLX"
    assert json.loads(db.get_state("opzioni_hlx:cs2"))[0]["campo"] == "m_flFogAmount"


def test_schema_archive_start_is_at_least():
    from hlxwatch.sources.first_seen import line_hash
    path = "DumpSource2/schemas/particles/CBaseRendererSource2.h"
    db = _Db()
    opzioni.collect(db, "cs2", lambda a: _Proc(f"HEAD:{path}\n") if a[0] == "grep" else _Proc(SCHEMA))
    db.conn.execute("INSERT INTO first_seen VALUES ('cs2', 'DumpSource2/schemas/altro.h', 'x', '2025-05-17T08:00:00Z', 0)")
    db.conn.execute("INSERT INTO first_seen VALUES ('cs2', ?, ?, '2025-05-17T08:00:00Z', 0)",
                    (path, line_hash('// MPropertySuppressExpr = "mod != hlx"')))
    db.conn.execute("INSERT INTO first_seen VALUES ('cs2', ?, ?, '2026-06-25T10:00:00Z', 0)",
                    (path, line_hash('// MPropertySuppressExpr = "mod != csgo && mod != hlx"')))
    s = {o["campo"]: o for o in opzioni.summary(db)}
    assert s["m_flFogAmount"]["almeno"] is True                      # il primo giorno degli schemi: c'era già
    assert s["m_bOnlyRenderInEffectsWaterPass"]["almeno"] is False
