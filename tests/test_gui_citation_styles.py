"""Tests for the custom citation styles editor dialog."""

from __future__ import annotations

import os
import uuid
from unittest.mock import patch

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

from adsapi.citations import (
    CitationStyle,
    initial_citation_styles,
)
from adsapi.gui.citation_styles import (
    TEMPLATE_HELP_TEXT,
    CitationStylesDialog,
)


@pytest.fixture(scope="module")
def application() -> QApplication:
    """Provide one Qt application for citation-style editor tests."""
    return QApplication.instance() or QApplication([])


RECORD = {
    "metadata": {
        "bibcode": "2024A&A...123..456B",
        "year": "2024",
        "title": ["Dust <SUP>2</SUP> dynamics"],
        "author": ["Bach, Yoonsoo P.", "Doe, Alice", "Roe, Bob"],
        "bibstem": ["A&A"],
        "pub": "Astronomy &amp; Astrophysics",
        "volume": "123",
        "page": ["456"],
    }
}


def test_template_help_text_completeness() -> None:
    """Template help text must cover placeholders, braces, missing fields, HTML, Unicode, no .pdf, and errors."""
    # Placeholders
    for field in [
        "bibcode",
        "year",
        "title",
        "first_author_surname",
        "first_author_initials",
        "authors",
        "authors_without_suffix",
        "journal",
        "bibstem",
        "pub",
        "volume",
        "page",
    ]:
        assert f"{{{field}}}" in TEMPLATE_HELP_TEXT

    # Literal braces
    assert "{" in TEMPLATE_HELP_TEXT and "}" in TEMPLATE_HELP_TEXT
    assert "{{" in TEMPLATE_HELP_TEXT and "}}" in TEMPLATE_HELP_TEXT

    # Missing field behavior
    assert (
        "empty string" in TEMPLATE_HELP_TEXT.lower()
        or "missing" in TEMPLATE_HELP_TEXT.lower()
    )

    # HTML conversion
    assert "html" in TEMPLATE_HELP_TEXT.lower()

    # Unicode / punctuation
    assert "unicode" in TEMPLATE_HELP_TEXT.lower()
    assert (
        "punctuation" in TEMPLATE_HELP_TEXT.lower()
        or "spaces" in TEMPLATE_HELP_TEXT.lower()
    )

    # No .pdf
    assert ".pdf" in TEMPLATE_HELP_TEXT

    # Save-blocking errors
    help_text = TEMPLATE_HELP_TEXT.lower()
    for error in [
        "unmatched braces",
        "unknown fields",
        "empty fields",
        "format specifiers",
        "conversions",
        "attribute/item access",
        "empty names",
        "empty templates",
        "empty ids",
        "invalid author settings",
        "duplicate names",
        "duplicate ids",
    ]:
        assert error in help_text


def test_template_help_button_and_accessible_description(
    application: QApplication,
) -> None:
    """Template help button and accessible description must match TEMPLATE_HELP_TEXT exactly."""
    with patch.object(QMessageBox, "critical") as mock_critical:
        dialog = CitationStylesDialog(list(initial_citation_styles()))
        assert not mock_critical.called

    assert dialog.template_help_button is not None
    assert dialog.template_help_button.toolTip() == TEMPLATE_HELP_TEXT
    assert dialog.template_help_button.accessibleDescription() == TEMPLATE_HELP_TEXT
    assert dialog.template_edit.accessibleDescription() == TEMPLATE_HELP_TEXT
    assert (
        dialog.help_view.toPlainText() == TEMPLATE_HELP_TEXT
        or TEMPLATE_HELP_TEXT in dialog.help_view.toPlainText()
    )


def test_initial_selection_does_not_preassign_current_style(
    application: QApplication,
    qtbot,
) -> None:
    """Initial selection signals must not see a blank editor as the current draft."""
    style = CitationStyle(id="s1", name="Solo", template="{year}")
    observed_states: list[tuple[int, int, bool]] = []
    original_handler = CitationStylesDialog._on_current_row_changed

    def record_selection_state(dialog: CitationStylesDialog, row: int) -> None:
        observed_states.append((row, dialog._current_index, dialog._is_updating_ui))
        original_handler(dialog, row)

    with (
        patch.object(
            CitationStylesDialog,
            "_on_current_row_changed",
            record_selection_state,
        ),
        patch.object(QMessageBox, "critical") as mock_critical,
    ):
        dialog = CitationStylesDialog([style])
    qtbot.addWidget(dialog)

    assert mock_critical.call_count == 0
    assert (0, -1, True) in observed_states
    assert dialog._current_index == 0
    assert dialog.template_edit.text() == "{year}"


def test_dialog_loads_styles_and_synchronizes_editor(application: QApplication) -> None:
    """Dialog loads initial styles, selects the first item, and populates editors."""
    styles = [
        CitationStyle(
            id="s1",
            name="Style 1",
            template="{bibcode}",
            author_format="surname",
            max_authors=5,
            authors_when_truncated=2,
            author_separator="; ",
            author_truncation_suffix=" and others",
        ),
        CitationStyle(
            id="s2",
            name="Style 2",
            template="{year}",
        ),
    ]
    dialog = CitationStylesDialog(styles)
    assert dialog.list_widget.count() == 2
    assert dialog.list_widget.currentRow() == 0
    assert dialog.name_edit.text() == "Style 1"
    assert dialog.template_edit.text() == "{bibcode}"
    assert dialog.author_format_combo.currentData() == "surname"
    assert dialog.max_authors_spin.value() == 5
    assert dialog.authors_when_truncated_spin.value() == 2
    assert dialog.authors_when_truncated_spin.maximum() == 5
    assert dialog.author_separator_edit.text() == "; "
    assert dialog.author_truncation_suffix_edit.text() == " and others"


def test_authors_when_truncated_maximum_synchronization(
    application: QApplication,
) -> None:
    """Max authors updates authors_when_truncated maximum and clamps value if needed."""
    style = CitationStyle(
        id="s1",
        name="Style 1",
        template="{bibcode}",
        max_authors=10,
        authors_when_truncated=8,
    )
    dialog = CitationStylesDialog([style])
    assert dialog.authors_when_truncated_spin.maximum() == 10
    dialog.max_authors_spin.setValue(5)
    assert dialog.authors_when_truncated_spin.maximum() == 5
    assert dialog.authors_when_truncated_spin.value() == 5


def test_save_contract_and_signal_emission(application: QApplication) -> None:
    """Saving emits saved signal with updated CitationStyle list and accepts dialog."""
    style = CitationStyle(id="s1", name="Old", template="{year}")
    dialog = CitationStylesDialog([style], preview_record=RECORD)
    emitted = []
    dialog.saved.connect(emitted.append)

    dialog.name_edit.setText("Readable")
    dialog.template_edit.setText("{first_author_initials} ({year})")
    dialog._save()

    assert dialog.result() == QDialog.Accepted
    assert len(dialog.styles) == 1
    assert dialog.styles[0].name == "Readable"
    assert dialog.styles[0].template == "{first_author_initials} ({year})"
    assert len(emitted) == 1
    assert emitted[0] == tuple(dialog.styles)


def test_save_preserves_leading_and_trailing_template_spaces(
    application: QApplication,
    qtbot,
) -> None:
    """Saving preserves literal spaces at both ends of a valid template."""
    style = CitationStyle(id="s1", name="Spaced", template="{year}")
    dialog = CitationStylesDialog([style])
    qtbot.addWidget(dialog)
    template = "  {year}  "

    dialog.template_edit.setText(template)
    dialog._save()

    assert dialog.result() == QDialog.Accepted
    assert dialog.styles[0].template == template


def test_cancel_rejects_without_emission(application: QApplication) -> None:
    """Cancel rejects dialog and does not emit saved signal."""
    style = CitationStyle(id="s1", name="Old", template="{year}")
    dialog = CitationStylesDialog([style])
    emitted = []
    dialog.saved.connect(emitted.append)

    dialog.name_edit.setText("Modified")
    dialog.reject()

    assert dialog.result() == QDialog.Rejected
    assert len(emitted) == 0


def test_add_style_creates_lowest_unused_name(application: QApplication) -> None:
    """Add button uses new_citation_style to generate lowest unused name."""
    s1 = CitationStyle(id="s1", name="My bibcode 1", template="{year}")
    s2 = CitationStyle(id="s2", name="My bibcode 2", template="{year}")
    dialog = CitationStylesDialog([s1, s2])

    dialog.add_button.click()
    assert dialog.list_widget.count() == 3
    assert dialog.list_widget.currentRow() == 2
    assert dialog.name_edit.text() == "My bibcode 3"
    assert dialog.styles[2].name == "My bibcode 3"


def test_duplicate_style_creates_copy_with_unique_name_and_fresh_id(
    application: QApplication,
) -> None:
    """Duplicate button creates copy with fresh UUID and 'Copy of <name>' unique name."""
    s1 = CitationStyle(id="s1", name="My Style", template="{year} {title}")
    dialog = CitationStylesDialog([s1])

    dialog.duplicate_button.click()
    assert dialog.list_widget.count() == 2
    assert dialog.list_widget.currentRow() == 1
    copy1 = dialog.styles[1]
    assert copy1.name == "Copy of My Style"
    assert copy1.template == "{year} {title}"
    assert copy1.id != "s1"
    assert len(copy1.id) > 0

    # Duplicate again to ensure Copy of Copy or numbered unique copy
    dialog.duplicate_button.click()
    assert dialog.list_widget.count() == 3
    copy2 = dialog.styles[2]
    assert copy2.name in (
        "Copy of My Style (2)",
        "Copy (2) of My Style",
        "Copy of Copy of My Style",
        "Copy 2 of My Style",
    )
    assert copy2.id != copy1.id and copy2.id != "s1"


def test_duplicate_retries_when_generated_id_collides(
    application: QApplication,
    qtbot,
) -> None:
    """Duplicate retries UUID generation until the new ID is unused."""
    collision_id = uuid.UUID(int=1).hex
    replacement_id = uuid.UUID(int=2).hex
    style = CitationStyle(id=collision_id, name="My Style", template="{year}")
    dialog = CitationStylesDialog([style])
    qtbot.addWidget(dialog)

    with patch(
        "adsapi.gui.citation_styles.uuid.uuid4",
        side_effect=[uuid.UUID(int=1), uuid.UUID(int=2)],
    ) as uuid4:
        dialog.duplicate_button.click()

    assert uuid4.call_count == 2
    assert [style.id for style in dialog.styles] == [collision_id, replacement_id]


def test_move_up_and_down(application: QApplication) -> None:
    """Move Up and Move Down reorder items in list and underlying styles."""
    s1 = CitationStyle(id="s1", name="First", template="{year}")
    s2 = CitationStyle(id="s2", name="Second", template="{bibcode}")
    s3 = CitationStyle(id="s3", name="Third", template="{title}")
    dialog = CitationStylesDialog([s1, s2, s3])

    assert dialog.list_widget.currentRow() == 0
    # Move up on top item is disabled or no-op
    assert not dialog.move_up_button.isEnabled()

    dialog.list_widget.setCurrentRow(1)
    assert dialog.move_up_button.isEnabled()
    assert dialog.move_down_button.isEnabled()

    dialog.move_up_button.click()
    assert dialog.list_widget.currentRow() == 0
    assert [s.name for s in dialog.styles] == ["Second", "First", "Third"]

    dialog.move_down_button.click()
    assert dialog.list_widget.currentRow() == 1
    assert [s.name for s in dialog.styles] == ["First", "Second", "Third"]


def test_delete_to_empty(application: QApplication) -> None:
    """Deleting all styles leaves list empty and disables editing controls."""
    s1 = CitationStyle(id="s1", name="Solo", template="{year}")
    dialog = CitationStylesDialog([s1])

    assert dialog.list_widget.count() == 1
    dialog.delete_button.click()

    assert dialog.list_widget.count() == 0
    assert len(dialog.styles) == 0
    assert not dialog.name_edit.isEnabled()
    assert not dialog.template_edit.isEnabled()
    assert not dialog.duplicate_button.isEnabled()
    assert not dialog.delete_button.isEnabled()
    assert not dialog.move_up_button.isEnabled()
    assert not dialog.move_down_button.isEnabled()

    # Saving empty styles is valid
    dialog._save()
    assert dialog.result() == QDialog.Accepted
    assert dialog.styles == ()


def test_delete_validates_current_draft_before_removing_style(
    application: QApplication,
    qtbot,
) -> None:
    """Delete blocks removal when the current editor draft is invalid."""
    style = CitationStyle(id="s1", name="Solo", template="{year}")
    dialog = CitationStylesDialog([style])
    qtbot.addWidget(dialog)
    dialog.template_edit.setText("{broken_field}")

    try:
        with patch.object(QMessageBox, "critical") as mock_critical:
            dialog.delete_button.click()

        assert mock_critical.called
        assert dialog.list_widget.count() == 1
        assert dialog.styles == (style,)
    finally:
        dialog.close()


def test_preview_does_not_mutate_styles_and_uses_custom_or_builtin_record(
    application: QApplication,
) -> None:
    """Preview button validates draft and renders against preview_record or built-in record."""
    s1 = CitationStyle(
        id="s1", name="Solo", template="{first_author_initials} ({year})"
    )
    dialog = CitationStylesDialog([s1], preview_record=RECORD)

    # Initial preview
    dialog.preview_button.click()
    assert dialog.preview_label.text() == "BachYP (2024)"
    assert dialog.styles[0].template == "{first_author_initials} ({year})"

    # Edit template without saving, preview updates preview_label
    dialog.template_edit.setText("{year}: {title}")
    dialog.preview_button.click()
    assert dialog.preview_label.text() == "2024: Dust 2 dynamics"
    # dialog.styles remains unchanged until stored / saved
    assert dialog.styles[0].template == "{first_author_initials} ({year})"


def test_preview_with_built_in_example_when_preview_record_is_none(
    application: QApplication,
) -> None:
    """Preview button uses labeled built-in example when preview_record is None."""
    s1 = CitationStyle(id="s1", name="Solo", template="{year} {bibcode}")
    dialog = CitationStylesDialog([s1], preview_record=None)

    dialog.preview_button.click()
    text = dialog.preview_label.text()
    assert len(text) > 0
    assert dialog.preview_note_label is not None
    assert "example" in dialog.preview_note_label.text().lower()


def test_validation_error_on_preview_shows_message_box(
    application: QApplication,
    qtbot,
) -> None:
    """Preview error displays QMessageBox.critical and does not crash."""
    s1 = CitationStyle(id="s1", name="Solo", template="{year}")
    dialog = CitationStylesDialog([s1])
    qtbot.addWidget(dialog)
    dialog.template_edit.setText("{unknown_field}")

    with patch.object(QMessageBox, "critical") as mock_critical:
        dialog.preview_button.click()
        assert mock_critical.called
        assert "unknown_field" in str(mock_critical.call_args)


@pytest.mark.parametrize("invalid_template", ["{name}", "{name.value}"])
def test_template_errors_focus_template_editor(
    application: QApplication,
    qtbot,
    invalid_template: str,
) -> None:
    """Template errors mentioning name still focus and select the template."""
    style = CitationStyle(id="s1", name="Solo", template="{year}")
    dialog = CitationStylesDialog([style])
    qtbot.addWidget(dialog)
    dialog.show()
    application.processEvents()
    dialog.template_edit.setText(invalid_template)

    with patch.object(QMessageBox, "critical") as mock_critical:
        dialog._save()

    assert mock_critical.called
    assert dialog.result() != QDialog.Accepted
    assert dialog.template_edit.hasFocus()
    assert dialog.template_edit.selectedText() == invalid_template


def test_validation_error_on_save_calls_critical_and_selects_affected_control(
    application: QApplication,
    qtbot,
) -> None:
    """Atomic save validates all styles; an error calls QMessageBox.critical, keeps dialog open, selects control."""
    s1 = CitationStyle(id="s1", name="Style 1", template="{year}")
    s2 = CitationStyle(id="s2", name="Style 2", template="{bibcode}")
    dialog = CitationStylesDialog([s1, s2])
    qtbot.addWidget(dialog)

    # Make second style template invalid
    dialog.show()
    application.processEvents()
    dialog.list_widget.setCurrentRow(1)
    dialog.template_edit.setText("{broken_field}")

    with patch.object(QMessageBox, "critical") as mock_critical:
        dialog._save()
        assert mock_critical.called
        assert dialog.result() != QDialog.Accepted
        assert dialog.list_widget.currentRow() == 1
        assert (
            dialog.template_edit.hasFocus()
            or dialog.template_edit == QApplication.focusWidget()
        )


def test_validation_error_duplicate_name(
    application: QApplication,
    qtbot,
) -> None:
    """Atomic save detects duplicate name across styles (case-folded and trimmed)."""
    s1 = CitationStyle(id="s1", name="Style 1", template="{year}")
    s2 = CitationStyle(id="s2", name="Style 2", template="{bibcode}")
    dialog = CitationStylesDialog([s1, s2])
    qtbot.addWidget(dialog)

    dialog.show()
    application.processEvents()
    dialog.list_widget.setCurrentRow(1)
    dialog.name_edit.setText(" style 1 ")

    with patch.object(QMessageBox, "critical") as mock_critical:
        dialog._save()
        assert mock_critical.called
        assert dialog.result() != QDialog.Accepted
        assert "duplicate" in str(mock_critical.call_args).lower()
        assert dialog.list_widget.currentRow() == 1
        assert (
            dialog.name_edit.hasFocus()
            or dialog.name_edit == QApplication.focusWidget()
        )


def test_save_updates_list_name_before_later_collection_validation(
    application: QApplication,
    qtbot,
) -> None:
    """A current rename is visible even when a later style makes save fail."""
    first = CitationStyle(id="s1", name="First", template="{year}")
    second = CitationStyle(id="s2", name="Renamed", template="{bibcode}")
    dialog = CitationStylesDialog([first, second])
    qtbot.addWidget(dialog)
    dialog.name_edit.setText("Renamed")

    with patch.object(QMessageBox, "critical") as mock_critical:
        dialog._save()

    assert mock_critical.called
    assert dialog.result() != QDialog.Accepted
    assert dialog.styles[0].name == "Renamed"
    assert dialog.list_widget.item(0).text() == "Renamed"
