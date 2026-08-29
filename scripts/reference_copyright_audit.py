#!/usr/bin/env python3
"""Fail when project-authored reference prose has long verbatim source overlap.

Only hashes, identifiers, and overlap lengths are emitted. Source passages are
never printed or written into the repository.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile
import unicodedata
from typing import Any


DEFAULT_CORPUS = Path(__file__).resolve().parents[1] / "knowledge" / "core.json"
WORD_RE = re.compile(r"[^\W_]+(?:['’][^\W_]+)?", re.UNICODE)


def _words(value: str) -> list[str]:
    normalized = unicodedata.normalize("NFKC", value).casefold().replace("’", "'")
    return WORD_RE.findall(normalized)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_source(path: Path, temporary: Path) -> str:
    if path.suffix.casefold() != ".pdf":
        return path.read_text(encoding="utf-8", errors="ignore")
    output = temporary / f"{hashlib.sha256(str(path).encode()).hexdigest()}.txt"
    try:
        subprocess.run(
            ["pdftotext", "-enc", "UTF-8", str(path), str(output)],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
    except FileNotFoundError as error:
        raise RuntimeError("pdftotext is required to audit PDF sources") from error
    except subprocess.CalledProcessError as error:
        raise RuntimeError(f"pdftotext failed for {path.name}: {error.stderr.strip()}") from error
    return output.read_text(encoding="utf-8", errors="ignore")


def _source_index(words: list[str], width: int) -> dict[tuple[str, ...], list[int]]:
    positions: dict[tuple[str, ...], list[int]] = {}
    for offset in range(0, len(words) - width + 1):
        positions.setdefault(tuple(words[offset:offset + width]), []).append(offset)
    return positions


def _longest_overlap(
    source_words: list[str], source_index: dict[tuple[str, ...], list[int]],
    document_words: list[str], minimum: int,
) -> int:
    longest = 0
    for document_offset in range(0, len(document_words) - minimum + 1):
        key = tuple(document_words[document_offset:document_offset + minimum])
        for source_offset in source_index.get(key, ()):
            length = minimum
            while (
                document_offset + length < len(document_words)
                and source_offset + length < len(source_words)
                and document_words[document_offset + length]
                == source_words[source_offset + length]
            ):
                length += 1
            longest = max(longest, length)
    return longest


def _documents(path: Path) -> list[tuple[str, list[str]]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema") != "smacx.reference-corpus.v1":
        raise ValueError("invalid reference corpus schema")
    documents: list[tuple[str, list[str]]] = []
    for document in payload.get("documents", []):
        document_id = str(document.get("document_id", ""))
        authored_text = " ".join(
            str(document.get(field, "")) for field in ("title", "summary", "body")
        )
        if not document_id or not authored_text.strip():
            raise ValueError("invalid reference document")
        documents.append((document_id, _words(authored_text)))
    return documents


def audit(corpus: Path, sources: list[Path], minimum: int) -> dict[str, Any]:
    documents = _documents(corpus)
    source_results: list[dict[str, Any]] = []
    violations: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="smacx-copyright-audit-") as directory:
        temporary = Path(directory)
        for source in sources:
            resolved = source.expanduser().resolve(strict=True)
            if not resolved.is_file():
                raise ValueError(f"source is not a regular file: {resolved}")
            source_words = _words(_read_source(resolved, temporary))
            index = _source_index(source_words, minimum)
            source_maximum = 0
            source_document: str | None = None
            for document_id, document_words in documents:
                overlap = _longest_overlap(source_words, index, document_words, minimum)
                if overlap > source_maximum:
                    source_maximum = overlap
                    source_document = document_id
                if overlap >= minimum:
                    violations.append({
                        "source": resolved.name,
                        "document_id": document_id,
                        "overlap_words": overlap,
                    })
            source_results.append({
                "source": resolved.name,
                "sha256": _sha256(resolved),
                "normalized_word_count": len(source_words),
                "maximum_overlap_words": source_maximum,
                "maximum_overlap_document_id": source_document,
            })
    return {
        "ok": not violations,
        "corpus": corpus.name,
        "minimum_failure_words": minimum,
        "document_count": len(documents),
        "sources": source_results,
        "violations": violations,
        "source_text_emitted": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--source", action="append", type=Path, required=True)
    parser.add_argument("--minimum-words", type=int, default=8)
    arguments = parser.parse_args()
    if not 8 <= arguments.minimum_words <= 100:
        parser.error("--minimum-words must be between 8 and 100")
    try:
        result = audit(arguments.corpus.resolve(strict=True), arguments.source,
                       arguments.minimum_words)
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as error:
        result = {"ok": False, "error": str(error), "source_text_emitted": False}
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
