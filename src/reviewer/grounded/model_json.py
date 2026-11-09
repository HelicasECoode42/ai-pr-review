"""Strict parsing of model responses."""
from __future__ import annotations

import json
import re


def _repair_closed_containers(raw):
    """Correct only mismatched closing brackets in a fully closed response.

    Never create keys, values, quotes, or missing EOF containers; do not repair
    truncated responses or concatenate multiple objects.
    """
    stack, output, quoted, escaped = [], [], False, False
    for char in raw:
        if quoted:
            output.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
            continue
        if char == '"':
            quoted = True; output.append(char)
        elif char in "{[":
            if not stack and "".join(output).strip():
                raise ValueError("Multiple JSON objects are not supported")
            stack.append(char); output.append(char)
        elif char in "}]":
            if stack:
                output.append("}" if stack.pop() == "{" else "]")
            # Ignore only surplus closing-container punctuation, never other data.
        else:
            output.append(char)
    if quoted or stack:
        raise ValueError("Truncated JSON cannot be repaired")
    return "".join(output)


_EXCERPT_CHARS = 300


class ModelResponseError(ValueError):
    """An unparseable model answer, carrying a short excerpt for the trace."""

    def __init__(self, cause: Exception, raw: str):
        super().__init__(f"{type(cause).__name__}: {cause}")
        self.error_type = type(cause).__name__
        self.raw_excerpt = str(raw)[:_EXCERPT_CHARS]


def _json(provider, system, prompt):
    raw = provider.complete_json(system, prompt)
    try:
        return _parse_model_json(raw)
    except ValueError as exc:
        raise ModelResponseError(exc, raw) from exc


def _parse_model_json(raw):
    notice = None
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        try:
            # Preserve literal newlines in fully formed strings; do not complete truncated JSON.
            value = json.loads(raw, strict=False)
            notice = "literal_control_characters"
        except json.JSONDecodeError:
            try:
                value, end = json.JSONDecoder().raw_decode(raw.lstrip())
                tail = raw.lstrip()[end:]
                if not isinstance(value, list) or not re.fullmatch(r"(?:\s*</[｜A-Za-z_ ]+>)+\s*", tail):
                    raise ValueError("Unsupported model response envelope")
                notice = "dsml_request_array"
            except (json.JSONDecodeError, ValueError):
                value = json.loads(_repair_closed_containers(raw), strict=False)
                notice = "closing_container_types_normalized"
    if isinstance(value, list):
        if not value or not all(isinstance(r, dict) and r.get("tool") in {"read", "search"} for r in value):
            raise ValueError("Only read/search request arrays are supported")
        value = {"context_requests": value, "response_format_notice": notice or "bare_request_array"}
    if not isinstance(value, dict):
        raise ValueError("Review response must be an object")
    if notice:
        value["response_format_notice"] = notice
    return value
