from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Sequence
from typing import Literal

JavaSymbolKind = Literal[
    "type",
    "method",
    "constructor",
    "field",
    "initializer",
    "enum_constant",
]

_MEMBER_KINDS = frozenset({"method", "constructor", "field", "initializer", "enum_constant"})
_PARAMETER_NAME = re.compile(
    r"^(?P<type>.+?)\s+(?P<name>[^\W\d]\w*|[$_][\w$]*)(?P<dimensions>(?:\s*\[\s*\])*)$",
    re.UNICODE,
)


def normalize_java_identifier(value: str) -> str | None:
    normalized = unicodedata.normalize("NFC", value.strip())
    if not normalized or not _is_java_identifier_start(normalized[0]):
        return None
    if not all(_is_java_identifier_part(character) for character in normalized[1:]):
        return None
    return normalized


def normalize_java_qualified_name(value: str) -> str | None:
    parts = value.strip().split(".")
    if not parts:
        return None
    normalized = [normalize_java_identifier(part) for part in parts]
    if any(part is None for part in normalized):
        return None
    return ".".join(part for part in normalized if part is not None)


def normalize_java_type(value: str) -> str | None:
    """Normalize source-level Java type spelling without resolving imports or erasure."""
    tokens = _type_tokens(unicodedata.normalize("NFC", value.strip()))
    if tokens is None or not tokens:
        return None
    angle_depth = 0
    bracket_depth = 0
    output: list[str] = []
    previous: str | None = None
    expect_bound_type = False
    for token in tokens:
        if token == "<":
            angle_depth += 1
        elif token == ">":
            angle_depth -= 1
            if angle_depth < 0:
                return None
        elif token == "[":
            bracket_depth += 1
        elif token == "]":
            bracket_depth -= 1
            if bracket_depth < 0:
                return None

        if _is_identifier_token(token):
            if (
                previous is not None
                and _is_identifier_token(previous)
                and previous
                not in {
                    "extends",
                    "super",
                }
            ):
                return None
            if token in {"extends", "super"}:
                if previous != "?":
                    return None
                output.append(f" {token} ")
                expect_bound_type = True
            else:
                if (
                    previous in {"extends", "super", "&"}
                    and output
                    and not output[-1].endswith(" ")
                ):
                    output.append(" ")
                output.append(token)
                expect_bound_type = False
        elif token == "...":
            if previous is None or previous in {".", "<", ",", "?", "extends", "super", "&"}:
                return None
            output.append("[]")
        elif token == "&":
            if previous is None or previous in {"<", ",", "?", "extends", "super", "&"}:
                return None
            output.append(" & ")
            expect_bound_type = True
        else:
            output.append(token)
        previous = token
    if angle_depth or bracket_depth or expect_bound_type:
        return None
    result = "".join(output).strip()
    if not result or result[-1] in {".", "<", ",", "?", "&"}:
        return None
    if "[]" in result and not _valid_array_suffixes(result):
        return None
    return result


def normalize_java_parameter_types(value: str) -> tuple[str, ...] | None:
    parts = _split_top_level_parameters(value)
    if parts is None:
        return None
    normalized: list[str] = []
    for part in parts:
        parameter_type = _normalize_parameter_declaration(part)
        if parameter_type is None:
            return None
        normalized.append(parameter_type)
    return tuple(normalized)


def normalize_symbol_lookup_keys(values: Sequence[str] | None) -> list[str] | None:
    if not values or isinstance(values, str):
        return None
    if any(not isinstance(value, str) or not value for value in values):
        return None
    normalized = _stable_values(values)
    return normalized or None


def build_java_type_lookup_keys(qualified_name: str, simple_name: str) -> list[str] | None:
    qualified = normalize_java_qualified_name(qualified_name)
    simple = normalize_java_identifier(simple_name)
    if qualified is None or simple is None:
        return None
    aliases = _qualified_type_aliases(qualified)
    aliases.append(simple)
    return _stable_keys(f"v1:type:{alias}" for alias in aliases)


def build_java_member_lookup_keys(
    kind: JavaSymbolKind,
    qualified_owner: str,
    member_names: Sequence[str],
    *,
    parameter_types: Sequence[str] | None = None,
) -> list[str] | None:
    if kind not in _MEMBER_KINDS:
        return None
    owner = normalize_java_qualified_name(qualified_owner)
    if owner is None:
        return None
    owners = _qualified_type_aliases(owner)
    normalized_members = _stable_values(
        member
        for value in member_names
        if (member := _normalize_member_name(kind, value)) is not None
    )
    if not normalized_members:
        return None
    normalized_parameters: tuple[str, ...] | None = None
    if parameter_types is not None:
        values = tuple(normalize_java_type(value) for value in parameter_types)
        if any(value is None for value in values):
            return None
        normalized_parameters = tuple(value for value in values if value is not None)

    keys: list[str] = []
    for current_owner in owners:
        for member in normalized_members:
            keys.append(f"v1:{kind}:{current_owner}#{member}")
            if normalized_parameters is not None and kind in {"method", "constructor"}:
                parameters = ",".join(normalized_parameters)
                keys.append(f"v1:{kind}:{current_owner}#{member}({parameters})")
    return _stable_keys(keys)


def _qualified_type_aliases(qualified_name: str) -> list[str]:
    parts = qualified_name.split(".")
    aliases = [qualified_name]
    for index, part in enumerate(parts[:-1]):
        if part[0].isupper():
            aliases.append(".".join(parts[index:]))
            break
    aliases.append(parts[-1])
    return _stable_values(aliases)


def _normalize_member_name(kind: JavaSymbolKind, value: str) -> str | None:
    if kind == "constructor":
        return "<init>"
    if kind == "initializer":
        return value if value in {"<clinit>", "<init-block>"} else None
    return normalize_java_identifier(value)


def _type_tokens(value: str) -> list[str] | None:
    tokens: list[str] = []
    index = 0
    while index < len(value):
        character = value[index]
        if character.isspace():
            index += 1
            continue
        if value.startswith("...", index):
            tokens.append("...")
            index += 3
            continue
        if character in ".<>,?&[]":
            tokens.append(character)
            index += 1
            continue
        if _is_java_identifier_start(character):
            end = index + 1
            while end < len(value) and _is_java_identifier_part(value[end]):
                end += 1
            tokens.append(value[index:end])
            index = end
            continue
        return None
    return tokens


def _normalize_parameter_declaration(value: str) -> str | None:
    candidate = unicodedata.normalize("NFC", value.strip())
    if not candidate:
        return None
    if candidate.startswith("final "):
        candidate = candidate[6:].lstrip()
    match = _PARAMETER_NAME.fullmatch(candidate)
    if match is not None:
        without_name = f"{match.group('type')}{match.group('dimensions')}"
        normalized = normalize_java_type(without_name)
        if normalized is not None:
            return normalized
    return normalize_java_type(candidate)


def _split_top_level_parameters(value: str) -> list[str] | None:
    stripped = value.strip()
    if not stripped:
        return []
    parts: list[str] = []
    start = 0
    angle_depth = 0
    bracket_depth = 0
    for index, character in enumerate(stripped):
        if character == "<":
            angle_depth += 1
        elif character == ">":
            angle_depth -= 1
        elif character == "[":
            bracket_depth += 1
        elif character == "]":
            bracket_depth -= 1
        elif character in "(){};":
            return None
        elif character == "," and angle_depth == 0 and bracket_depth == 0:
            part = stripped[start:index].strip()
            if not part:
                return None
            parts.append(part)
            start = index + 1
        if angle_depth < 0 or bracket_depth < 0:
            return None
    if angle_depth or bracket_depth:
        return None
    final = stripped[start:].strip()
    if not final:
        return None
    parts.append(final)
    return parts


def _stable_keys(values: Iterable[str]) -> list[str] | None:
    result = _stable_values(value for value in values if value)
    return result or None


def _stable_values(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _is_java_identifier_start(character: str) -> bool:
    return character in {"_", "$"} or character.isidentifier()


def _is_java_identifier_part(character: str) -> bool:
    return character in {"_", "$"} or f"A{character}".isidentifier()


def _is_identifier_token(token: str) -> bool:
    return bool(token) and _is_java_identifier_start(token[0])


def _valid_array_suffixes(value: str) -> bool:
    without_arrays = value.replace("[]", "")
    return "[" not in without_arrays and "]" not in without_arrays
