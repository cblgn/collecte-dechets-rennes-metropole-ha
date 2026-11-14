"""Vérifie que les PDF publiés par la métropole sont toujours ceux de ``sources/``.

Usage :
    python tools/check_sources.py                 # met à jour sources/ si besoin
    python tools/check_sources.py --dry-run       # signale sans rien écrire
    python tools/check_sources.py --report r.md   # écrit aussi un résumé Markdown

Pour chaque calendrier de la page, on télécharge le PDF, on calcule son
sha256 et on le compare au manifeste ``sources/<saison>/manifest.json``. La
saison est lue dans le PDF (premier mois « OCTOBRE 2026 » -> 2026-2027), donc
les calendriers de la nouvelle saison arrivent tout seuls dans leur dossier.

Un PDF nouveau ou modifié est écrit dans ``sources/<saison>/`` et le manifeste
est mis à jour. Il reste ensuite à lancer ``tools/build_calendar.py`` (le
workflow ``sources.yml`` le fait et ouvre une PR).

Codes retour : 0 = rien n'a changé, 10 = des PDF ont changé, 1 = erreur
(page illisible...). En CI, ``changed=true|false`` est aussi écrit dans
``$GITHUB_OUTPUT``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import pymupdf

from download_sources import PAGE_URL, calendar_links, fetch, pdf_url

ROOT = Path(__file__).resolve().parent.parent
SOURCES_DIR = ROOT / "sources"
EXIT_CHANGED = 10


@dataclass
class Result:
    status: str  # "nouveau", "modifié", "inchangé"
    season: str
    file: str
    label: str


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def detect_season(pdf: bytes) -> str:
    """Saison AAAA-AAAA d'après le premier en-tête « OCTOBRE AAAA » du PDF."""
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        text = doc[0].get_text()
    m = re.search(r"OCTOBRE\s+(\d{4})", text)
    if not m:
        raise ValueError("en-tête « OCTOBRE AAAA » introuvable")
    year = int(m.group(1))
    return f"{year}-{year + 1}"


def load_manifest(season_dir: Path) -> dict[str, dict]:
    """Manifeste existant, ou reconstitué à partir des PDF déjà présents."""
    path = season_dir / "manifest.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {p.name: {"sha256": sha256(p.read_bytes())} for p in sorted(season_dir.glob("*.pdf"))}


def save_manifest(season_dir: Path, manifest: dict[str, dict]) -> None:
    text = json.dumps(dict(sorted(manifest.items())), ensure_ascii=False, indent=2) + "\n"
    (season_dir / "manifest.json").write_text(text, encoding="utf-8")


def check(sources_dir: Path, dry_run: bool) -> tuple[list[Result], list[str]]:
    links = calendar_links(fetch(PAGE_URL).decode("utf-8", "ignore"))
    if not links:
        raise RuntimeError("aucun lien de calendrier trouvé : la page a peut-être changé")

    results: list[Result] = []
    errors: list[str] = []
    manifests: dict[str, dict[str, dict]] = {}
    for viewer_url, label in links.items():
        try:
            url = pdf_url(viewer_url)
            pdf = fetch(url)
            season = detect_season(pdf)
        except Exception as err:  # noqa: BLE001 - on continue avec les autres calendriers
            errors.append(f"{label} ({viewer_url}) : {err}")
            continue

        season_dir = sources_dir / season
        manifest = manifests.setdefault(season, load_manifest(season_dir) if season_dir.exists() else {})
        name = viewer_url.rsplit("/", 1)[1].removesuffix(".html") + ".pdf"
        digest = sha256(pdf)
        previous = manifest.get(name, {}).get("sha256")
        status = "inchangé" if previous == digest else ("modifié" if previous else "nouveau")
        results.append(Result(status, season, name, label))

        manifest[name] = {"label": label, "viewer_url": viewer_url, "pdf_url": url, "sha256": digest}
        if status != "inchangé" and not dry_run:
            season_dir.mkdir(parents=True, exist_ok=True)
            (season_dir / name).write_bytes(pdf)

    if not dry_run:
        for season, manifest in manifests.items():
            if (sources_dir / season).exists():
                save_manifest(sources_dir / season, manifest)
    return results, errors


def report(results: list[Result], errors: list[str]) -> str:
    changed = [r for r in results if r.status != "inchangé"]
    lines = ["# Vérification des calendriers sources\n"]
    lines.append(f"{len(results)} calendriers vérifiés, **{len(changed)} nouveau(x) ou modifié(s)**.\n")
    if changed:
        lines += ["| Statut | Saison | Fichier | Calendrier |", "|---|---|---|---|"]
        lines += [f"| {r.status} | {r.season} | `{r.file}` | {r.label} |" for r in changed]
        lines.append("")
    if errors:
        lines.append("## Erreurs\n")
        lines += [f"- {e}" for e in errors]
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sources", type=Path, default=SOURCES_DIR)
    parser.add_argument("--dry-run", action="store_true", help="signale les changements sans rien écrire")
    parser.add_argument("--report", type=Path, help="fichier Markdown de résumé")
    args = parser.parse_args()

    try:
        results, errors = check(args.sources, args.dry_run)
    except Exception as err:  # noqa: BLE001
        print(f"Erreur : {err}", file=sys.stderr)
        return 1

    text = report(results, errors)
    print(text)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(text, encoding="utf-8")

    changed = any(r.status != "inchangé" for r in results)
    if output := os.environ.get("GITHUB_OUTPUT"):
        with open(output, "a", encoding="utf-8") as f:
            f.write(f"changed={'true' if changed else 'false'}\n")
    if errors and not results:
        return 1
    return EXIT_CHANGED if changed else 0


if __name__ == "__main__":
    sys.exit(main())
