"""Offline journal subjects and explicitly non-authoritative CAS references."""
from functools import lru_cache
import json
from pathlib import Path
import sys

SUBJECTS = {
    "农学与生物科学": "Agricultural and Biological Sciences", "计算机": "Computer Science",
    "环境科学": "Environmental Science", "地球与行星科学": "Earth and Planetary Sciences",
    "工程": "Engineering", "材料科学": "Materials Science", "能源": "Energy",
    "数学": "Mathematics", "物理与天文学": "Physics and Astronomy", "化学": "Chemistry",
    "化学工程": "Chemical Engineering", "生物化学与分子生物学": "Biochemistry, Genetics and Molecular Biology",
    "医学与牙科": "Medicine and Dentistry", "药理毒理与药学": "Pharmacology, Toxicology and Pharmaceutical Science",
    "免疫与微生物": "Immunology and Microbiology", "神经科学": "Neuroscience",
    "护理与卫生": "Nursing and Health Professions", "兽医学": "Veterinary Science and Veterinary Medicine",
    "心理学": "Psychology", "社会科学": "Social Sciences", "艺术与人文": "Arts and Humanities",
    "经济与金融": "Economics, Econometrics and Finance", "商业与管理": "Business, Management and Accounting",
    "决策科学": "Decision Sciences",
}
CATEGORY_OPTIONS = ["全部学科", "农学与生物科学", "林学", "计算机", "农学 × 计算机", "林学 × 计算机",
                    *[name for name in SUBJECTS if name not in ("农学与生物科学", "计算机")], "分类未收录"]
ZONE_OPTIONS = ["全部分区", "1区", "2区", "3区", "4区", "未收录"]


@lru_cache(maxsize=1)
def metadata():
    path = Path(getattr(sys, "_MEIPASS", Path(__file__).parent)) / "journal_metadata.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema") != 1:
        raise ValueError("期刊分类数据格式不正确")
    return data


def details(item):
    return metadata()["journals"].get(item["issn"], {})


def category_matches(item, category):
    subjects = details(item).get("subjects", [])
    primary = {s["primary"] for s in subjects}
    forestry = any(s["secondary"] == "Forestry" for s in subjects)
    if category == "全部学科":
        return True
    if category == "分类未收录":
        return not subjects
    if category == "林学":
        return forestry
    if category == "农学 × 计算机":
        return SUBJECTS["农学与生物科学"] in primary and "Computer Science" in primary
    if category == "林学 × 计算机":
        return forestry and "Computer Science" in primary
    return SUBJECTS.get(category) in primary


def reference_matches(item, zone="全部分区", top=False):
    cas = details(item).get("cas2025", {})
    if top and not cas.get("top"):
        return False
    if zone == "未收录":
        return not cas
    return zone == "全部分区" or zone == f"{cas.get('zone')}区"


def reference_label(item):
    cas = details(item).get("cas2025")
    if not cas:
        return "2025 中科院参考 · 未收录"
    rank = f" · {cas['rank']}" if cas.get("rank") else ""
    return f"2025 中科院参考 · {cas['major']} {cas['zone']}区{rank}" + (" · Top" if cas["top"] else "") + f" · {cas['index']}"


def subject_label(item):
    primary = {s["primary"] for s in details(item).get("subjects", [])}
    labels = [name for name, value in SUBJECTS.items() if value in primary]
    if category_matches(item, "林学"):
        labels.insert(0, "林学")
    return " / ".join(labels) if labels else "官方学科未收录"
