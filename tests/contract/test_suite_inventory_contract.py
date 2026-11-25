"""Every inventory `forge providers inventory` writes must be readable by the suite.

AgentForge and the AI Infrastructure Suite take no code dependency on each
other; the contract between them is the ``provider-inventory.v1`` document,
whose schema the suite owns. This module checks every variant the command can
write against the suite's packaged schema and its reader, and checks that this
package's endpoint-locality rule agrees with the suite's on the shared table.

Not part of the offline matrix: it needs ``ai-infrastructure-suite``
installed, which the ``suite-contract`` CI job does from the suite's main
branch. Without it every test here skips. CI sets
``AGENTFORGE_REQUIRE_SUITE_CONTRACT=1`` so a missing suite fails instead of
passing a check that never ran.
"""

from __future__ import annotations

import importlib
import json
import os
from importlib.resources import files
from pathlib import Path
from typing import Any, Final

import pytest
from tests.inventory_variants import variants

from forge.models.endpoints import endpoint_is_local
from forge.models.inventory import render_inventory

REQUIRE_ENV: Final = "AGENTFORGE_REQUIRE_SUITE_CONTRACT"


def _import_or_skip(module: str) -> Any:
    try:
        return importlib.import_module(module)
    except ImportError:
        if os.environ.get(REQUIRE_ENV) == "1":
            pytest.fail(
                f"{module} is not installed and {REQUIRE_ENV}=1; "
                "a skip here would report success for a check that never ran."
            )
        pytest.skip(
            f"{module} is not installed; set {REQUIRE_ENV}=1 to make this a failure.",
            allow_module_level=True,
        )


inventory_reader = _import_or_skip("ai_infrastructure_suite.provider_inventory")
jsonschema = _import_or_skip("jsonschema")


def _suite_data(name: str) -> Any:
    return json.loads(
        files("ai_infrastructure_suite").joinpath(f"data/{name}").read_text(encoding="utf-8")
    )


VARIANTS = variants()


@pytest.mark.parametrize("name", sorted(VARIANTS))
def test_every_variant_validates_against_the_suites_schema(name: str) -> None:
    document = json.loads(render_inventory(VARIANTS[name]))
    validator = jsonschema.Draft202012Validator(_suite_data("provider-inventory.schema.json"))
    assert [error.message for error in validator.iter_errors(document)] == []


@pytest.mark.parametrize("name", sorted(VARIANTS))
def test_every_variant_is_accepted_by_the_suites_reader(name: str) -> None:
    text = render_inventory(VARIANTS[name])
    inventory = inventory_reader.parse_provider_inventory(text.encode("utf-8"))
    assert [p.provider_id for p in inventory.providers] == [
        p["provider_id"] for p in VARIANTS[name].document["providers"]
    ]


def test_the_suite_derives_the_hosting_this_package_would() -> None:
    for result in VARIANTS.values():
        inventory = inventory_reader.parse_provider_inventory(render_inventory(result).encode())
        for provider in inventory.providers:
            if provider.endpoint_origin is None:
                continue
            expected = "local" if endpoint_is_local(provider.endpoint_origin) else "hosted"
            if provider.hosting_declared == "hosted":
                expected = "hosted"
            assert provider.hosting == expected, provider.provider_id


def test_the_vendored_locality_table_equals_the_suites() -> None:
    vendored = json.loads(
        (
            Path(__file__).resolve().parents[1] / "fixtures" / "endpoint-locality-cases.json"
        ).read_text(encoding="utf-8")
    )
    assert vendored == _suite_data("endpoint-locality-cases.json")


def test_this_rule_agrees_with_the_suites_on_every_case() -> None:
    for case in _suite_data("endpoint-locality-cases.json")["cases"]:
        assert endpoint_is_local(case["url"]) is inventory_reader.origin_is_local(case["url"])
        assert endpoint_is_local(case["url"]) is case["local"], case["url"]
