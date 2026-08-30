#!/usr/bin/env python3
"""Build a private, structured mechanics encyclopedia from a legal game copy.

The source mount is read-only. Output is bounded JSONL consumed directly by the
operator's Control Center; no extracted prose is included in project images or
the repository. Tutorials, strategy, scripts, scenarios, fiction, and guides
are deliberately excluded.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unicodedata

from entrypoint import validate_source


SOURCE_ROOT = Path(os.environ.get("SMACX_GAME_SOURCE", "/game-source"))
RULE_SOURCES = ("Manual.pdf", "helpx.txt", "conceptsx.txt")
FACTION_FILES = (
    "GAIANS.TXT", "hive.txt", "univ.txt", "MORGAN.TXT", "spartans.txt",
    "believe.txt", "PEACE.TXT", "cyborg.txt", "pirates.txt", "drone.txt",
    "angels.txt", "fungboy.txt", "caretake.txt", "usurper.txt",
)
MAX_SOURCE_BYTES = 16 * 1024 * 1024
MAX_DOCUMENTS = 900
CHUNK_WORDS = 300
CHUNK_OVERLAP = 24
CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
WORDS = re.compile(r"\S+")


# section: topic, entity kind, readable field names. The first value is the
# display name except where the section itself defines an explicit native key.
SECTION_SPECS: dict[str, tuple[str, str, tuple[str, ...]]] = {
    "TERRAIN": ("terraforming", "terrain-order", (
        "name", "prerequisite", "sea_name", "sea_prerequisite", "build_turns",
        "order_text", "order_letter", "keystroke",
    )),
    "RESOURCEINFO": ("economy", "resource-rule", (
        "name", "nutrients", "minerals", "energy", "unused",
    )),
    "WORLDSIZE": ("setup", "world-size", ("name", "height", "width")),
    "TIMECONTROLS": ("multiplayer", "time-control", (
        "name", "turn_seconds", "base_seconds", "unit_seconds", "event_seconds",
        "extra_seconds", "refresh_turns", "maximum_bonuses",
    )),
    "TECHNOLOGY": ("research", "technology", (
        "name", "native_code", "military_weight", "research_weight",
        "infrastructure_weight", "colonization_weight", "prerequisite_1",
        "prerequisite_2", "flags",
    )),
    "CHASSIS": ("units", "chassis", (
        "name", "grammar_1", "alternate_name", "grammar_2", "defensive_name",
        "grammar_3", "alternate_defensive_name", "grammar_4", "speed", "triad",
        "range", "missile", "cargo", "cost", "prerequisite",
    )),
    "REACTORS": ("units", "reactor", (
        "name", "short_name", "power", "prerequisite",
    )),
    "WEAPONS": ("units", "weapon", (
        "name", "short_name", "offense", "mode", "cost", "icon", "prerequisite",
    )),
    "DEFENSES": ("units", "defense", (
        "name", "short_name", "strength", "mode", "cost", "prerequisite",
    )),
    "ABILITIES": ("units", "ability", (
        "name", "cost_rule", "prerequisite", "abbreviation", "allowed_flags", "effect",
    )),
    "MORALE": ("combat", "morale", ("human_name", "native_name")),
    "UNITS": ("units", "predefined-unit", (
        "name", "chassis", "weapon", "defense", "plan", "cost", "cargo",
        "prerequisite", "icon", "ability_flags",
    )),
    "FACILITIES": ("bases", "facility", (
        "name", "cost", "maintenance", "prerequisite", "obsolete_or_free", "effect",
        "ai_combat", "ai_military", "ai_research", "ai_infrastructure", "ai_colonize",
    )),
    "SOCIO": ("society", "social-model", (
        "name", "prerequisite", "effect_1", "effect_2", "effect_3", "effect_4",
    )),
    "SOCECONOMY": ("society", "social-rating-economy", ("rating", "effect")),
    "SOCEFFIC": ("society", "social-rating-efficiency", ("rating", "effect")),
    "SOCSUPPORT": ("society", "social-rating-support", ("rating", "effect")),
    "SOCTALENT": ("society", "social-rating-talent", ("rating", "effect")),
    "SOCMORALE": ("society", "social-rating-morale", ("rating", "effect")),
    "SOCPOLICE": ("society", "social-rating-police", ("rating", "effect")),
    "SOCGROWTH": ("society", "social-rating-growth", ("rating", "effect")),
    "SOCPLANET": ("society", "social-rating-planet", ("rating", "effect")),
    "SOCPROBE": ("society", "social-rating-probe", ("rating", "effect")),
    "SOCINDUSTRY": ("society", "social-rating-industry", ("rating", "effect")),
    "SOCRESEARCH": ("society", "social-rating-research", ("rating", "effect")),
    "DIFF": ("setup", "difficulty", ("name",)),
    "PROPOSALS": ("diplomacy", "council-proposal", (
        "name", "prerequisite", "effect",
    )),
}


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_source(path: Path, temporary: Path) -> str:
    if path.stat().st_size > MAX_SOURCE_BYTES:
        raise RuntimeError(f"reference_source_too_large:{path.name}")
    if path.suffix.casefold() != ".pdf":
        raw = path.read_bytes()
        for encoding in ("utf-8", "cp1252", "latin-1"):
            try:
                return raw.decode(encoding)
            except UnicodeDecodeError:
                continue
        raise RuntimeError(f"reference_source_encoding:{path.name}")
    output = temporary / "manual.txt"
    subprocess.run(
        ["pdftotext", "-enc", "UTF-8", "-layout", str(path), str(output)],
        check=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE, text=True,
    )
    return output.read_text(encoding="utf-8", errors="replace")


def normalize(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).replace("\r", "\n")
    value = CONTROL.sub("", value)
    value = re.sub(r"[ \t]+", " ", value)
    return re.sub(r"\n{3,}", "\n\n", value).strip()


def slug(value: str) -> str:
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")[:96] or "unknown"


def section_text(value: str, section: str) -> str:
    match = re.search(rf"(?m)^#{re.escape(section)}\s*$", value)
    if not match:
        return ""
    following = value[match.end():]
    boundary = re.search(r"(?m)^#(?!#)", following)
    return following[:boundary.start()] if boundary else following


def rows(value: str, section: str) -> list[list[str]]:
    result: list[list[str]] = []
    for raw in section_text(value, section).splitlines():
        line = raw.strip()
        if not line or line.startswith((";", "#")):
            continue
        parsed = [item.strip() for item in next(csv.reader([line], skipinitialspace=True))]
        while parsed and not parsed[-1]:
            parsed.pop()
        if len(parsed) >= (1 if section == "DIFF" else 2):
            result.append(parsed)
    return result


def relation(kind: str, key: str, relationship: str) -> dict[str, str]:
    return {"kind": kind, "key": key, "relationship": relationship}


def technology_relation(value: str, relationship: str = "requires") -> dict[str, str] | None:
    key = value.strip()
    if not key or key.casefold() in {"none", "disable", "deleted", "..."}:
        return None
    return relation("technology", slug(key), relationship)


def entity_from_row(
    source_name: str, source_hash: str, section: str, values: list[str], row_index: int,
) -> dict | None:
    topic, kind, names = SECTION_SPECS[section]
    if section == "SOCIO" and values[0] in {"ECONOMY", "Politics"}:
        return None
    if values[0].isdigit() and section in {"WORLDSIZE", "UNITS"}:
        return None
    title = values[0].lstrip("*").strip()
    if not title or title.casefold().startswith(("deleted", "empty facility")) \
            or title in {"...", "None"}:
        return None
    fields = {
        names[index] if index < len(names) else f"native_field_{index + 1}": value
        for index, value in enumerate(values)
    }
    key = slug(values[1]) if section == "TECHNOLOGY" else (
        f"rating-{values[0].strip()}" if section.startswith("SOC") and section != "SOCIO"
        else slug(title)
    )
    related: list[dict[str, str]] = []
    for field, value in fields.items():
        if "prerequisite" in field:
            item = technology_relation(value)
            if item:
                related.append(item)
    if section == "UNITS":
        related.extend((
            relation("chassis", slug(fields.get("chassis", "")), "uses"),
            relation("weapon", slug(fields.get("weapon", "")), "uses"),
            relation("defense", slug(fields.get("defense", "")), "uses"),
        ))
    body_lines = [f"# {title}", "", f"- Entity type: `{kind}`", f"- Native key: `{key}`"]
    for field, value in fields.items():
        body_lines.append(f"- {field.replace('_', ' ').title()}: {value}")
    return {
        "type": "document", "document_kind": "entity", "source_name": source_name,
        "source_sha256": source_hash, "topic": topic, "ruleset_id": "smacx",
        "source_priority": 300, "entity_kind": kind, "entity_key": key,
        "title": title,
        "summary": f"Exact Alien Crossfire {kind.replace('-', ' ')} record from the operator's installation.",
        "body": "\n".join(body_lines),
        "metadata": {"section": section, "row_index": row_index, "fields": fields,
                     "aliases": sorted({title, key}), "related": related},
    }


def faction_entity(path: Path, value: str) -> dict | None:
    selected = []
    for line in value.splitlines():
        if line.strip().upper() == "#BASES":
            break
        if line.strip() and not line.lstrip().startswith(";"):
            selected.append(line.strip())
    data = [line for line in selected if not line.startswith("#")]
    if not data:
        return None
    identity = next(csv.reader([data[0]], skipinitialspace=True))
    title = identity[0].strip() if identity else path.stem
    key = slug(path.stem)
    mechanical = data[:4]
    related = []
    for line in mechanical[1:]:
        tokens = [item.strip() for item in next(csv.reader([line], skipinitialspace=True))]
        for index, token in enumerate(tokens):
            if token.upper() == "TECH" and index + 1 < len(tokens):
                item = technology_relation(tokens[index + 1], "starts-with")
                if item:
                    related.append(item)
    return {
        "type": "document", "document_kind": "entity", "source_name": path.name,
        "source_sha256": file_sha256(path), "topic": "factions", "ruleset_id": "smacx",
        "source_priority": 300, "entity_kind": "faction", "entity_key": key,
        "title": title, "summary": "Exact mechanical faction header from the operator's installation.",
        "body": f"# {title}\n\n" + "\n".join(f"- Native record: {line}" for line in mechanical),
        "metadata": {"aliases": sorted({title, path.stem, key}), "related": related},
    }


def mechanics_only(name: str, value: str) -> str:
    if name.casefold() == "manual.pdf":
        rules_start = value.find("CHAPTER 3", 1000)
        notes_start = value.find("NOTES   AND   TIPS", rules_start)
        tables_start = value.find("APPENDIX 2", notes_start)
        options_start = value.find("APPENDIX 3", tables_start)
        advanced_start = value.find("ADVANCED CUSTOMIZATION", options_start)
        if min(rules_start, notes_start, tables_start, options_start, advanced_start) < 0:
            return ""
        selected = "\n\n".join((
            value[rules_start:notes_start], value[tables_start:options_start],
            value[options_start:advanced_start],
        ))
        excluded = ("scenario editor", "advanced customization", "notes and tips",
                    "general tips", "walkthrough", "cheat mode")
        return "\n".join(line for line in selected.splitlines()
                         if not any(phrase in line.casefold() for phrase in excluded))
    return value


def chunks(text: str) -> list[str]:
    words = WORDS.findall(text)
    result: list[str] = []
    step = CHUNK_WORDS - CHUNK_OVERLAP
    for offset in range(0, len(words), step):
        chunk = " ".join(words[offset:offset + CHUNK_WORDS]).strip()
        if len(chunk) >= 80:
            result.append(chunk)
        if offset + CHUNK_WORDS >= len(words):
            break
    return result


def main() -> int:
    identity = validate_source(SOURCE_ROOT)
    print(json.dumps({
        "type": "manifest", "schema": "smacx.private-reference.v2",
        "terranx_sha256": identity["terranx_sha256"],
        "ruleset_id": "smacx", "policy": "mechanics_only_no_guides",
        "precedence": ["structured-alien-crossfire", "expansion-datalinks", "manual-rules"],
    }, separators=(",", ":")), flush=True)
    documents: list[dict] = []
    sources: set[str] = set()
    counts: dict[str, int] = {}
    with tempfile.TemporaryDirectory(prefix="smacx-reference-") as directory:
        temporary = Path(directory)
        alphax = SOURCE_ROOT / "alphax.txt"
        if not alphax.is_file() or alphax.is_symlink():
            raise RuntimeError("alien_crossfire_rules_data_missing")
        source_hash = file_sha256(alphax)
        source_value = normalize(read_source(alphax, temporary))
        sources.add(alphax.name)
        for section in SECTION_SPECS:
            for index, values in enumerate(rows(source_value, section)):
                document = entity_from_row(alphax.name, source_hash, section, values, index)
                if document:
                    documents.append(document)
        for name in FACTION_FILES:
            path = SOURCE_ROOT / name
            if path.is_file() and not path.is_symlink():
                document = faction_entity(path, normalize(read_source(path, temporary)))
                if document:
                    documents.append(document)
                    sources.add(path.name)
        for name in RULE_SOURCES:
            path = SOURCE_ROOT / name
            if not path.is_file() or path.is_symlink():
                continue
            source_hash = file_sha256(path)
            source_chunks = chunks(normalize(mechanics_only(
                path.name, read_source(path, temporary),
            )))
            if not source_chunks:
                continue
            sources.add(path.name)
            priority = 200 if path.suffix.casefold() == ".txt" else 100
            topic = "datalinks" if path.suffix.casefold() == ".txt" else "manual"
            for index, body in enumerate(source_chunks):
                documents.append({
                    "type": "document", "document_kind": "section",
                    "source_name": path.name, "source_sha256": source_hash,
                    "topic": topic, "ruleset_id": "smacx", "source_priority": priority,
                    "section_index": index, "section_count": len(source_chunks),
                    "title": f"{path.name} — mechanics section {index + 1}/{len(source_chunks)}",
                    "summary": "Private rules fallback from the operator's installation; exact structured records take precedence.",
                    "body": body, "metadata": {"related": []},
                })
    # Add reverse unlock relationships after every native code is known.
    by_tech: dict[str, dict] = {
        str(item.get("entity_key", "")).casefold(): item for item in documents
        if item.get("entity_kind") == "technology"
    }
    for item in documents:
        for edge in item.get("metadata", {}).get("related", []):
            if edge.get("kind") == "technology":
                tech = by_tech.get(str(edge.get("key", "")).casefold())
                if tech is not None:
                    tech["metadata"]["related"].append(relation(
                        str(item.get("entity_kind")), str(item.get("entity_key")), "unlocks",
                    ))
    if len(documents) > MAX_DOCUMENTS:
        raise RuntimeError("private_reference_document_limit")
    for item in documents:
        kind = str(item.get("entity_kind") or "section")
        counts[kind] = counts.get(kind, 0) + 1
        print(json.dumps(item, ensure_ascii=False, separators=(",", ":")), flush=True)
    required = {"technology", "facility", "weapon", "defense", "ability", "faction",
                "social-model", "council-proposal", "time-control", "difficulty"}
    if not required.issubset(counts):
        raise RuntimeError("private_reference_required_entity_kind_missing")
    print(json.dumps({
        "type": "complete", "sources": len(sources), "documents": len(documents),
        "entity_counts": counts, "required_entity_kinds": sorted(required),
    }, separators=(",", ":")), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
