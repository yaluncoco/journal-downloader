"""Read-only PDF integrity inventory, shared by resume and folder previews."""
import hashlib
import json
import os
from pathlib import Path
import re

from library_store import existing_sidecar


def read_pdf(path, expected=None):
    from pdf_export import validate_pdf
    meta = json.loads(existing_sidecar(path).read_text(encoding="utf-8"))
    if not isinstance(meta, dict) or not re.fullmatch(r"10\.\d{4,9}/\S+", str(meta.get("doi", ""))):
        raise ValueError("PDF 缺少可核验的 DOI 记录")
    if expected and any(meta.get(k) != v for k, v in expected.items()):
        raise ValueError("PDF 与本次输出设置或 XML 不一致")
    if path.stat().st_size > 512 * 1024 * 1024:
        raise ValueError("PDF 文件超过 512 MB")
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != meta.get("pdf_sha256"):
        raise ValueError("PDF 校验记录不匹配")
    if validate_pdf(data, meta["doi"]) != meta.get("pages"):
        raise ValueError("PDF 页数不一致")
    year = next((int(p) for p in reversed(path.parts[:-1]) if re.fullmatch(r"\d{4}", p)), meta.get("year"))
    return {**meta, "doi": meta["doi"].lower(), "path": str(path), "year": year}


def scan_pdfs(base, start=1800, end=2100, checkpoint=lambda: None):
    root = Path(base)
    root = root if root.name == "fulltext_pdf" else root / "fulltext_pdf"
    saved, invalid, unverified, invalid_dois = {}, 0, 0, set()
    if root.is_dir():
        for folder, directories, files in os.walk(root, followlinks=False):
            checkpoint()
            directories[:] = sorted(d for d in directories if not d.startswith(".")
                and not (Path(folder) / d).is_symlink()
                and not (hasattr(Path, "is_junction") and (Path(folder) / d).is_junction()))
            for name in sorted(files):
                path = Path(folder) / name
                if path.suffix.lower() != ".pdf" or path.is_symlink():
                    continue
                checkpoint()
                folder_year = next((int(p) for p in reversed(path.relative_to(root).parts[:-1]) if re.fullmatch(r"\d{4}", p)), None)
                if folder_year is not None and not start <= folder_year <= end:
                    continue
                if not existing_sidecar(path).exists():
                    unverified += 1
                    continue
                try:
                    info = read_pdf(path)
                    if info["year"] is not None and not start <= info["year"] <= end:
                        continue
                    saved.setdefault(info["doi"], info)
                except (ValueError, OSError, TypeError, KeyError):
                    invalid += 1
                    _pending_identity(path, invalid_dois)
                except Exception:
                    # Parser-specific failures are corrupt/unreadable PDFs too.
                    invalid += 1
                    _pending_identity(path, invalid_dois)
    return {"saved": saved, "invalid": invalid, "unverified": unverified, "invalid_dois": invalid_dois}


def _pending_identity(path, identifiers):
    # An untrusted sidecar may deduplicate pending counts, never certify a PDF.
    try:
        doi = json.loads(existing_sidecar(path).read_text(encoding="utf-8")).get("doi", "").lower()
        if re.fullmatch(r"10\.\d{4,9}/\S+", doi):
            identifiers.add(doi)
    except (ValueError, OSError, AttributeError):
        pass
