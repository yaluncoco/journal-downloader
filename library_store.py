"""Keep internal state outside paper folders; migrate only known legacy files."""
from pathlib import Path
import hashlib
import os
import shutil

STATE_HOME = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "JournalXMLDownloader" / "library-data"


def path_key(path):
    return hashlib.sha256(os.path.normcase(str(Path(path).resolve())).encode("utf-8")).hexdigest()[:32]


def state_dir(library):
    return STATE_HOME / "libraries" / path_key(library)


def sidecar_path(paper):
    paper = Path(paper)
    key = path_key(paper)
    return STATE_HOME / "integrity" / key[:2] / (key + (".sha256" if paper.suffix.lower() == ".xml" else ".json"))


def existing_sidecar(paper):
    current = sidecar_path(paper)
    legacy = Path(paper).with_suffix(Path(paper).suffix + (".sha256" if Path(paper).suffix.lower() == ".xml" else ".json"))
    return current if current.exists() else legacy


def _checked_move(source, target, root):
    """Relocate known data without following links or overwriting conflicts."""
    source, target, root = Path(source), Path(target), Path(root).resolve()
    if not source.exists():
        return
    if not source.resolve().is_relative_to(root) or not target.resolve().is_relative_to(STATE_HOME.resolve()):
        raise OSError("文库整理路径越界，已保留原文件。")
    for part in (source, *source.parents):
        if part == root:
            break
        if part.is_symlink() or (hasattr(part, "is_junction") and part.is_junction()):
            raise OSError("文库辅助目录包含链接，已保留原文件。")
    if source.is_dir():
        for child in list(source.iterdir()):
            _checked_move(child, target / child.name, root)
        source.rmdir()  # Remove only the now-empty, known auxiliary directory.
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        incoming = hashlib.sha256(source.read_bytes()).hexdigest()
        if incoming == hashlib.sha256(target.read_bytes()).hexdigest():
            source.unlink()
            return
        # Preserve a different legacy file for recovery, keeping current state intact.
        target = state_dir(root) / "migration-conflicts" / (path_key(source)[:16] + "-" + incoming[:16] + source.suffix)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if hashlib.sha256(target.read_bytes()).hexdigest() != incoming:
                raise OSError("辅助文件冲突，已保留原文件。")
            source.unlink()
            return
    shutil.move(str(source), str(target))


def migrate_xml_checks(xml_root):
    root = Path(xml_root).resolve()
    if not root.is_dir():
        return
    for source in root.rglob("*.xml.sha256"):
        _checked_move(source, sidecar_path(source.with_suffix("")), root)


def migrate_library(library):
    root = Path(library).resolve()
    state = state_dir(root)
    state.mkdir(parents=True, exist_ok=True)
    for name in ("journal.json", "index_summary.json", "manifest.jsonl", "pending.jsonl",
                 "download_log.jsonl", "download_summary.json", "server_pause.json",
                 "download_progress.json", "indexes", "review_required"):
        _checked_move(root / name, state / name, root)
    _checked_move(root / "fulltext_txt", state / "legacy" / "fulltext_txt", root)
    migrate_xml_checks(root / "fulltext_xml")


def migrate_pdf(output):
    root = Path(output).resolve()
    state = state_dir(root)
    state.mkdir(parents=True, exist_ok=True)
    for name in ("pdf_log.jsonl", "pdf_pending.jsonl", "pdf_summary.json", "offline_export_result.json"):
        _checked_move(root / name, state / name, root)
    for name, destination in ((".image-cache", "image-cache"), (".work", "legacy-work")):
        _checked_move(root / name, state / destination, root)
    for source in root.rglob("*.pdf.json"):
        _checked_move(source, sidecar_path(source.with_suffix("")), root)
