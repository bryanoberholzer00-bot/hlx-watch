"""Sorgente GitHub: commit dei repository pubblici SteamDatabase/GameTracking-*.

Usa solo la REST API ufficiale (api.github.com) e raw.githubusercontent.com per i file
di cui GitHub non fornisce la patch (troppo grandi). Rispetta i rate limit leggendo gli
header X-RateLimit-* e Retry-After.
"""
from __future__ import annotations

import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote

import requests
from requests.adapters import HTTPAdapter

from hlxwatch import __version__
from hlxwatch.analyzers import KeywordMatcher, analyze_line_sets, analyze_patch
from hlxwatch.analyzers.codenames import CodenameTracker
from hlxwatch.analyzers.localization import LocalizationTracker
from hlxwatch.analyzers.renames import find_renames
from hlxwatch.analyzers.diff_analyzer import parse_patch
from hlxwatch.config import Config
from hlxwatch.models import BuildInfo, Finding
from hlxwatch.repos import tracked_repos
from hlxwatch.storage import Database
from hlxwatch.storage.db import iso, parse_iso, utcnow

log = logging.getLogger(__name__)

TRIVIAL_LINE = re.compile(r"[\s{}\[\]()/,;]*")      # parentesi e punteggiatura: non sono righe di contenuto


def format_key(text: str) -> str:
    """Una riga senza spazi né maiuscole, e con i tipi delle FGD unificati: nel 2026 Valve ha riscritto
    "choices" in "intchoices" in tutte le FGD, e quelle righe non sono modifiche di contenuto."""
    return re.sub(r"\s+", "", text).lower().replace("intchoices", "choices")
BUILDID_RE = re.compile(r"build\s*id\D{0,5}(\d{5,})", re.IGNORECASE)
STEAM_INF_RE = re.compile(r"^([+-])(ClientVersion|SourceRevision|VersionDate)=(.*)$", re.MULTILINE)


def steam_inf_build(patch: str) -> dict[str, str] | None:
    """Nuova versione del gioco dalla patch di steam.inf: serve dove Steam in anonimo non mostra le build
    (Deadlock). Restituisce None se ClientVersion non cambia (per esempio cambia solo l'orario)."""
    new: dict[str, str] = {}
    old: dict[str, str] = {}
    for sign, key, value in STEAM_INF_RE.findall(patch):
        (new if sign == "+" else old)[key] = value.strip()
    version = new.get("ClientVersion")
    if not version or version == old.get("ClientVersion"):
        return None
    return {"version": version, "previous": old.get("ClientVersion", ""),
            "revision": new.get("SourceRevision", ""), "date": new.get("VersionDate", "")}
FILES_PER_PAGE = 300
COMMITS_PER_PAGE = 100


class GitHubClient:
    """Client REST minimale con retry, backoff e gestione del rate limit."""

    def __init__(self, api_url: str, raw_url: str, token: str | None = None, timeout: int = 60,
                 pool_size: int = 8):
        self.api_url = api_url.rstrip("/")
        self.raw_url = raw_url.rstrip("/")
        self.timeout = timeout
        self.remaining: int | None = None
        self.session = requests.Session()
        adapter = HTTPAdapter(pool_connections=max(4, pool_size), pool_maxsize=max(4, pool_size))
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)
        self.session.headers.update({
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": f"hlx-watch/{__version__}",
        })
        if token:
            self.session.headers["Authorization"] = f"Bearer {token}"

    # ------------------------------------------------------------ richieste
    def _retry_wait(self, response: requests.Response) -> int | None:
        retry_after = response.headers.get("Retry-After")
        if retry_after and retry_after.isdigit():
            return min(int(retry_after) + 1, 3600)
        if response.headers.get("X-RateLimit-Remaining") == "0":
            reset = response.headers.get("X-RateLimit-Reset")
            if reset and reset.isdigit():
                return max(5, min(int(reset) - int(time.time()) + 2, 3600))
            return 60
        if "rate limit" in (response.text or "").lower():
            return 60
        return None

    def _request(self, url: str, params: dict[str, Any] | None = None, stream: bool = False,
                 max_retries: int = 6) -> requests.Response | None:
        backoff = 5
        for attempt in range(1, max_retries + 1):
            try:
                response = self.session.get(url, params=params, timeout=self.timeout, stream=stream)
            except requests.RequestException as exc:
                log.warning("Errore di rete verso %s: %s (tentativo %d/%d)", url, exc, attempt, max_retries)
                time.sleep(backoff)
                backoff = min(backoff * 2, 120)
                continue
            remaining = response.headers.get("X-RateLimit-Remaining")
            if remaining and remaining.isdigit():
                self.remaining = int(remaining)
            if response.status_code == 404:
                response.close()
                return None
            if response.status_code in (403, 429):
                wait = self._retry_wait(response)
                if wait is not None:
                    log.warning("Rate limit GitHub raggiunto: attendo %d secondi", wait)
                    response.close()
                    time.sleep(wait)
                    continue
            if response.status_code >= 500:
                log.warning("GitHub ha risposto %s su %s, riprovo tra %ds", response.status_code, url, backoff)
                response.close()
                time.sleep(backoff)
                backoff = min(backoff * 2, 120)
                continue
            if response.status_code >= 400:
                body = response.text[:300]
                response.close()
                raise requests.HTTPError(f"GitHub {response.status_code} su {url}: {body}")
            return response
        raise RuntimeError(f"Troppi tentativi falliti verso {url}")

    def get_json(self, path: str, params: dict[str, Any] | None = None) -> Any:
        response = self._request(self.api_url + path, params=params)
        if response is None:
            return None
        try:
            return response.json()
        finally:
            response.close()

    # -------------------------------------------------------------- commit
    def list_commits(self, repo: str, branch: str, *, since: datetime | None = None,
                     stop_at_sha: str | None = None, max_commits: int = 0, max_pages: int = 60) -> list[dict[str, Any]]:
        """Ritorna i commit dal più vecchio al più nuovo (GitHub li espone dal più nuovo)."""
        commits: list[dict[str, Any]] = []
        for page in range(1, max_pages + 1):
            params: dict[str, Any] = {"sha": branch, "per_page": COMMITS_PER_PAGE, "page": page}
            if since is not None:
                params["since"] = iso(since)
            data = self.get_json(f"/repos/{repo}/commits", params)
            if not data:
                break
            stop = False
            for commit in data:
                if stop_at_sha and commit["sha"] == stop_at_sha:
                    stop = True
                    break
                commits.append(commit)
            if stop or len(data) < COMMITS_PER_PAGE:
                break
            if page == max_pages:
                log.warning("Raggiunto il limite di %d pagine di commit per %s", max_pages, repo)
        commits.reverse()
        if max_commits > 0 and len(commits) > max_commits:
            log.info("%s: %d commit trovati, ne elaboro %d (i più vecchi); il resto alla prossima scansione",
                     repo, len(commits), max_commits)
            commits = commits[:max_commits]
        return commits

    def commit_detail(self, repo: str, sha: str, max_pages: int = 10) -> dict[str, Any] | None:
        """Dettaglio del commit con TUTTI i file (GitHub li pagina a 300 per pagina, massimo 3000)."""
        first: dict[str, Any] | None = None
        files: list[dict[str, Any]] = []
        for page in range(1, max_pages + 1):
            data = self.get_json(f"/repos/{repo}/commits/{sha}", {"per_page": FILES_PER_PAGE, "page": page})
            if data is None:
                break
            if first is None:
                first = data
            batch = data.get("files") or []
            files.extend(batch)
            if len(batch) < FILES_PER_PAGE:
                break
            if page == max_pages:
                log.warning("%s %s: più di %d file, i rimanenti non sono esposti dall'API",
                            repo, sha[:10], max_pages * FILES_PER_PAGE)
        if first is not None:
            first["files"] = files
        return first

    def raw_file(self, repo: str, sha: str, path: str, max_bytes: int) -> str | None:
        """Contenuto del file a quel commit. '' se non esiste, None se supera max_bytes."""
        url = f"{self.raw_url}/{repo}/{sha}/{quote(path)}"
        response = self._request(url, stream=True)
        if response is None:
            return ""
        try:
            length = response.headers.get("Content-Length")
            if length and length.isdigit() and int(length) > max_bytes:
                return None
            chunks: list[bytes] = []
            size = 0
            for chunk in response.iter_content(65536):
                size += len(chunk)
                if size > max_bytes:
                    return None
                chunks.append(chunk)
            return b"".join(chunks).decode("utf-8", "replace")
        finally:
            response.close()


class GitHubTrackingSource:
    name = "github"

    def __init__(self, cfg: Config, db: Database):
        self.cfg = cfg
        self.db = db
        self.workers = max(1, int(cfg.get("github.fallback_workers", 8)))
        self.client = GitHubClient(
            cfg.get("github.api_url", "https://api.github.com"),
            cfg.get("github.raw_url", "https://raw.githubusercontent.com"),
            cfg.github_token,
            pool_size=self.workers,
        )
        self.text_ext = tuple(str(e).lower() for e in (cfg.get("github.text_extensions", []) or []))
        self.ignore_patterns = [re.compile(p) for p in (cfg.get("github.ignore_path_patterns", []) or [])]
        self.codenames = CodenameTracker(cfg, db)
        self.localization = LocalizationTracker(cfg, db)
        rcfg = cfg.get("renames", {}) or {}
        self.renames_enabled = bool(rcfg.get("enabled", True))
        self.rename_score = int(rcfg.get("score", 100))
        self._repo_by_name = {r["name"]: r for r in tracked_repos(cfg, db)}
        self.watched = [(re.compile(str(w["pattern"]), re.IGNORECASE), str(w.get("title", w["pattern"])),
                         int(w.get("score", 50)), [re.compile(str(x)) for x in (w.get("ignore") or [])])
                        for w in (cfg.get("watched_files", []) or []) if w.get("pattern")]
        self.context_lines = int(cfg.get("general.context_lines", 2))
        self.max_findings = int(cfg.get("general.max_findings_per_file", 400))
        self.fallback_max = int(cfg.get("github.fallback_max_bytes", 25 * 1024 * 1024))
        self.max_pages = int(cfg.get("github.max_file_pages", 10))
        if not cfg.github_token:
            log.warning("GITHUB_TOKEN assente: limite di 60 richieste/ora (metti un token in .env per 5000/ora)")

    # ----------------------------------------------------------------- run
    def run(self, *, since: datetime | None = None, repos: list[str] | None = None,
            max_commits: int | None = None) -> list[Finding]:
        findings: list[Finding] = []
        self.errors: list[str] = []
        for repo_cfg in tracked_repos(self.cfg, self.db):
            if repos and repo_cfg["name"] not in repos:
                continue
            try:
                findings += self.scan_repo(repo_cfg, since=since, max_commits=max_commits)
            except Exception as exc:
                log.exception("Sorgente GitHub: errore su %s, continuo con gli altri repository", repo_cfg.get("repo"))
                self.errors.append(f"{repo_cfg.get('repo')}: {exc}")
        return findings

    def scan_repo(self, repo_cfg: dict[str, Any], *, since: datetime | None = None,
                  max_commits: int | None = None) -> list[Finding]:
        name, repo = repo_cfg["name"], repo_cfg["repo"]
        branch = repo_cfg.get("branch", "master")
        key_sha, key_date = f"github:{name}:last_sha", f"github:{name}:last_date"
        last_sha = self.db.get_state(key_sha)
        last_date = parse_iso(self.db.get_state(key_date))

        stop_at: str | None = None
        if since is None:
            if last_sha:
                stop_at = last_sha
                since = (last_date - timedelta(days=2)) if last_date else None
            elif repo_cfg.get("since") and parse_iso(repo_cfg["since"]):
                since = parse_iso(repo_cfg["since"]) - timedelta(days=1)    # repository scoperto: dalla sua nascita
            else:
                since = utcnow() - timedelta(days=int(self.cfg.get("github.initial_days", 7)))
        if max_commits is None:
            max_commits = int(self.cfg.get("github.max_commits_per_run", 50))

        log.info("[%s] cerco commit in %s@%s a partire da %s", name, repo, branch,
                 since.strftime("%Y-%m-%d") if since else "cursore")
        commits = self.client.list_commits(repo, branch, since=since, stop_at_sha=stop_at, max_commits=max_commits)
        if not commits:
            log.info("[%s] nessun commit nuovo", name)
            return []
        log.info("[%s] %d commit da analizzare", name, len(commits))

        matcher = KeywordMatcher(self.cfg.keywords, ignore_ids=repo_cfg.get("ignore_keywords", []) or [])
        findings: list[Finding] = []
        for commit in commits:
            sha = commit["sha"]
            date = parse_iso(((commit.get("commit") or {}).get("committer") or {}).get("date")) or utcnow()
            if self.db.build_exists(name, "github", sha):
                self._advance(key_sha, key_date, sha, date)
                continue
            detail = self.client.commit_detail(repo, sha, self.max_pages)
            if detail is None:
                log.warning("[%s] commit %s non più disponibile", name, sha[:10])
                continue
            message = ((detail.get("commit") or {}).get("message") or "").strip()
            m = BUILDID_RE.search(message)
            build = BuildInfo(
                game=name, source="github", ref=sha, date=date, url=detail.get("html_url"),
                message=message[:500], steam_buildid=m.group(1) if m else None,
                files_changed=len(detail.get("files") or []),
            )
            build_id = self.db.upsert_build(build)
            commit_findings = self._analyze_commit(repo_cfg, detail, matcher)
            for finding in commit_findings:
                finding.build = build
                self.db.add_finding(finding, build_id)
            n_new = sum(1 for f in commit_findings if f.is_new)
            log.info("[%s] %s %s: %d file, %d finding (%d nuovi) - %s", name, sha[:10],
                     date.strftime("%Y-%m-%d"), build.files_changed, len(commit_findings), n_new,
                     message.splitlines()[0][:80] if message else "")
            findings += commit_findings
            self._advance(key_sha, key_date, sha, date)
        return findings

    def _advance(self, key_sha: str, key_date: str, sha: str, date: datetime) -> None:
        self.db.set_state(key_sha, sha)
        self.db.set_state(key_date, iso(date))

    # ------------------------------------------------------------- analisi
    def _ignored(self, path: str) -> bool:
        return any(p.search(path) for p in self.ignore_patterns)

    def _is_text(self, path: str) -> bool:
        return path.lower().endswith(self.text_ext)

    def _analyze_commit(self, repo_cfg: dict[str, Any], detail: dict[str, Any],
                        matcher: KeywordMatcher) -> list[Finding]:
        name, repo, sha = repo_cfg["name"], repo_cfg["repo"], detail["sha"]
        parents = detail.get("parents") or []
        parent_sha = parents[0]["sha"] if parents else None
        findings: list[Finding] = []
        fallback_entries: list[dict[str, Any]] = []
        for entry in detail.get("files") or []:
            path = entry.get("filename") or ""
            status = entry.get("status", "modified")
            if not path or self._ignored(path):
                continue

            # 1) il percorso stesso (file nuovi/rimossi/rinominati con "hlx" nel nome)
            if status in ("added", "removed", "renamed"):
                change_type = f"file_{status}"
                res = matcher.analyze_line(path, file_path=path, change_type=change_type)
                if res:
                    previous = entry.get("previous_filename")
                    findings.append(Finding(
                        game=name, source="github", file_path=path, change_type=change_type, text=path,
                        score=res.score, keywords=res.keywords, token=res.token,
                        context=f"prima: {previous}" if previous else "",
                    ))

            # 2) il contenuto
            patch = entry.get("patch")
            if patch:
                lines = [(dl.op, dl.text) for hunk in parse_patch(patch) for dl in hunk if dl.op in "+-"]
                findings += self._extra_findings(name, path, lines, matcher, status=status)
            if patch and repo_cfg.get("build_from_steam_inf") and path.endswith("steam.inf"):
                info = steam_inf_build(patch)
                if info:
                    findings.append(self._steam_inf_finding(repo_cfg, path, info))
            if patch:
                findings += analyze_patch(patch, game=name, source="github", file_path=path, matcher=matcher,
                                          context_lines=self.context_lines, max_findings=self.max_findings)
            elif self._is_text(path) and (entry.get("additions") or entry.get("deletions")
                                          or status in ("added", "removed")):
                fallback_entries.append(entry)

        # I file senza patch richiedono due download ciascuno: nei commit da migliaia di file
        # farli in sequenza costava minuti, quindi vanno in parallelo.
        if fallback_entries:
            started = time.time()
            if len(fallback_entries) > 20:
                log.info("[%s] %s: %d file senza patch, diff manuale con %d download paralleli",
                         name, sha[:10], len(fallback_entries), self.workers)
            with ThreadPoolExecutor(max_workers=self.workers) as pool:
                futures = [pool.submit(self._fallback_diff, name, repo, sha, parent_sha, entry, matcher)
                           for entry in fallback_entries]
                for entry, future in zip(fallback_entries, futures):
                    try:
                        findings += future.result()
                    except Exception:
                        log.exception("[%s] diff manuale fallito su %s", name, entry.get("filename"))
            if len(fallback_entries) > 20:
                log.info("[%s] %s: diff manuale completato in %.0fs", name, sha[:10], time.time() - started)
        return findings

    def _extra_findings(self, name: str, path: str, lines: list[tuple[str, str]],
                        matcher: KeywordMatcher, status: str = "modified") -> list[Finding]:
        """Oltre alle parole chiave: nomi di progetto mai visti, famiglie di token di localizzazione nuove
        e ogni riga cambiata nei file sorvegliati."""
        out: list[Finding] = self.localization.check_file(
            name, path, (t for op, t in lines if op == "+"), file_added=(status == "added"),
            score_override=(self._repo_by_name.get(name) or {}).get("loc_prefix_score"))
        if self.renames_enabled and not path.endswith(".stringsignore"):
            out += self._rename_findings(name, path, lines)
        watched = next(((title, score, ignore) for regex, title, score, ignore in self.watched if regex.search(path)), None)
        shown = 0
        if watched:
            plus = {format_key(t) for op, t in lines if op == "+"}
            minus = {format_key(t) for op, t in lines if op == "-"}
        for op, text in lines:
            stripped = text.strip()
            if not stripped:
                continue
            change_type = "added" if op == "+" else "removed"
            if op == "+":
                f = self.codenames.check(text, path, game=name, change_type=change_type)
                if f:
                    out.append(f)
            if watched and (format_key(text) in (minus if op == "+" else plus)
                            or any(x.search(stripped) for x in watched[2])):
                continue                          # riga solo spostata o riformattata
            if watched and shown < 50 and not TRIVIAL_LINE.fullmatch(stripped) \
                    and matcher.analyze_line(text, file_path=path, change_type=change_type) is None:
                out.append(Finding(game=name, source="github", file_path=path, change_type=change_type,
                                   text=stripped[:500], score=watched[1], keywords=["watched_file"],
                                   context=f"file sorvegliato: {watched[0]}"))
                shown += 1
        return out

    def _rename_findings(self, name: str, path: str, lines: list[tuple[str, str]]) -> list[Finding]:
        """Righe HLX tolte che ricompaiono nello stesso file con un altro prefisso (hlx.hrtf_basic -> core.hrtf_basic)."""
        removed = [t for op, t in lines if op == "-"]
        if not any("hlx" in t.lower() for t in removed):
            return []
        out: list[Finding] = []
        for old, new, prefix in find_renames(removed, (t for op, t in lines if op == "+")):
            where = (f"il prefisso hlx è diventato '{prefix}'" if prefix else "il prefisso hlx è stato tolto")
            log.warning("[%s] RINOMINA HLX in %s: %s -> %s", name, path, old[:80], new[:80])
            out.append(Finding(game=name, source="github", file_path=path, change_type="renamed",
                               text=new[:500], old_text=old[:500], score=self.rename_score,
                               keywords=["hlx_renamed"], token=f"rename:{old[:120].lower()}",
                               context=f"rinominata: {where}"))
        return out

    def _steam_inf_finding(self, repo_cfg: dict[str, Any], path: str, info: dict[str, str]) -> Finding:
        name = repo_cfg["name"]
        title = self.cfg.game_title(name)
        text = f"{title}: nuova build {info['version']}" + (f" (prima {info['previous']})" if info["previous"] else "")
        details = [f"revisione {info['revision']}" if info["revision"] else "", info["date"]]
        log.info("[%s] nuova build dal file steam.inf: %s", name, info["version"])
        return Finding(
            game=name, source="github", file_path=path, change_type="build", text=text,
            # punteggio per repository: Deadlock pubblica anche 3-5 build al giorno, sotto la soglia di notifica
            score=int(repo_cfg.get("build_score", self.cfg.get("steam.build_score", 55))), keywords=["steam_build"],
            token=f"build:{name}:{info['version']}", context=", ".join(d for d in details if d),
        )

    def _fallback_diff(self, name: str, repo: str, sha: str, parent_sha: str | None,
                       entry: dict[str, Any], matcher: KeywordMatcher) -> list[Finding]:
        """GitHub non ha fornito la patch (file troppo grande): scarico le due versioni e confronto le righe."""
        path = entry["filename"]
        status = entry.get("status")
        new_text = "" if status == "removed" else self.client.raw_file(repo, sha, path, self.fallback_max)
        old_path = entry.get("previous_filename") or path
        if status == "added" or not parent_sha:
            old_text: str | None = ""
        else:
            old_text = self.client.raw_file(repo, parent_sha, old_path, self.fallback_max)
        if new_text is None or old_text is None:
            log.info("[%s] salto %s: supera %d byte per il diff manuale", name, path, self.fallback_max)
            return []
        old_lines, new_lines = old_text.splitlines(), new_text.splitlines()
        log.debug("[%s] diff manuale di %s (%d -> %d righe)", name, path, len(old_lines), len(new_lines))
        old_set, new_set = set(old_lines), set(new_lines)
        extra = self._extra_findings(name, path, [("+", ln) for ln in new_lines if ln not in old_set]
                                     + [("-", ln) for ln in old_lines if ln not in new_set], matcher,
                                     status=status or "modified")
        return extra + analyze_line_sets(old_lines, new_lines, game=name, source="github", file_path=path,
                                 matcher=matcher, context_lines=self.context_lines, max_findings=self.max_findings)
