"""Read existing XML libraries without network requests or changing user files."""
import hashlib
import os
from pathlib import Path
import re
import xml.etree.ElementTree as ET

from library_store import existing_sidecar, state_dir
from xml_utils import inspect_xml, local, normalized_text

SKIP = {"fulltext_pdf", "review_required", "library-data", "indexes", "node_modules"}


def xml_files(root, checkpoint=lambda: None):
    root = Path(root)
    if not root.is_dir():
        return
    for folder, directories, files in os.walk(root, followlinks=False):
        checkpoint()
        directories[:] = sorted(d for d in directories if not d.startswith(".") and d not in SKIP
                                and not (Path(folder) / d).is_symlink()
                                and not (hasattr(Path, "is_junction") and (Path(folder) / d).is_junction()))
        for name in sorted(files):
            if name.lower().endswith(".xml"):
                path = Path(folder) / name
                if not path.is_symlink():
                    yield path


def read_xml(path):
    if path.stat().st_size > 128 * 1024 * 1024:
        raise ValueError("XML 文件超过 128 MB")
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    sidecar = existing_sidecar(path)
    if sidecar.exists() and sidecar.read_text(encoding="ascii").strip().lower() != digest:
        raise ValueError("XML 校验记录不匹配")
    try:
        root = ET.fromstring(data)
    except ET.ParseError as error:
        raise ValueError("XML 文件不完整") from error
    core = next((node for node in root.iter() if local(node) == "coredata"), None)
    if core is None:
        raise ValueError("缺少论文身份信息")
    def values(tag):
        return [normalized_text(node) for node in core.iter() if local(node) == tag]
    dois = values("doi")
    doi = dois[0].strip().lower() if dois else ""
    if not re.fullmatch(r"10\.\d{4,9}/\S+", doi):
        raise ValueError("缺少有效 DOI")
    result = inspect_xml(data, doi)
    if result["status"] not in ("fulltext_structured", "fulltext_rawtext"):
        raise ValueError("XML 未通过全文校验")
    dates = values("coverDate")
    year = next((int(part) for part in reversed(path.parts[:-1]) if re.fullmatch(r"\d{4}", part)), None)
    if year is None and dates and dates[0][:4].isdigit():
        year = int(dates[0][:4])
    return {**result, "doi": doi, "year": year, "path": str(path), "sha256": digest,
            "bytes": len(data), "issns": values("issn") + values("eIssn"),
            "journal": next(iter(values("publicationName")), "")}


def scan_existing(base, start=1800, end=2100, checkpoint=lambda: None):
    base = Path(base)
    root = base / "fulltext_xml" if (base / "fulltext_xml").is_dir() else base
    saved, invalid, outside, duplicates = {}, 0, 0, 0
    for path in xml_files(root, checkpoint):
        checkpoint()
        try:
            info = read_xml(path)
        except (OSError, ValueError, ET.ParseError):
            invalid += 1
            continue
        if info["year"] is not None and not start <= info["year"] <= end:
            outside += 1
            continue
        if info["doi"] in saved:
            duplicates += 1
            continue
        info["file"] = str(path.relative_to(base))
        saved[info["doi"]] = info
    return {"saved": saved, "invalid": invalid, "outside": outside, "duplicates": duplicates,
            "base": str(base)}


def normalize_root(root):
    root = Path(root).expanduser()
    if root.name == "fulltext_xml":
        return root.parent
    if root.parent.name == "fulltext_xml":
        return root.parent.parent
    return root


def identifies(base, issn, title):
    import json
    from journal_catalog import normalize
    for directory in (state_dir(base), base):
        for filename in ("journal.json", "index_summary.json"):
            try:
                item = json.loads((directory / filename).read_text(encoding="utf-8"))
                if item.get("issn"):
                    return item["issn"] == issn
            except (OSError, ValueError, AttributeError):
                pass
    if base.name == issn and (base / "fulltext_xml").is_dir():
        return True
    root = base / "fulltext_xml" if (base / "fulltext_xml").is_dir() else base
    # XML identifies a moved library even when its local state is unavailable.
    for index, path in enumerate(xml_files(root)):
        if index >= 8:
            break
        try:
            info = read_xml(path)
            if info["issns"] or info["journal"]:
                return issn in info["issns"] or (bool(title) and normalize(info["journal"]) == normalize(title))
        except (OSError, ValueError, ET.ParseError):
            continue
    return False


def detect_journal(root):
    """Infer only an explicitly selected library, never an aggregate download root."""
    import json
    from journal_catalog import lookup
    root = normalize_root(root)
    if not (root / "fulltext_xml").is_dir() and not any(root.glob("*.xml")):
        return None
    for directory in (state_dir(root), root):
        try:
            meta = json.loads((directory / "journal.json").read_text(encoding="utf-8"))
            item = lookup(str(meta.get("issn", "")).replace("-", ""), meta.get("name", ""))
            if item:
                return item
        except (OSError, ValueError, AttributeError):
            pass
    item = lookup(root.name.replace("-", ""), "")
    if item:
        return item
    found = {}
    for index, path in enumerate(xml_files(root / "fulltext_xml" if (root / "fulltext_xml").is_dir() else root)):
        if index >= 8:
            break
        try:
            info = read_xml(path)
            item = next((value for issn in info["issns"] if (value := lookup(issn.replace("-", ""), ""))), None)
            item = item or lookup("", info["journal"])
            if item:
                found[item["issn"]] = item
        except (OSError, ValueError):
            continue
    return next(iter(found.values())) if len(found) == 1 else None


def resolve_library(root, issn, title):
    root = normalize_root(root)
    if (root / issn).is_dir():
        return root / issn
    if (root / "fulltext_xml").is_dir() or any(root.glob("*.xml")):
        if identifies(root, issn, title):
            return root
    # Preserve legacy state-only library locations.
    if (state_dir(root) / "journal.json").exists() or (root / "journal.json").exists() or (root / "index_summary.json").exists():
        if identifies(root, issn, title):
            return root
    return root / issn
