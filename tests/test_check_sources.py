"""Tests de la vérification hebdomadaire des PDF sources (sans réseau)."""

import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import check_sources as cs  # noqa: E402

SEASON_DIR = ROOT / "sources" / "2026-2027"
PDF_A, PDF_B = sorted(SEASON_DIR.glob("*.pdf"))[:2]


def test_detect_season() -> None:
    assert cs.detect_season(PDF_A.read_bytes()) == "2026-2027"


def _fake_site(monkeypatch, pdfs: dict[str, bytes]) -> None:
    """Page avec un lien par PDF ; la liseuse renvoie vers le PDF du même nom."""
    page = "".join(f'<a href="https://heyzine.com/flip-book/{n}.html">Zone {n}</a>' for n in pdfs)

    def fetch(url: str) -> bytes:
        if url == cs.PAGE_URL:
            return page.encode()
        return pdfs[url.rsplit("/", 1)[1].removesuffix(".pdf")]

    monkeypatch.setattr(cs, "fetch", fetch)
    monkeypatch.setattr(cs, "pdf_url", lambda viewer: f"https://cdn.heyzine.com/files/uploaded/{viewer.rsplit('/', 1)[1].removesuffix('.html')}.pdf")


def test_detects_new_and_modified(monkeypatch, tmp_path) -> None:
    season = tmp_path / "2026-2027"
    season.mkdir()
    shutil.copy(PDF_A, season / "aaa.pdf")

    # aaa inchangé, bbb nouveau
    _fake_site(monkeypatch, {"aaa": PDF_A.read_bytes(), "bbb": PDF_B.read_bytes()})
    results, errors = cs.check(tmp_path, dry_run=False)
    assert not errors
    assert {r.file: r.status for r in results} == {"aaa.pdf": "inchangé", "bbb.pdf": "nouveau"}
    assert (season / "bbb.pdf").read_bytes() == PDF_B.read_bytes()
    manifest = json.loads((season / "manifest.json").read_text())
    assert manifest["bbb.pdf"]["sha256"] == cs.sha256(PDF_B.read_bytes())

    # aaa remplacé par un autre contenu : modifié, mais rien d'écrit en dry-run
    _fake_site(monkeypatch, {"aaa": PDF_B.read_bytes(), "bbb": PDF_B.read_bytes()})
    results, _ = cs.check(tmp_path, dry_run=True)
    assert {r.file: r.status for r in results} == {"aaa.pdf": "modifié", "bbb.pdf": "inchangé"}
    assert (season / "aaa.pdf").read_bytes() == PDF_A.read_bytes()
