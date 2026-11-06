"""Reproducible enrichment: official Elsevier subjects + pinned CAS 2025 reference.

python build_metadata.py --source-dir qa/research-v150
The reference table is kept separate from executable code; see DATA_SOURCES.md.
"""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import re

from build_catalog import rows, SOURCE_ROOT, SOURCE_PAGE

COMMIT = "4687a8f608f20aaf004299992f5d347dba009361"
CAS_SHA = "481224dca2cacc1cce49afb44c968d734c834e498e5ff054f15efb5a548789fc"
CAS_URL = f"https://github.com/hitfyd/ShowJCR/blob/{COMMIT}/中科院分区表及JCR原始数据文件/FQBJCR2025-UTF8.csv"


def zone(value):
    match = re.fullmatch(r"([1-4])(?:\s*\[([^\]]+)\])?", value.strip())
    return {"zone": int(match[1]), "rank": match[2] or ""} if match else None


def build(source, output):
    titles = json.loads(Path(__file__).with_name("journals.json").read_text(encoding="utf-8"))["journals"]
    entries = {item["issn"]: {"subjects": []} for item in titles}
    subject_path = source / "subjects.xlsx"
    for row in rows(subject_path):
        entry = entries.get(row.get("ISSN", "").upper())
        subject = {"primary": row.get("Primary Level", ""), "secondary": row.get("Secondary Level", "")}
        if entry is not None and subject["primary"] and subject not in entry["subjects"]:
            entry["subjects"].append(subject)
    cas_path = source / "cas2025.csv"
    if hashlib.sha256(cas_path.read_bytes()).hexdigest() != CAS_SHA:
        raise ValueError("CAS reference does not match the pinned source checksum")
    by_issn = {}
    with cas_path.open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            if row.get("年份") != "2025" or not (major := zone(row.get("大类分区", ""))):
                continue
            identifiers = re.findall(r"\d{4}-\d{3}[\dX]", row.get("ISSN/EISSN", "").upper())
            item = {"year": 2025, "major": row.get("大类", ""), **major,
                    "top": row.get("Top") == "是", "index": row.get("Web of Science", ""),
                    "minor": [], "issns": identifiers}
            for i in range(1, 7):
                if (name := row.get(f"小类{i}")) and (z := zone(row.get(f"小类{i}分区", ""))):
                    item["minor"].append({"name": name, **z})
            for issn in identifiers:
                matches = by_issn.setdefault(issn, [])
                if item not in matches:
                    matches.append(item)
    conflicts = []
    for issn, entry in entries.items():
        matches = by_issn.get(issn, [])
        if len(matches) == 1:
            entry["cas2025"] = matches[0]
        elif matches:
            conflicts.append(issn)
    result = {"schema": 1, "retrieved_at": "2026-11-06", "subjects_source": {
        "url": SOURCE_ROOT + "jnlsubject.xlsx", "page": SOURCE_PAGE,
        "sha256": hashlib.sha256(subject_path.read_bytes()).hexdigest()},
        "cas_source": {"url": CAS_URL, "commit": COMMIT, "sha256": CAS_SHA,
                       "license": "GPL-3.0", "verified_officially": False, "year": 2025},
        "conflicts": conflicts, "journals": entries}
    output.write_text(json.dumps(result, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    print(json.dumps({"subjects": sum(bool(e["subjects"]) for e in entries.values()),
                      "cas2025": sum("cas2025" in e for e in entries.values()), "conflicts": conflicts}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path(__file__).with_name("journal_metadata.json"))
    args = parser.parse_args()
    build(args.source_dir, args.output)
