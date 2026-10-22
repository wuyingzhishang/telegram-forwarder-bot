from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any


_LINK_ENTITY_TYPES = {"url", "text_link", "mention", "text_mention", "email"}
_URL_PATTERN = re.compile(r"(?i)\b(?:https?://|www\.)[^\s<>()]+")


def _utf16_length(value: str) -> int:
    return len(value.encode("utf-16-le")) // 2


def _utf16_to_index(value: str, offset: int) -> int:
    consumed = 0
    for index, character in enumerate(value):
        if consumed >= offset:
            return index
        consumed += _utf16_length(character)
    return len(value)


def _entity_data(entities: Iterable[Any], value: str) -> list[dict[str, Any]]:
    result = []
    for entity in entities:
        data = entity.to_dict() if hasattr(entity, "to_dict") else dict(entity)
        start = _utf16_to_index(value, int(data.get("offset", 0)))
        end = _utf16_to_index(value, int(data.get("offset", 0)) + int(data.get("length", 0)))
        if end > start:
            data["_start"] = start
            data["_end"] = end
            result.append(data)
    return result


def _apply_replacement(
    value: str,
    entities: list[dict[str, Any]],
    start: int,
    end: int,
    replacement: str,
) -> tuple[str, list[dict[str, Any]]]:
    delta = len(replacement) - (end - start)
    updated = []
    for entity in entities:
        entity_start = int(entity["_start"])
        entity_end = int(entity["_end"])
        if entity_end <= start:
            updated.append(entity)
            continue
        if entity_start >= end:
            entity["_start"] = entity_start + delta
            entity["_end"] = entity_end + delta
            updated.append(entity)
            continue
        if not replacement and entity_start >= start and entity_end <= end:
            continue
        entity["_start"] = entity_start if entity_start < start else start
        entity["_end"] = entity_end + delta if entity_end > end else start + len(replacement)
        if int(entity["_end"]) > int(entity["_start"]):
            updated.append(entity)
    return value[:start] + replacement + value[end:], updated


def _merge_spans(spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    merged: list[tuple[int, int]] = []
    for start, end in sorted(spans):
        if not merged or start > merged[-1][1]:
            merged.append((start, end))
        else:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
    return merged


def _delete_spans(
    value: str,
    entities: list[dict[str, Any]],
    spans: list[tuple[int, int]],
) -> tuple[str, list[dict[str, Any]]]:
    for start, end in reversed(_merge_spans(spans)):
        value, entities = _apply_replacement(value, entities, start, end, "")
    return value, entities


def _delete_terms(
    value: str,
    entities: list[dict[str, Any]],
    terms: Iterable[str],
) -> tuple[str, list[dict[str, Any]]]:
    for term in terms:
        term = str(term).strip()
        if not term:
            continue
        spans = []
        for match in re.finditer(re.escape(term), value, re.IGNORECASE):
            start, end = match.span()
            while end < len(value) and value[end] in " \t":
                end += 1
            if end == match.end():
                while start > 0 and value[start - 1] in " \t":
                    start -= 1
            spans.append((start, end))
        value, entities = _delete_spans(value, entities, spans)
    return value, entities


def transform_text(
    value: str,
    source_entities: Iterable[Any],
    rule: dict[str, object],
) -> tuple[str, list[dict[str, Any]]]:
    entities = _entity_data(source_entities, value)

    if rule.get("remove_links"):
        spans = []
        for entity in entities:
            if entity.get("type") not in _LINK_ENTITY_TYPES:
                continue
            start, end = int(entity["_start"]), int(entity["_end"])
            while end < len(value) and value[end] in " \t":
                end += 1
            if end == int(entity["_end"]):
                while start > 0 and value[start - 1] in " \t":
                    start -= 1
            spans.append((start, end))
        for match in _URL_PATTERN.finditer(value):
            start = match.start()
            end = match.end()
            while end > match.start() and value[end - 1] in ".,!?;:":
                end -= 1
            while start > 0 and value[start - 1] in " \t":
                start -= 1
            if end > match.start():
                spans.append((start, end))
        value, entities = _delete_spans(value, entities, spans)

    value, entities = _delete_terms(value, entities, rule.get("remove_keywords", []))

    replacements = rule.get("replacements", [])
    if isinstance(replacements, list):
        for item in replacements:
            if not isinstance(item, dict):
                continue
            find = str(item.get("find", "")).strip()
            if not find:
                continue
            replacement = str(item.get("replace", ""))
            spans = [(match.start(), match.end()) for match in re.finditer(re.escape(find), value, re.IGNORECASE)]
            for start, end in reversed(spans):
                value, entities = _apply_replacement(value, entities, start, end, replacement)

    prefix = str(rule.get("prefix_text", ""))
    suffix = str(rule.get("suffix_text", ""))
    if prefix:
        addition = prefix + ("\n\n" if value else "")
        value = addition + value
        shift = len(addition)
        for entity in entities:
            entity["_start"] = int(entity["_start"]) + shift
            entity["_end"] = int(entity["_end"]) + shift
    if suffix:
        addition = ("\n\n" if value else "") + suffix
        value += addition

    final_entities = []
    for entity in entities:
        start = int(entity.pop("_start"))
        end = int(entity.pop("_end"))
        entity["offset"] = _utf16_length(value[:start])
        entity["length"] = _utf16_length(value[start:end])
        final_entities.append(entity)
    return value, final_entities
