from types import SimpleNamespace

from adsfolio.pdf.annotations import (
    AnnotationRecord,
    MatchedAnnotation,
    apply_annotations,
    extract_annotations,
    match_annotations,
    normalize_text,
    port_annotations,
)


class _FakeCreatedAnnot:
    def __init__(self):
        self.colors = None
        self.opacity = None
        self.info = {}
        self.updated = False

    def set_colors(self, stroke=None):
        self.colors = stroke

    def set_opacity(self, opacity):
        self.opacity = opacity

    def set_info(self, content=""):
        self.info["content"] = content

    def update(self):
        self.updated = True


class _FakeAnnot:
    def __init__(self, type_code, rect, content="", color=None, opacity=0.5):
        self.type = (type_code, "")
        self.rect = rect
        self.info = {"content": content}
        self.colors = {"stroke": color}
        self.opacity = opacity


class _FakePage:
    def __init__(self, number, text, clip_text=None, annots=None, search_map=None):
        self.number = number
        self._text = text
        self._clip_text = clip_text or {}
        self._annots = annots or []
        self._search_map = search_map or {}
        self.created = []

    def get_text(self, mode, clip=None):
        if clip is not None:
            return self._clip_text.get(clip, "")
        return self._text

    def annots(self):
        return list(self._annots)

    def search_for(self, text, quads=True):
        return list(self._search_map.get(text, []))

    def add_highlight_annot(self, quads):
        annot = _FakeCreatedAnnot()
        self.created.append(("highlight", quads, annot))
        return annot

    def add_underline_annot(self, quads):
        annot = _FakeCreatedAnnot()
        self.created.append(("underline", quads, annot))
        return annot

    def add_strikeout_annot(self, quads):
        annot = _FakeCreatedAnnot()
        self.created.append(("strikeout", quads, annot))
        return annot

    def add_squiggly_annot(self, quads):
        annot = _FakeCreatedAnnot()
        self.created.append(("squiggly", quads, annot))
        return annot


class _FakeDoc:
    def __init__(self, pages):
        self._pages = pages
        self.saved_path = None
        self.closed = False

    def __iter__(self):
        return iter(self._pages)

    def __getitem__(self, index):
        return self._pages[index]

    def close(self):
        self.closed = True

    def save(self, path):
        self.saved_path = path


def test_normalize_text_collapses_whitespace_and_hyphenation():
    text = "A hy-\nphenated   sentence\nwith  spaces"
    assert normalize_text(text) == "A hyphenated sentence with spaces"


def test_extract_annotations_reads_portable_types_with_context(monkeypatch, tmp_path):
    source_path = tmp_path / "source.pdf"
    source_path.write_bytes(b"%PDF")
    page_text = "prefix context highlighted phrase suffix context"
    annot = _FakeAnnot(
        type_code=8,
        rect="r1",
        content="sticky note",
        color=(1.0, 0.0, 0.0),
        opacity=0.7,
    )
    page = _FakePage(
        number=0,
        text=page_text,
        clip_text={"r1": "highlighted phrase"},
        annots=[annot],
    )
    doc = _FakeDoc([page])
    monkeypatch.setattr(
        "adsfolio.pdf.annotations._import_fitz",
        lambda: SimpleNamespace(open=lambda _: doc),
    )

    records = extract_annotations(source_path, context_chars=10)
    assert len(records) == 1
    assert records[0].annotation_type == "Highlight"
    assert records[0].text == "highlighted phrase"
    assert records[0].content == "sticky note"
    assert records[0].context_before
    assert records[0].context_after


def test_match_annotations_prefers_context_anchored_page(monkeypatch, tmp_path):
    target_path = tmp_path / "target.pdf"
    target_path.write_bytes(b"%PDF")
    text = "important result"
    record = AnnotationRecord(
        annotation_type="Highlight",
        text=text,
        context_before="alpha beta gamma",
        context_after="delta epsilon zeta",
        color=None,
        content="",
        page_num=0,
        opacity=None,
        type_code=8,
    )

    page0 = _FakePage(
        number=0,
        text="alpha beta gamma important result delta epsilon zeta",
        search_map={text: ["quad-page0"]},
    )
    page1 = _FakePage(
        number=1,
        text="important result appears elsewhere",
        search_map={text: ["quad-page1"]},
    )
    target_doc = _FakeDoc([page0, page1])
    monkeypatch.setattr(
        "adsfolio.pdf.annotations._import_fitz",
        lambda: SimpleNamespace(open=lambda _: target_doc),
    )

    matched = match_annotations([record], target_path)
    assert len(matched) == 1
    assert matched[0].target_page == 0
    assert matched[0].target_quads == ["quad-page0"]


def test_apply_annotations_writes_output_and_preserves_fields(monkeypatch, tmp_path):
    target_path = tmp_path / "target.pdf"
    target_path.write_bytes(b"%PDF")
    output_path = tmp_path / "out.pdf"
    page = _FakePage(number=0, text="dummy")
    doc = _FakeDoc([page])
    monkeypatch.setattr(
        "adsfolio.pdf.annotations._import_fitz",
        lambda: SimpleNamespace(open=lambda _: doc),
    )

    matched = [
        MatchedAnnotation(
            source_index=0,
            source=AnnotationRecord(
                annotation_type="Underline",
                text="text",
                context_before="",
                context_after="",
                color=(0.1, 0.2, 0.3),
                content="note",
                page_num=0,
                opacity=0.6,
                type_code=9,
            ),
            target_page=0,
            target_quads=["quad"],
        )
    ]
    count = apply_annotations(matched, target_path=target_path, output_path=output_path)

    assert count == 1
    assert doc.saved_path == str(output_path)
    _, _, created = page.created[0]
    assert created.colors == (0.1, 0.2, 0.3)
    assert created.opacity == 0.6
    assert created.info["content"] == "note"
    assert created.updated is True


def test_port_annotations_reports_unported_records(monkeypatch, tmp_path):
    source_path = tmp_path / "source.pdf"
    target_path = tmp_path / "target.pdf"
    output_path = tmp_path / "out.pdf"
    source_path.write_bytes(b"%PDF")
    target_path.write_bytes(b"%PDF")

    records = [
        AnnotationRecord("Highlight", "one", "", "", None, "", 0, None, 8),
        AnnotationRecord("Highlight", "two", "", "", None, "", 0, None, 8),
    ]
    matched = [
        MatchedAnnotation(
            source_index=0,
            source=records[0],
            target_page=0,
            target_quads=["q"],
        )
    ]

    monkeypatch.setattr("adsfolio.pdf.annotations.extract_annotations", lambda _: records)
    monkeypatch.setattr(
        "adsfolio.pdf.annotations.match_annotations",
        lambda _records, _target: matched,
    )
    monkeypatch.setattr(
        "adsfolio.pdf.annotations.apply_annotations",
        lambda _matched, target_path, output_path: 1,
    )
    monkeypatch.setattr(
        "adsfolio.pdf.annotations._count_nonportable_annotations",
        lambda _: 2,
    )

    report = port_annotations(source_path, target_path, output_path)
    assert report.total_portable == 2
    assert report.ported == 1
    assert report.failed == 1
    assert report.skipped_nonportable == 2
    assert len(report.unported) == 1
    assert report.unported[0]["text"] == "two"
