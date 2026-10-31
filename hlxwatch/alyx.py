"""Confronto con Half-Life: Alyx: cosa c'era nei file pubblici prima del suo annuncio, accanto a HLX oggi.

Alyx nei file di Valve si chiamava `hlvr`. Il comando `alyx-storia` clona (bare, uno alla volta, poi li cancella)
i repository SteamTracking che coprono gli anni 2016-2020 e con `git log -G` trova la prima comparsa di ogni nome
`hlvr` fino all'uscita del gioco. Il risultato è storia chiusa: si salva una volta in `alyx_seed.json` e il sito lo
legge da lì, senza riclonare nulla a ogni giro.

Si cerca solo `hlvr` (e i nomi ufficiali comparsi dopo l'annuncio): i nomi con "alyx" degli anni precedenti, come
ACT_DI_ALYX_ZOMBIE_MELEE o tlk_alyx_enemy_dead, vengono da Half-Life 2 Episodio 2 e non dal gioco Alyx.
"""
from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import tempfile
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Iterator

log = logging.getLogger(__name__)

ANNOUNCED = "2019-11-21"           # trailer di presentazione (Valve lo aveva anticipato il 18 novembre)
RELEASED = "2020-03-23"
UNTIL = "2020-03-24"
PICKAXE = r"hlvr|hl_?alyx|half.?life.?alyx"
TOKEN = re.compile(r"[a-z0-9_]*(?:hlvr|hl_?alyx)[a-z0-9_]*", re.IGNORECASE)
SEED = Path(__file__).with_name("alyx_seed.json")

# (nome, titolo, repository, ramo): i giochi Source 2 e i programmi di Valve archiviati negli anni di Alyx
REPOS = [
    ("steamvr", "SteamVR", "SteamTracking/GameTracking-SteamVR", "master"),
    ("dota2", "Dota 2", "SteamTracking/GameTracking-Dota2", "master"),
    ("dota2test", "Dota 2 Test", "SteamTracking/GameTracking-Dota2Test", "master"),
    ("artifact", "Artifact", "SteamTracking/GameTracking-Artifact", "master"),
    ("underlords", "Dota Underlords", "SteamTracking/GameTracking-Underlords", "master"),
    ("steam", "client di Steam", "SteamTracking/SteamTracking", "master"),
]

# cosa erano nel gioco uscito alcuni dei nomi (solo quelli sicuri)
MEANINGS = {
    "hlvr_weapon_energygun": "la pistola",
    "hlvr_weapon_shotgun": "il fucile a pompa",
    "hlvr_weapon_rapidfire": "la mitraglietta",
    "item_hlvr_grenade_frag": "la granata",
    "item_hlvr_grenade_xen": "la granata Xen",
    "item_hlvr_crafting_currency_small": "la resina, la moneta per potenziare le armi",
    "item_hlvr_prop_battery": "le batterie da inserire nei macchinari",
    "item_hlvr_clip_energygun": "i caricatori della pistola",
    "item_hlvr_clip_shotgun_single": "le cartucce del fucile",
    "item_hlvr_clip_rapidfire": "i caricatori della mitraglietta",
}


def clean(token: str) -> str | None:
    """Un nome leggibile: niente simboli C++ storpiati dal compilatore, niente numeri davanti."""
    t = token.lower().strip("_")
    if "cschema" in t or "centityinstance" in t:
        return None
    t = re.sub(r"^n?\d+", "", t).strip("_")
    if sum(ch.isdigit() for ch in t) >= 4:      # dati casuali (immagini in base64 nei CSS di Steam), non nomi
        return None
    return t if len(t) >= 4 else None


def names_in(line: str) -> Iterator[str]:
    for tok in TOKEN.findall(line):
        name = clean(tok)
        if name:
            yield name


def scan_history(lines: Iterable[str], repo: str) -> dict[str, dict[str, Any]]:
    """Da `git log --reverse -p` alla prima comparsa (riga aggiunta) di ogni nome."""
    first: dict[str, dict[str, Any]] = {}
    sha = day = path = ""
    for raw in lines:
        line = raw.rstrip("\n")
        if line.startswith("@@@"):
            sha, day = (line[3:].split("|") + [""])[:2]
        elif line.startswith("+++ "):
            path = line[6:] if line.startswith("+++ b/") else ""
        elif line.startswith("+") and day:
            for name in names_in(line):
                if name not in first or day < first[name]["day"]:
                    first[name] = {"name": name, "day": day, "repo": repo, "path": path, "sha": sha,
                                   "line": line[1:].strip()[:160]}
    return first


def _git(git_dir: Path, *args: str) -> subprocess.Popen:
    return subprocess.Popen(["git", "-C", str(git_dir), *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, encoding="utf-8", errors="replace")


def collect(workdir: Path, only: list[str] | None = None) -> dict[str, Any]:
    names: dict[str, dict[str, Any]] = {}
    repos: dict[str, Any] = {}
    for name, title, slug, branch in REPOS:
        if only and name not in only:
            continue
        git_dir = workdir / f"{name}.git"
        try:
            if not git_dir.exists():
                log.info("[%s] clono %s", name, slug)
                subprocess.run(["git", "clone", "--quiet", "--bare", "--single-branch", "--branch", branch,
                                f"https://github.com/{slug}.git", str(git_dir)], check=True)
            p = _git(git_dir, "log", "--reverse", "--format=%cs", branch)
            start = (p.stdout.readline() if p.stdout else "").strip()
            p.kill()
            p = _git(git_dir, "log", "--reverse", "-i", "-E", "-G", PICKAXE, f"--until={UNTIL}",
                     "--format=@@@%h|%cs", "-p", "--unified=0", "--no-color", "--no-renames", "--no-ext-diff", branch)
            found = scan_history(p.stdout or [], name)
            p.wait()
            for k, v in found.items():
                if k not in names or v["day"] < names[k]["day"]:
                    names[k] = v
            repos[name] = {"title": title, "repo": slug, "start": start, "names": len(found)}
            log.info("[%s] archivio dal %s, %d nomi hlvr", name, start, len(found))
        except Exception as exc:             # un repository che non si clona non ferma gli altri
            log.exception("[%s] non riuscito", name)
            repos[name] = {"title": title, "repo": slug, "error": f"{type(exc).__name__}: {exc}"[:200]}
        finally:
            shutil.rmtree(git_dir, ignore_errors=True)
    return {"generated": date.today().isoformat(), "announced": ANNOUNCED, "released": RELEASED,
            "pickaxe": PICKAXE, "repos": repos, "names": sorted(names.values(), key=lambda n: (n["day"], n["name"]))}


def run(out: Path, workdir: str | None = None, only: list[str] | None = None) -> dict[str, Any]:
    base = Path(workdir or tempfile.mkdtemp(prefix="hlx-alyx-"))
    base.mkdir(parents=True, exist_ok=True)
    data = collect(base, only)
    out.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return data


def load_seed(path: Path = SEED) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _months(a: str, b: str) -> int:
    ya, ma = int(a[:4]), int(a[5:7])
    yb, mb = int(b[:4]), int(b[5:7])
    return (yb - ya) * 12 + (mb - ma)


def _curve(days: list[str], start: str, end: str) -> list[int]:
    """Nomi distinti comparsi finora, mese per mese da `start` a `end`."""
    per = Counter(_months(start, d) for d in days if start <= d <= end)
    out, total = [], 0
    for m in range(_months(start, end) + 1):
        total += per.get(m, 0)
        out.append(total)
    return out


def _it(day: str | None) -> str:
    return f"{day[8:10]}/{day[5:7]}/{day[:4]}" if day else ""


def compare(seed: dict[str, Any] | None, reg: dict[str, Any], today: str,
            game_title=lambda g: g) -> dict[str, Any] | None:
    """I numeri della pagina: Alyx dalla prima traccia all'uscita, HLX dalla prima traccia a oggi."""
    if not seed or not seed.get("names"):
        return None
    names = seed["names"]
    announced, released = seed.get("announced", ANNOUNCED), seed.get("released", RELEASED)
    a_start = names[0]["day"]
    pre = [n for n in names if n["day"] < announced]
    # se la prima traccia è a ridosso dell'inizio del suo archivio, c'era già da prima: la data è un minimo
    archive_start = (seed.get("repos", {}).get(names[0]["repo"]) or {}).get("start") or a_start
    near_start = (date.fromisoformat(a_start) - date.fromisoformat(archive_start)).days <= 60
    traces = sorted(reg.get("traces") or [], key=lambda t: t["first"])
    h_start = traces[0]["first"] if traces else None
    alyx_curve = _curve([n["day"] for n in names], a_start, released)
    hlx_curve = _curve([t["first"] for t in traces], h_start, today) if h_start else []
    years: dict[str, list[dict[str, Any]]] = {}
    titles = {k: r.get("title", k) for k, r in seed.get("repos", {}).items()}
    where = Counter(titles.get(n["repo"], n["repo"]) for n in pre)
    for n in pre:
        years.setdefault(n["day"][:4], []).append({**n, "it": _it(n["day"]), "game": titles.get(n["repo"], n["repo"]),
                                                   "meaning": MEANINGS.get(n["name"], "")})
    # nomi di Alyx ripresi da HLX: lo stesso nome con hlx al posto di hlvr, o un percorso che li cita entrambi
    by_name = {n["name"]: n for n in names}
    links = []
    for t in traces:
        label = t["label"].strip().lower()
        src = by_name.get(label.replace("hlx", "hlvr"))
        if src or "hlvr" in label:
            links.append({"hlx": t["label"].strip(), "hlx_it": _it(t["first"]), "alyx": src["name"] if src else "",
                          "alyx_it": _it(src["day"]) if src else ""})
    return {
        "links": links,
        "alyx": {"first": a_start, "first_it": _it(a_start), "first_game": titles.get(names[0]["repo"], ""),
                 "first_name": names[0]["name"],
                 "announced_it": _it(announced), "released_it": _it(released),
                 "months_to_announce": _months(a_start, announced), "months_to_release": _months(a_start, released),
                 "pre": len(pre), "total": len(names),
                 "where": [{"game": g, "n": k} for g, k in where.most_common()],
                 "archive_start_it": _it(archive_start), "at_archive_start": near_start,
                 "curve": alyx_curve, "announce_month": _months(a_start, announced)},
        "hlx": {"first": h_start, "first_it": _it(h_start), "months": _months(h_start, today) if h_start else 0,
                "total": len(traces), "curve": hlx_curve,
                "first_game": game_title(next(iter(traces[0]["games"]))) if traces else ""},
        "years": dict(sorted(years.items())),
        "repos": [{"name": k, **r} for k, r in seed.get("repos", {}).items()],
        "chart": chart(alyx_curve, hlx_curve, _months(a_start, announced)),
    }


def chart(alyx: list[int], hlx: list[int], announce: int, w: int = 720, h: int = 260) -> dict[str, Any]:
    """Due linee a gradini (nomi distinti finora) allineate sulla prima traccia: coordinate già pronte per l'SVG."""
    left, right, top, bottom = 44, 16, 14, 34
    months = max(len(alyx), len(hlx), 2) - 1
    peak = max([*alyx, *hlx, 1])
    step = 10 if peak > 40 else 5

    def x(m: float) -> float:
        return round(left + (w - left - right) * m / months, 1)

    def y(v: float) -> float:
        return round(h - bottom - (h - top - bottom) * v / peak, 1)

    def path(curve: list[int]) -> str:
        if not curve:
            return ""
        pts = [f"M{x(0)},{y(curve[0])}"]
        for m in range(1, len(curve)):
            pts.append(f"H{x(m)}V{y(curve[m])}")
        return "".join(pts)

    yticks = [{"v": v, "y": y(v)} for v in range(0, peak + 1, step)]
    xticks = [{"label": "prima traccia" if not m else "1 anno" if m == 12 else f"{m // 12} anni", "x": x(m)}
              for m in range(0, months + 1, 12)]
    return {"w": w, "h": h, "alyx": path(alyx), "hlx": path(hlx), "yticks": yticks, "xticks": xticks,
            "announce_x": x(announce), "announce_y": y(alyx[announce]) if announce < len(alyx) else y(0),
            "alyx_end": {"x": x(len(alyx) - 1), "y": y(alyx[-1])} if alyx else None,
            "hlx_end": {"x": x(len(hlx) - 1), "y": y(hlx[-1])} if hlx else None,
            "plot_top": top, "plot_bottom": h - bottom, "left": left, "right": w - right}
