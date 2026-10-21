"""First-run guide shown while no library folder has been added."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class WelcomePanel(QWidget):
    """Explain the three setup steps and what ADSfolio sends over the network.

    Only the first step is required. Token and profile steps show whether a
    value already exists, so returning users can skip them.
    """

    add_folder_requested = Signal()
    token_requested = Signal()
    profile_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 24, 24, 24)
        outer.addStretch(1)
        card = QFrame(self)
        card.setFrameShape(QFrame.StyledPanel)
        # A fixed width lets wrapped text report its true height to the layout.
        card.setFixedWidth(620)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)

        title = QLabel("Welcome to ADSfolio", card)
        font = title.font()
        font.setPointSize(font.pointSize() + 6)
        font.setBold(True)
        title.setFont(font)
        layout.addWidget(title)
        intro = QLabel(
            "Organize the research PDFs you already have. Adding a folder and "
            "identifying its papers keep your PDFs' names and locations.",
            card,
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        steps = QGridLayout()
        steps.setHorizontalSpacing(12)
        steps.setVerticalSpacing(10)
        self.add_folder_button = QPushButton("Add PDF Folder…", card)
        self.add_folder_button.setDefault(True)
        self.add_folder_button.setToolTip(
            "Select a PDF folder, then choose OK to identify its papers."
        )
        self.add_folder_button.clicked.connect(self.add_folder_requested)
        self.token_button = QPushButton("Add ADS Token…", card)
        self.token_button.clicked.connect(self.token_requested)
        self.profile_button = QPushButton("Set Profile…", card)
        self.profile_button.clicked.connect(self.profile_requested)
        self.token_status = QLabel(card)
        self.profile_status = QLabel(card)
        rows = (
            (
                "1",
                "Add a PDF folder",
                "Your files stay where they are.",
                None,
                self.add_folder_button,
            ),
            (
                "2",
                "Optional: NASA ADS token",
                "Needed for ADS lookups. Crossref and DataCite work without it.",
                self.token_status,
                self.token_button,
            ),
            (
                "3",
                "Optional: publication profile",
                "Your ORCID and name forms help ADSfolio find your own papers.",
                self.profile_status,
                self.profile_button,
            ),
        )
        for row, (number, heading, detail, status, button) in enumerate(rows):
            badge = QLabel(number, card)
            badge.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
            badge_font = badge.font()
            badge_font.setBold(True)
            badge.setFont(badge_font)
            steps.addWidget(badge, row, 0)
            heading_label = QLabel(heading, card)
            heading_font = heading_label.font()
            heading_font.setBold(True)
            heading_label.setFont(heading_font)
            detail_label = QLabel(detail, card)
            box = QVBoxLayout()
            box.setSpacing(2)
            box.addWidget(heading_label)
            box.addWidget(detail_label)
            if status is not None:
                status.setTextInteractionFlags(Qt.TextSelectableByMouse)
                box.addWidget(status)
            steps.addLayout(box, row, 1)
            steps.addWidget(button, row, 2, Qt.AlignTop)
        steps.setColumnStretch(1, 1)
        layout.addLayout(steps)

        self.network_note = QLabel(
            "At startup, ADSfolio asks GitHub whether a newer version exists. "
            "Turn this off in Help → Automatically Check for Updates. Paper "
            "lookups contact NASA ADS, Crossref, and DataCite. PDFs are not "
            "uploaded.",
            card,
        )
        self.network_note.setWordWrap(True)
        note_font = self.network_note.font()
        note_font.setPointSize(max(1, note_font.pointSize() - 1))
        self.network_note.setFont(note_font)
        layout.addWidget(self.network_note)

        # Reserve the wrapped height at the fixed width; minimum sizes alone
        # measure wrapped labels at another width and clip the rows.
        card.setMinimumHeight(card.heightForWidth(card.width()))
        row = QVBoxLayout()
        row.setAlignment(Qt.AlignHCenter)
        row.addWidget(card)
        outer.addLayout(row)
        outer.addStretch(2)
        self.set_status(token_source=None, profile_saved=False)

    def set_status(self, *, token_source: str | None, profile_saved: bool) -> None:
        """Show whether the optional token and profile already exist."""
        self.token_status.setText("Token found." if token_source else "No token yet.")
        self.token_status.setToolTip(f"From {token_source}" if token_source else "")
        self.token_button.setText("Change Token…" if token_source else "Add ADS Token…")
        self.profile_status.setText(
            "Profile saved." if profile_saved else "No profile yet."
        )
        self.profile_button.setText(
            "Edit Profile…" if profile_saved else "Set Profile…"
        )
