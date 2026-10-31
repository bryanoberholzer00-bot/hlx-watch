"""Quanto restano nei file pubblici i progetti di Valve prima dell'annuncio: lo stesso metodo del confronto con Alyx,
applicato ad altri prodotti con un nome in codice noto.

Il comando `progetti-storia` clona una volta ogni repository SteamTracking che serve (bare, poi lo cancella) e, per
ogni progetto, trova con `git log -G` la prima comparsa di ogni nome che contiene il nome in codice, fino al giorno
dell'annuncio. Il risultato è storia chiusa e si salva in `progetti_seed.json`; i falsi positivi si tolgono dopo,
con le regole `skip` di ogni progetto (applicate anche quando il sito legge il file).
"""
from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import tempfile
from datetime import date
from pathlib import Path
from typing import Any

from hlxwatch.alyx import _git, _it, _months, scan_history

log = logging.getLogger(__name__)

SEED = Path(__file__).with_name("progetti_seed.json")

REPOS = {
    "dota2": ("Dota 2", "SteamTracking/GameTracking-Dota2", "master"),
    "dota2test": ("Dota 2 Test", "SteamTracking/GameTracking-Dota2Test", "master"),
    "cs2": ("Counter-Strike", "SteamTracking/GameTracking-CS2", "master"),
    "underlords": ("Dota Underlords", "SteamTracking/GameTracking-Underlords", "master"),
    "artifact": ("Artifact", "SteamTracking/GameTracking-Artifact", "master"),
    "steamvr": ("SteamVR", "SteamTracking/GameTracking-SteamVR", "master"),
    "steam": ("client di Steam", "SteamTracking/SteamTracking", "master"),
}

# announced: giorno dell'annuncio ufficiale. codes: nomi in codice (espressione per git e per i nomi).
# skip: nomi da non contare (espressioni), trovati controllando i risultati a mano.
PROJECTS = {
    "deadlock": {"title": "Deadlock", "code": "citadel", "announced": "2024-08-23",
                 "codes": r"citadel", "repos": ["dota2", "dota2test", "cs2", "underlords", "artifact", "steamvr"],
                 "skip": []},
    "cs2": {"title": "Counter-Strike 2", "code": "csgo (in Source 2)", "announced": "2023-03-22",
            "codes": r"csgo", "repos": ["dota2", "dota2test", "underlords", "artifact", "steamvr"], "skip": []},
    "deck": {"title": "Steam Deck", "code": "neptune, steampal", "announced": "2021-07-15",
             "codes": r"neptune|steampal|jupiter", "repos": ["steam"], "skip": []},
    "frame": {"title": "Steam Frame, Steam Machine e Steam Controller", "code": "deckard, fremont, ibex",
              "announced": "2025-11-12", "codes": r"deckard|fremont|ibex", "repos": ["steamvr", "steam"], "skip": []},
}

MAX_NAMES = 4000           # per progetto: i nomi molto comuni (csgo) non devono gonfiare il file


def extractor(codes: str):
    token = re.compile(rf"[a-z0-9_]*(?:{codes})[a-z0-9_]*", re.IGNORECASE)

    def names(line: str):
        for tok in token.findall(line):
            t = re.sub(r"^n?\d+", "", tok.lower().strip("_")).strip("_")
            if "cschema" in t or "centityinstance" in t or sum(ch.isdigit() for ch in t) >= 4:
                continue
            if len(t) >= 4:
                yield t
    return names


def collect(workdir: Path, only: list[str] | None = None) -> dict[str, Any]:
    wanted = {k: p for k, p in PROJECTS.items() if not only or k in only}
    names: dict[str, dict[str, dict[str, Any]]] = {k: {} for k in wanted}
    repos: dict[str, Any] = {}
    for repo in [r for r in REPOS if any(r in p["repos"] for p in wanted.values())]:
        title, slug, branch = REPOS[repo]
        git_dir = workdir / f"{repo}.git"
        try:
            log.info("[%s] clono %s", repo, slug)
            subprocess.run(["git", "clone", "--quiet", "--bare", "--single-branch", "--branch", branch,
                            f"https://github.com/{slug}.git", str(git_dir)], check=True)
            p = _git(git_dir, "log", "--reverse", "--format=%cs", branch)
            start = (p.stdout.readline() if p.stdout else "").strip()
            p.kill()
            repos[repo] = {"title": title, "repo": slug, "start": start}
            for key, proj in wanted.items():
                if repo not in proj["repos"]:
                    continue
                p = _git(git_dir, "log", "--reverse", "-i", "-E", "-G", proj["codes"], f"--until={proj['announced']}",
                         "--format=@@@%h|%cs", "-p", "--unified=0", "--no-color", "--no-renames", "--no-ext-diff",
                         branch)
                found = scan_history(p.stdout or [], repo, extractor(proj["codes"]))
                p.wait()
                for k, v in found.items():
                    if k not in names[key] or v["day"] < names[key][k]["day"]:
                        names[key][k] = v
                log.info("[%s] %s: %d nomi", repo, key, len(found))
        except Exception as exc:              # un repository che non si clona non ferma gli altri
            log.exception("[%s] non riuscito", repo)
            repos[repo] = {"title": title, "repo": slug, "error": f"{type(exc).__name__}: {exc}"[:200]}
        finally:
            shutil.rmtree(git_dir, ignore_errors=True)
    out = {}
    for key, proj in wanted.items():
        found = sorted(names[key].values(), key=lambda n: (n["day"], n["name"]))
        out[key] = {"names": found[:MAX_NAMES], "truncated": len(found) > MAX_NAMES}
    return {"generated": date.today().isoformat(), "repos": repos, "projects": out}


def run(out: Path, workdir: str | None = None, only: list[str] | None = None) -> dict[str, Any]:
    base = Path(workdir or tempfile.mkdtemp(prefix="hlx-progetti-"))
    base.mkdir(parents=True, exist_ok=True)
    data = collect(base, only)
    out.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return data


def load_seed(path: Path = SEED) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def summary(seed: dict[str, Any] | None, today: str) -> list[dict[str, Any]]:
    """Per ogni progetto: prima traccia, mesi fino all'annuncio, nomi contati (senza i falsi positivi)."""
    if not seed:
        return []
    rows = []
    for key, proj in PROJECTS.items():
        data = (seed.get("projects") or {}).get(key)
        if not data:
            continue
        skip = [re.compile(s) for s in proj.get("skip", [])]
        names = [n for n in data["names"] if not any(s.search(n["name"]) for s in skip)]
        if not names:
            continue
        first = names[0]
        repo = (seed.get("repos") or {}).get(first["repo"], {})
        start = repo.get("start") or first["day"]
        near = (date.fromisoformat(first["day"]) - date.fromisoformat(start)).days <= 60
        rows.append({"key": key, "title": proj["title"], "code": proj["code"], "first": first["day"],
                     "first_it": _it(first["day"]), "first_name": first["name"], "first_line": first.get("line", ""),
                     "where": repo.get("title", first["repo"]), "announced_it": _it(proj["announced"]),
                     "months": _months(first["day"], proj["announced"]), "names": len(names), "at_start": near})
    return sorted(rows, key=lambda r: -r["months"])
