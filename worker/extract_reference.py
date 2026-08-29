#!/usr/bin/env python3
"""Extract a private mechanics-only reference index from a legal game copy.

The helper runs with a read-only game-source mount and emits bounded JSONL to
the Control Center. It deliberately excludes scripts, walkthroughs, scenario
directories, narrative files, and strategy guides.
"""

from __future__ import annotations

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
CORE_FILES = (
    "Manual.pdf",
    "alpha.txt",
    "alphax.txt",
    "help.txt",
    "helpx.txt",
    "concepts.txt",
    "conceptsx.txt",
    "TECHSHORTS.txt",
    "GAIANS.TXT",
    "MORGAN.TXT",
    "PEACE.TXT",
    "angels.txt",
    "believe.txt",
    "caretake.txt",
    "cyborg.txt",
    "drone.txt",
    "fungboy.txt",
    "hive.txt",
    "pirates.txt",
    "spartans.txt",
    "univ.txt",
    "usurper.txt",
)
MAX_SOURCE_BYTES = 16 * 1024 * 1024
MAX_DOCUMENTS = 900
CHUNK_WORDS = 320
CHUNK_OVERLAP = 32
CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
WORDS = re.compile(r"\S+")


def sha256(path: Path) -> str:
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
        check=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    return output.read_text(encoding="utf-8", errors="replace")


def normalize(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).replace("\r", "\n")
    value = CONTROL.sub("", value)
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def between(value: str, start: str, end: str | None = None) -> str:
    """Select a named source section and fail closed when its boundary is absent."""
    start_index = value.find(start)
    if start_index < 0:
        return ""
    if end is None:
        return value[start_index:]
    end_index = value.find(end, start_index + len(start))
    if end_index < 0:
        return ""
    return value[start_index:end_index]


def mechanics_only(name: str, value: str) -> str:
    """Remove tutorial, strategy, narrative, and editor material before chunking."""
    folded = name.casefold()
    if folded == "manual.pdf":
        # Chapters 3-5 are the rules reference. Appendix 2 contains the tables,
        # and Appendix 3 contains the documented game-rule/preferences surface.
        # The tutorial, Notes and Tips, scenario editor, and setting fiction are
        # deliberately absent from an agent's retrieval corpus.
        # Ignore the table-of-contents occurrences and anchor on unique body
        # headings. Appendix headers repeat on every page, so each following
        # boundary is searched only after the prior body section begins.
        rules_start = value.find("CHAPTER 3", 1000)
        notes_start = value.find("NOTES   AND   TIPS", rules_start)
        tables_start = value.find("APPENDIX 2", notes_start)
        options_start = value.find("APPENDIX 3", tables_start)
        advanced_start = value.find("ADVANCED CUSTOMIZATION", options_start)
        if min(rules_start, notes_start, tables_start, options_start, advanced_start) < 0:
            return ""
        rules = value[rules_start:notes_start]
        tables = value[tables_start:options_start]
        options = value[options_start:advanced_start]
        # Main-rule chapters can mention that an editor command exists. Those
        # cross-reference lines add no player mechanic and are omitted too.
        excluded_line_phrases = (
            "scenario editor", "advanced customization", "notes and tips",
            "general tips", "walkthrough", "cheat mode",
        )
        selected = "\n\n".join((rules, tables, options))
        return "\n".join(
            line for line in selected.splitlines()
            if not any(phrase in line.casefold() for phrase in excluded_line_phrases)
        )
    if folded in {"alpha.txt", "alphax.txt"}:
        mechanics = between(value, "#RULES", "##### IMPORTANT NOTE TO TRANSLATORS")
        terminology = between(value, "#BONUSNAMES")
        return "\n\n".join(part for part in (mechanics, terminology) if part)
    if folded.endswith(".txt") and folded not in {
        "help.txt", "helpx.txt", "concepts.txt", "conceptsx.txt", "techshorts.txt",
    }:
        # Faction records begin with identity, mechanical bonuses, preferred
        # social model, and aversion. Everything after that is names, dialogue,
        # personality prose, bases, and narrative assets.
        header = between(value, "#", "#BASES")
        lines = [line for line in header.splitlines() if line.strip() and not line.lstrip().startswith(";")]
        return "\n".join(lines[:5])
    return value


def topic_for(name: str) -> str:
    folded = name.casefold()
    if folded == "manual.pdf" or folded.startswith("concept"):
        return "manual"
    if folded.startswith("help"):
        return "datalinks"
    if folded in {"alpha.txt", "alphax.txt"}:
        return "rules-data"
    if folded == "techshorts.txt":
        return "research"
    return "factions"


def chunks(text: str) -> list[str]:
    words = WORDS.findall(text)
    if not words:
        return []
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
    documents = 0
    sources = 0
    print(json.dumps({
        "type": "manifest",
        "schema": "smacx.private-reference.v1",
        "terranx_sha256": identity["terranx_sha256"],
        "policy": "mechanics_only_no_guides",
    }, separators=(",", ":")), flush=True)
    with tempfile.TemporaryDirectory(prefix="smacx-reference-") as directory:
        temporary = Path(directory)
        for source_name in CORE_FILES:
            path = SOURCE_ROOT / source_name
            if not path.is_file() or path.is_symlink():
                continue
            source_hash = sha256(path)
            source_chunks = chunks(normalize(mechanics_only(
                source_name, read_source(path, temporary),
            )))
            if not source_chunks:
                continue
            sources += 1
            for index, body in enumerate(source_chunks):
                documents += 1
                if documents > MAX_DOCUMENTS:
                    raise RuntimeError("private_reference_document_limit")
                print(json.dumps({
                    "type": "document",
                    "source_name": source_name,
                    "source_sha256": source_hash,
                    "chunk_index": index,
                    "chunk_count": len(source_chunks),
                    "topic": topic_for(source_name),
                    "body": body,
                }, ensure_ascii=False, separators=(",", ":")), flush=True)
    print(json.dumps({
        "type": "complete", "sources": sources, "documents": documents,
    }, separators=(",", ":")), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
