"""RFC 8785 canonical JSON and MCP tool definition hashes. No SDK needed."""

from __future__ import annotations

import hashlib
import math

import pytest

from forge.tools.mcp.hashing import (
    HASHED_FIELDS,
    CanonicalizationError,
    canonical_json,
    definition_document,
    definition_sha256,
)

SCHEMA = {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        # RFC 8785 Appendix B (IEEE 754 doubles -> ECMAScript strings).
        (0.0, "0"),
        (-0.0, "0"),
        (5e-324, "5e-324"),
        (-5e-324, "-5e-324"),
        (1.7976931348623157e308, "1.7976931348623157e+308"),
        (-1.7976931348623157e308, "-1.7976931348623157e+308"),
        (9007199254740992.0, "9007199254740992"),
        (-9007199254740992.0, "-9007199254740992"),
        (295147905179352830000.0, "295147905179352830000"),
        (9.999999999999997e22, "9.999999999999997e+22"),
        (1e23, "1e+23"),
        (1.0000000000000001e23, "1.0000000000000001e+23"),
        (999999999999999700000.0, "999999999999999700000"),
        (999999999999999900000.0, "999999999999999900000"),
        (1e21, "1e+21"),
        (999999999999999.9, "999999999999999.9"),
        (1e-7, "1e-7"),
        (0.000001, "0.000001"),
        (333333333.3333333, "333333333.3333333"),
        (4.35, "4.35"),
        (100.0, "100"),
        (1, "1"),
        (-12, "-12"),
    ],
)
def test_numbers_follow_ecmascript(value: float, expected: str) -> None:
    assert canonical_json(value) == expected


def test_rfc8785_sorting_example() -> None:
    value = {
        "€": "Euro Sign",
        "\r": "Carriage Return",
        "דּ": "Hebrew Letter Dalet With Dagesh",
        "1": "One",
        "\U0001f600": "Emoji: Grinning Face",
        "\u0080": "Control",
        "ö": "Latin Small Letter O With Diaeresis",
    }
    expected = (
        '{"\\r":"Carriage Return","1":"One","\u0080":"Control",'
        '"ö":"Latin Small Letter O With Diaeresis","€":"Euro Sign",'
        '"\U0001f600":"Emoji: Grinning Face","דּ":"Hebrew Letter Dalet With Dagesh"}'
    )
    assert canonical_json(value) == expected


def test_rfc8785_literals_and_string_escapes() -> None:
    value = {
        "numbers": [333333333.33333329, 1e30, 4.50, 2e-3, 0.000000000000000000000000001],
        "string": '€$\u000f\nA\'B"\\\\"/',
        "literals": [None, True, False],
    }
    expected = (
        '{"literals":[null,true,false],"numbers":[333333333.3333333,1e+30,4.5,0.002,1e-27],'
        '"string":"€$\\u000f\\nA\'B\\"\\\\\\\\\\"/"}'
    )
    assert canonical_json(value) == expected


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf, 2**53, object(), {1: "x"}])
def test_values_without_a_canonical_form_are_refused(bad: object) -> None:
    with pytest.raises(CanonicalizationError):
        canonical_json(bad)


def test_definition_hash_covers_exactly_six_fields() -> None:
    document = definition_document("files", "search", description="Search.", input_schema=SCHEMA)
    assert tuple(document) == HASHED_FIELDS
    assert document["outputSchema"] is None and document["annotations"] is None
    expected = hashlib.sha256(canonical_json(document).encode("utf-8")).hexdigest()
    assert (
        definition_sha256("files", "search", description="Search.", input_schema=SCHEMA) == expected
    )


def test_any_hashed_change_moves_the_hash_and_key_order_does_not() -> None:
    base = definition_sha256("files", "search", description="Search.", input_schema=SCHEMA)
    reordered = {
        "required": ["query"],
        "properties": {"query": {"type": "string"}},
        "type": "object",
    }
    assert (
        definition_sha256("files", "search", description="Search.", input_schema=reordered) == base
    )
    variants = [
        definition_sha256("other", "search", description="Search.", input_schema=SCHEMA),
        definition_sha256("files", "find", description="Search.", input_schema=SCHEMA),
        definition_sha256("files", "search", description="Search!", input_schema=SCHEMA),
        definition_sha256("files", "search", description=None, input_schema=SCHEMA),
        definition_sha256(
            "files", "search", description="Search.", input_schema={**SCHEMA, "required": []}
        ),
        definition_sha256(
            "files",
            "search",
            description="Search.",
            input_schema=SCHEMA,
            output_schema={"type": "object"},
        ),
        definition_sha256(
            "files",
            "search",
            description="Search.",
            input_schema=SCHEMA,
            annotations={"readOnlyHint": True},
        ),
    ]
    assert base not in variants and len(set(variants)) == len(variants)


def test_definition_hash_is_pinned() -> None:
    """A golden value: any change to the hashing rule is a breaking change to every approval."""
    digest = definition_sha256("files", "search", description="Search.", input_schema=SCHEMA)
    canonical = (
        '{"annotations":null,"description":"Search.","inputSchema":{"properties":{"query":'
        '{"type":"string"}},"required":["query"],"type":"object"},"name":"search",'
        '"outputSchema":null,"server_id":"files"}'
    )
    assert digest == hashlib.sha256(canonical.encode("utf-8")).hexdigest()
