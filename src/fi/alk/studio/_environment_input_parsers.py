from __future__ import annotations

import csv
import io
import json
import math
import unicodedata
from dataclasses import dataclass
from pathlib import PurePath
from typing import Any, Literal

MAX_SOURCE_BYTES = 25 * 1024 * 1024
MAX_SOURCE_FILES = 100
MAX_DESIGN_BYTES = 200 * 1024 * 1024
MAX_PDF_PAGES = 2_000
MAX_EXTRACTED_TEXT_CHARS = 10 * 1024 * 1024
MAX_RECORD_FIELDS = 500
MAX_RECORD_ROWS = 100_000
PARSER_REVISION = "environment-inputs-v1"


class SourceParseError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class RawParseIssue:
    code: str
    message: str
    row: int | None = None
    field: str | None = None


@dataclass(frozen=True)
class RawPolicyPage:
    page: int
    text: str


@dataclass(frozen=True)
class RawPolicyDocument:
    pages: tuple[RawPolicyPage, ...]


@dataclass(frozen=True)
class RawRecordDocument:
    headers: tuple[str, ...]
    rows: tuple[tuple[Any, ...], ...]
    source_rows: tuple[int, ...]
    issues: tuple[RawParseIssue, ...] = ()


RawParsedSource = RawPolicyDocument | RawRecordDocument


def parse_environment_source(
    *,
    content: bytes,
    filename: str,
    media_type: str,
    role: Literal["policy", "reference", "seed_records"],
) -> RawParsedSource:
    if len(content) > MAX_SOURCE_BYTES:
        raise SourceParseError(
            "source_too_large",
            f"{filename} exceeds the 25 MiB source limit",
        )
    suffix = PurePath(filename).suffix.lower()
    normalized_media_type = media_type.split(";", 1)[0].strip().lower()
    if (
        role == "reference"
        and normalized_media_type == "text/plain"
        and suffix == ".txt"
    ):
        text = _normalize_text(_decode_text(content))
        if len(text) > MAX_EXTRACTED_TEXT_CHARS:
            raise SourceParseError(
                "source_too_large", "Reference text exceeds the 10 MiB character limit"
            )
        if not text:
            raise SourceParseError(
                "source_no_extractable_text", "The reference contains no text"
            )
        return RawPolicyDocument(pages=(RawPolicyPage(page=1, text=text),))
    if role in {"policy", "reference"}:
        if suffix != ".pdf" or normalized_media_type != "application/pdf":
            raise SourceParseError(
                "source_unsupported_type",
                "Policy sources require PDF; reference sources require PDF or UTF-8 text",
            )
        return _parse_pdf(content)
    if suffix == ".csv" and normalized_media_type in {
        "text/csv",
        "application/csv",
        "application/vnd.ms-excel",
    }:
        return _parse_csv(content)
    if suffix == ".json" and normalized_media_type == "application/json":
        return _parse_json(content)
    raise SourceParseError(
        "source_unsupported_type",
        "Week 1 record sources must be CSV or JSON files",
    )


def _parse_pdf(content: bytes) -> RawPolicyDocument:
    try:
        from pypdf import PdfReader, apply_configuration
        from pypdf.errors import LimitReachedError, PdfReadError
    except ImportError as exc:
        raise SourceParseError(
            "source_parser_dependency_missing",
            "PDF parsing requires the environment-inputs dependency group",
        ) from exc

    try:
        with apply_configuration(
            maximum_declared_stream_length=MAX_EXTRACTED_TEXT_CHARS,
            array_based_stream_maximum_output_length=MAX_EXTRACTED_TEXT_CHARS,
            jbig2_maximum_output_length=MAX_EXTRACTED_TEXT_CHARS,
            lzw_maximum_output_length=MAX_EXTRACTED_TEXT_CHARS,
            run_length_maximum_output_length=MAX_EXTRACTED_TEXT_CHARS,
            zlib_maximum_output_length=MAX_EXTRACTED_TEXT_CHARS,
            brotli_maximum_output_length=MAX_EXTRACTED_TEXT_CHARS,
            image_maximum_buffer_size=MAX_EXTRACTED_TEXT_CHARS,
            stream_decoding_work_maximum_length=MAX_EXTRACTED_TEXT_CHARS,
        ):
            reader = PdfReader(io.BytesIO(content), strict=True)
            if reader.is_encrypted:
                raise SourceParseError(
                    "source_parse_failed",
                    "Password-protected PDF files are not supported",
                )
            if len(reader.pages) > MAX_PDF_PAGES:
                raise SourceParseError(
                    "source_too_large",
                    f"PDF sources may contain at most {MAX_PDF_PAGES} pages",
                )
            parsed_pages: list[RawPolicyPage] = []
            extracted_chars = 0
            for index, page in enumerate(reader.pages, start=1):
                text = _normalize_text(page.extract_text() or "")
                extracted_chars += len(text)
                if extracted_chars > MAX_EXTRACTED_TEXT_CHARS:
                    raise SourceParseError(
                        "source_too_large",
                        "Extracted PDF text exceeds the 10 MiB character limit",
                    )
                parsed_pages.append(RawPolicyPage(page=index, text=text))
            pages = tuple(parsed_pages)
    except SourceParseError:
        raise
    except LimitReachedError as exc:
        raise SourceParseError(
            "source_too_large", "PDF decompression exceeded the parser resource limit"
        ) from exc
    except (PdfReadError, OSError, ValueError, TypeError) as exc:
        raise SourceParseError(
            "source_parse_failed", "The PDF could not be read"
        ) from exc
    if not pages or not any(page.text.strip() for page in pages):
        raise SourceParseError(
            "source_no_extractable_text",
            "The PDF contains no extractable text; OCR is not supported in Week 1",
        )
    return RawPolicyDocument(pages=pages)


def _parse_csv(content: bytes) -> RawRecordDocument:
    text = _decode_text(content)
    try:
        reader = csv.reader(io.StringIO(text, newline=""), strict=True)
        raw_headers = next(reader)
    except StopIteration as exc:
        raise SourceParseError("source_parse_failed", "The CSV file is empty") from exc
    except csv.Error as exc:
        raise SourceParseError(
            "source_parse_failed", "The CSV header is malformed"
        ) from exc
    headers = tuple(header.strip() for header in raw_headers)
    if not headers or any(not header for header in headers):
        raise SourceParseError("source_parse_failed", "CSV headers must be nonempty")
    if len(set(headers)) != len(headers):
        raise SourceParseError("source_parse_failed", "CSV headers must be unique")
    if len(headers) > MAX_RECORD_FIELDS:
        raise SourceParseError(
            "source_too_large",
            f"Record sources may contain at most {MAX_RECORD_FIELDS} fields",
        )

    rows: list[tuple[str, ...]] = []
    source_rows: list[int] = []
    issues: list[RawParseIssue] = []
    try:
        for row_number, row in enumerate(reader, start=2):
            if row_number > MAX_RECORD_ROWS + 1:
                raise SourceParseError(
                    "source_too_large",
                    f"Record sources may contain at most {MAX_RECORD_ROWS} rows",
                )
            if len(row) != len(headers):
                issues.append(
                    RawParseIssue(
                        code="source_parse_failed",
                        message=(
                            f"Row {row_number} has {len(row)} cells; "
                            f"expected {len(headers)}"
                        ),
                        row=row_number,
                    )
                )
                continue
            rows.append(tuple(_normalize_text(value) for value in row))
            source_rows.append(row_number)
    except csv.Error:
        issues.append(
            RawParseIssue(
                code="source_parse_failed",
                message="The CSV body contains a malformed row",
                row=reader.line_num,
            )
        )
    return RawRecordDocument(
        headers=headers,
        rows=tuple(rows),
        source_rows=tuple(source_rows),
        issues=tuple(issues),
    )


def _parse_json(content: bytes) -> RawRecordDocument:
    text = _decode_text(content)
    try:
        payload = json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_json_constant,
            parse_float=_finite_json_float,
        )
    except (json.JSONDecodeError, ValueError, RecursionError) as exc:
        raise SourceParseError(
            "source_parse_failed", "The JSON file is invalid"
        ) from exc
    if not isinstance(payload, list):
        raise SourceParseError(
            "source_parse_failed",
            "The JSON top level must be an array of objects",
        )
    if len(payload) > MAX_RECORD_ROWS:
        raise SourceParseError(
            "source_too_large",
            f"Record sources may contain at most {MAX_RECORD_ROWS} rows",
        )

    headers: list[str] = []
    for item in payload:
        if isinstance(item, dict):
            for key in item:
                if key not in headers:
                    headers.append(key)
                    if len(headers) > MAX_RECORD_FIELDS:
                        raise SourceParseError(
                            "source_too_large",
                            f"Record sources may contain at most {MAX_RECORD_FIELDS} fields",
                        )
    issues: list[RawParseIssue] = []
    rows: list[tuple[Any, ...]] = []
    source_rows: list[int] = []
    for index, item in enumerate(payload, start=1):
        if not isinstance(item, dict):
            issues.append(
                RawParseIssue(
                    code="source_parse_failed",
                    message=f"JSON item {index} is not an object",
                    row=index,
                )
            )
            continue
        nested_field = next(
            (field for field, value in item.items() if isinstance(value, (dict, list))),
            None,
        )
        if nested_field is not None:
            issues.append(
                RawParseIssue(
                    code="source_parse_failed",
                    message=(
                        f"JSON item {index} field {nested_field!r} is nested; "
                        "flatten it before upload"
                    ),
                    row=index,
                    field=nested_field,
                )
            )
            continue
        rows.append(
            tuple(
                _normalize_text(value) if isinstance(value, str) else value
                for value in (item.get(header) for header in headers)
            )
        )
        source_rows.append(index)
    return RawRecordDocument(
        headers=tuple(headers),
        rows=tuple(rows),
        source_rows=tuple(source_rows),
        issues=tuple(issues),
    )


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if not isinstance(key, str):
            raise ValueError("JSON object keys must be strings")
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"Non-finite JSON number: {value}")


def _finite_json_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("Non-finite JSON number")
    return number


def _decode_text(content: bytes) -> str:
    try:
        return content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise SourceParseError(
            "source_parse_failed",
            "CSV and JSON sources must use UTF-8 encoding",
        ) from exc


def _normalize_text(value: str) -> str:
    normalized = (
        unicodedata.normalize("NFC", value).replace("\r\n", "\n").replace("\r", "\n")
    )
    return "\n".join(line.rstrip() for line in normalized.split("\n")).strip()


__all__ = [
    "MAX_DESIGN_BYTES",
    "MAX_SOURCE_BYTES",
    "MAX_SOURCE_FILES",
    "PARSER_REVISION",
    "RawParseIssue",
    "RawPolicyDocument",
    "RawPolicyPage",
    "RawRecordDocument",
    "SourceParseError",
    "parse_environment_source",
]
