"""docs/provider-coverage.md must say what the preset data says, and no more."""

from __future__ import annotations

import re
from pathlib import Path

from forge.models.presets import load_built_in_presets

DOC = Path(__file__).resolve().parents[1] / "docs" / "provider-coverage.md"


def _rows() -> dict[str, list[str]]:
    rows = {}
    text = DOC.read_text(encoding="utf-8")
    # Only the Presets section: other tables on the page describe statuses.
    section = text.split("\n## Presets\n", 1)[1].split("\n## ", 1)[0]
    for line in section.splitlines():
        match = re.match(r"\| `([a-z0-9_]+)` \|", line)
        if match:
            rows[match.group(1)] = [cell.strip() for cell in line.strip("|").split("|")]
    return rows


def test_every_preset_has_a_row_and_no_row_is_invented() -> None:
    assert set(_rows()) == set(load_built_in_presets())


def test_each_rows_status_matches_the_preset_data() -> None:
    rows = _rows()
    for preset_id, preset in load_built_in_presets().items():
        _, hosting, jurisdiction, status, read, _ = rows[preset_id]
        assert hosting == preset.hosting, preset_id
        expected_jurisdiction = (
            "not applicable" if preset.jurisdiction == "not_applicable" else preset.jurisdiction
        )
        assert jurisdiction == expected_jurisdiction, preset_id
        if preset.review == "reviewed":
            assert status.startswith("`documented`"), preset_id
            assert preset.reviewed_on in read, preset_id
            assert preset.docs_url in read, preset_id
        else:
            assert status == "`untested`", preset_id
        assert ("partial" in status) == (
            preset.compatibility == "partial" and preset.review == "reviewed"
        ), preset_id


def test_no_preset_row_claims_to_be_fake_tested() -> None:
    # The fake server exercises the shared code path, not a vendor.
    for preset_id, cells in _rows().items():
        assert "fake-tested" not in cells[3], preset_id


def test_the_page_never_claims_a_vendor_was_tested() -> None:
    text = DOC.read_text(encoding="utf-8").lower()

    assert "no vendor's api has been contacted" in text
    for phrase in ("tested with", "verified with", "works with mistral", "supported by"):
        assert phrase not in text
    # The three words appear once, in the sentence that disowns them.
    assert text.count('"verified"') == 1
