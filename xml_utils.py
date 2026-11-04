"""XML validation and atomic storage, reused from the verified CLI downloader."""


import re
import hashlib
import xml.etree.ElementTree as ET


def local(node):
    return node.tag.rsplit("}", 1)[-1]


def normalized_text(node):
    return re.sub(r"\s+", " ", "".join(node.itertext())).strip()


def inspect_xml(data, expected_doi):
    """Recognize structured body and Elsevier's explicit rawtext representation."""
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return {"status": "invalid_xml"}
    if any(local(node) in ("service-error", "error") for node in root.iter()):
        return {"status": "api_error", "message": normalized_text(root)[:500],
                "api_code": next((normalized_text(n) for n in root.iter() if local(n) == "statusCode"), "")}
    core = next((node for node in root.iter() if local(node) == "coredata"), None)
    actual_dois = ["".join(node.itertext()).strip().lower() for node in core.iter()
                   if local(node) == "doi"] if core is not None else []
    if actual_dois and expected_doi.lower() not in actual_dois:
        return {"status": "doi_mismatch", "returned_dois": actual_dois}
    details = {"document_subtype": next((normalized_text(n) for n in root.iter()
                                         if local(n) == "document-subtype"), ""),
               "title": next((normalized_text(n) for n in core.iter() if local(n) == "title"), "")
               if core is not None else ""}
    details["identity_verified"] = bool(actual_dois)
    details["is_editorial_board"] = (details["document_subtype"] == "edb" or
                                      details["title"].strip().lower() == "editorial board")
    original = [node for node in root.iter() if local(node) == "originalText"]
    has_unclassified_text = False
    for text_node in original:
        bodies = [node for node in text_node.iter() if local(node) == "body"]
        body_text = "\n".join(normalized_text(node) for node in bodies).strip()
        if body_text:
            if not actual_dois:
                return {"status": "needs_review_identity", "reason": "No DOI in coredata", **details}
            return {"status": "fulltext_structured", "body_characters": len(body_text), **details}
        raw_text = "\n".join(normalized_text(n) for n in text_node.iter() if local(n) == "rawtext").strip()
        if raw_text:
            if not actual_dois:
                return {"status": "needs_review_identity", "reason": "No DOI in coredata", **details}
            return {"status": "fulltext_rawtext", "body_characters": len(raw_text), **details}
        has_unclassified_text |= bool(normalized_text(text_node))
    if has_unclassified_text:
        return {"status": "needs_review_unstructured", "reason": "No explicit body or rawtext element", **details}
    return {"status": "no_fulltext", "reason": "No article body in originalText", **details}


def plain_text(data, doi):
    """A reading aid; XML remains authoritative for equations, tables and markup."""
    root = ET.fromstring(data)
    blocks = [f"DOI: {doi}"]
    core = next((n for n in root.iter() if local(n) == "coredata"), None)
    if core is not None:
        blocks += [normalized_text(n) for n in core if local(n) in ("title", "creator", "description")]

    block_tags = {"section-title", "title", "para", "simple-para", "label", "bib-reference", "table", "display"}

    def walk(node):
        if local(node) in block_tags:
            value = normalized_text(node)
            if value:
                blocks.append(value)
        elif len(node):
            for child in node:
                walk(child)
        else:
            value = normalized_text(node)
            if value:
                blocks.append(value)

    for original in (n for n in root.iter() if local(n) == "originalText"):
        bodies = [n for n in original.iter() if local(n) == "body" and normalized_text(n)]
        if bodies:
            for body in bodies:
                walk(body)
            for tail in original.iter():
                if local(tail) == "tail":
                    walk(tail)
        else:
            blocks += [normalized_text(n) for n in original.iter() if local(n) == "rawtext"]
    return "\n\n".join(block for block in blocks if block) + "\n"


def atomic_bytes(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".part")
    temporary.write_bytes(data)
    temporary.replace(path)


def save_fulltext(target, txt, data, doi):
    from library_store import sidecar_path
    atomic_bytes(target, data)
    digest = hashlib.sha256(data).hexdigest()
    atomic_bytes(sidecar_path(target), (digest + "\n").encode("ascii"))
    return digest
