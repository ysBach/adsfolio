__all__ = [
    "format_author_list_cv",
    "format_title_latex",
    "html_to_latex",
    "html_to_text",
]

import re
from html import unescape
from html.parser import HTMLParser


class _HTMLTextExtractor(HTMLParser):
    """Collect text content while discarding ADS display markup."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        """Append literal text between markup tags."""
        self.parts.append(data)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Keep visible separation for HTML line-break tags."""
        if tag.lower() in {"br", "p"}:
            self.parts.append(" ")


def html_to_text(text: str) -> str:
    """Return ADS HTML-like display text as plain Unicode text.

    Parameters
    ----------
    text
        ADS metadata that may contain markup such as ``<sub>`` or HTML entities.

    Returns
    -------
    str
        Plain text with tag contents preserved and markup removed.
    """
    if not text:
        return ""
    parser = _HTMLTextExtractor()
    parser.feed(unescape(text))
    parser.close()
    return "".join(parser.parts)


_MATH_ESCAPES = {
    "\\": r"\mbox{\textbackslash{}}",
    "^": r"\mbox{\textasciicircum{}}",
    "~": r"\mbox{\textasciitilde{}}",
}


def _escape_latex(s: str, *, in_math: bool = False, bibtex: bool = False) -> str:
    r"""Escape special characters for LaTeX.

    Parameters
    ----------
    s
        Text to escape.
    in_math
        The text is inside a math group such as ``$_{...}$``. The text-mode
        commands for backslash, ``^`` and ``~`` do not work there, so these
        characters are typeset as text with ``\mbox``.
    bibtex
        Use brace commands that keep BibTeX field delimiters balanced.
    """
    if not s:
        return ""

    repl = {
        "\\": r"\textbackslash{}",
        "&": r"\&",
        "%": r"\%",
        "$": r"\$",
        "#": r"\#",
        "_": r"\_",
        "{": r"\{",
        "}": r"\}",
        "^": r"\^{}",
        "~": r"\textasciitilde{}",
    }
    if in_math:
        repl |= _MATH_ESCAPES
    if bibtex:
        repl |= {
            "{": r"\lbrace{}" if in_math else r"\textbraceleft{}",
            "}": r"\rbrace{}" if in_math else r"\textbraceright{}",
        }
    return "".join(repl.get(ch, ch) for ch in s)


def format_title_latex(title: str) -> str:
    """
    Format a paper title for LaTeX.

    Escapes special characters like &, %, $, etc.
    """
    return html_to_latex(title)


def format_author_list_cv(
    names,
    n_max=10,
    n_min=3,
    n_me=10,
    me_target=None,
    me_fmt=r"\textbf{{{}}}",
    use_and=r"\&",
    authorsep="; ",
    oxford_comma=True,
    latex=True,
):
    r"""
    Format an author list in a CV / publication style, with optional truncation,
    self-author highlighting, and LaTeX output. The function is designed to work
    without any external dependencies (e.g., ADS), operating purely on name strings.

    Parameters
    ----------
    names : list of str
        Author names in the form "Last, First [Middle ...]".
        Example: "Smith, John Quentin"

    n_max : int, default=10
        Maximum number of authors to display.

        - If 0, all authors are shown.
        - If > 0 and the list exceeds this length, truncation may occur
          depending on n_min and n_me.

    n_min : int, default=3
        Minimum number of authors to show when truncation is applied.
        Used together with n_max to control how many leading authors
        are preserved before replacing the remainder with "et al.".

    n_me : int, default=10
        Upper position limit (1-based) up to which matching target authors
        are retained when truncation occurs.

    me_target : str, list, or None, default=None
        Short-form identifier(s) for the target author(s), typically "Last, F.".
        Can be a single string or a list of strings.
        If provided and matched, these authors are emphasized.

    me_fmt : str, default=r"\textbf{{{}}}"
        Formatting template to apply to target authors using
        ``me_fmt.format(author)``. For LaTeX output with literal braces,
        escape them as ``{{`` and ``}}``, with ``{}`` as the author name
        placeholder (e.g. ``r"\textbf{{{}}}"`` or ``r"\textit{{{}}}"``).
        Plain format strings such as ``"<b>{}</b>"`` or constant text such
        as ``"this is me"`` are also accepted. Set to `None` to disable
        highlighting.

    latex : bool, default=True
        If True, escape author names and targets for LaTeX using
        ``html_to_latex()``. If False, names are kept as plain strings.
        Target author highlighting (`me_fmt`) and author joining (`use_and`,
        `authorsep`) apply in both modes.

    authorsep : str, default="; "
        Separator between authors.

    use_and : str or None, default="\&"
        If None, the conjunction is omitted. All other values currently behave
        the same: the literal word "and" is used (2 authors: "A and B";
        more: "A; B, and C", or "A; B and C" without Oxford comma).

    oxford_comma : bool, default=True
        If True, an Oxford comma is applied (", and" before the final author).
        If False, no Oxford comma is used (" and" before the final author).

    Returns
    -------
    str
        A single formatted string representing the author list.

    Notes
    -----
    - First and middle names are reduced to initials.
    - Ordering of authors is preserved.
    - When truncated, the output ends with "et al.".
    - The function is intended for CVs, publication lists, and grant documents,
      where consistent formatting and self-author highlighting are required.

    Examples
    --------
    Full list, no truncation, LaTeX output with self highlighted::

        sample_names = [
            "Kim, James T.",
            "Doe, Jane",
            "Smith, John Quentin",
            "Longlastname, Alice Bob",
            "Lee, Charles"
        ]

        format_author_list_cv(
            sample_names,
            n_max=0,
            me_target="Kim, J.",
            latex=True
        )

    Output::

        \textbf{Kim, J. T.}; Doe, J.; Smith, J. Q.; Longlastname, A. B., and Lee, C.

    Truncated list with multiple targets (self and student)::

        format_author_list_cv(
            sample_names,
            n_max=3,
            n_min=2,
            n_me=3,
            me_target=["Kim, J.", "Doe, J."],
            latex=True
        )

    Output::

        \textbf{Kim, J. T.}; \textbf{Doe, J.}; et al.
    """

    def _format_single(entry: str) -> str:
        if "," not in entry:
            return entry.strip()
        last, first_middle = entry.split(",", 1)
        parts = [p.strip() for p in first_middle.strip().split() if p.strip()]
        initials = " ".join(f"{p[0]}." for p in parts)
        return f"{last.strip()}, {initials}" if initials else last.strip()

    # 1) Normalize and (optionally) escape names
    # Use local lambda to respect 'latex' flag of this function
    escape_fn = html_to_latex if latex else (lambda x: x)

    formatted = [escape_fn(_format_single(n)) for n in names]
    total = len(formatted)

    # 2) Prepare me-target matching on formatted strings
    targets = []
    if me_target:
        if isinstance(me_target, str):
            raw_targets = [me_target]
        else:
            raw_targets = list(me_target)

        targets = [html_to_latex(t) if latex else t for t in raw_targets]

    # Find all matches
    me_indices = []
    if targets:
        for i, s in enumerate(formatted):
            if any(s.startswith(t) for t in targets):
                me_indices.append(i)

    # 3) Decide truncation window (guarantee n_min before 'et al.')
    n_max_eff = float("inf") if n_max == 0 else n_max

    truncated = False
    cutoff = None

    # Retain target author matches up to 1-based position n_me (0-based index < n_me).
    valid_matches = [idx for idx in me_indices if idx < n_me]
    last_valid_match = max(valid_matches) if valid_matches else -1

    # Extend truncation cutoff to include the last target match when total authors exceed n_me.

    if valid_matches and total > n_me:
        cutoff = max(n_min, last_valid_match + 1)
        truncated = True
        window = formatted[:cutoff]
    elif total > n_max_eff:
        cutoff = n_min
        truncated = True
        window = formatted[:n_min]
    else:
        window = formatted

    # 4) Bold me if requested (preserve cutoff window)
    if me_fmt is not None and targets:

        def _maybe_bold(s: str) -> str:
            if any(s.startswith(t) for t in targets):
                return me_fmt.format(s)
            return s

        formatted = [_maybe_bold(s) for s in formatted]
        # Re-slice window from modified formatted array if truncated
        # (This relies on 'formatted' being the source of truth)
        if truncated and cutoff is not None:
            window = formatted[:cutoff]
        else:
            window = formatted

    # 5) Join logic
    if truncated:
        # Truncated lists typically omit "and" before et al.
        return authorsep.join(window) + f"{authorsep}et al."

    if use_and is None or len(window) < 2:
        return authorsep.join(window)
    if len(window) == 2:
        return f"{window[0]} and {window[1]}"
    if oxford_comma:
        return authorsep.join(window[:-1]) + ", and " + window[-1]
    else:
        return authorsep.join(window[:-1]) + " and " + window[-1]


_ENTITIES = {"&amp;": "&", "&lt;": "<", "&gt;": ">"}
_ENTITY_PATTERN = re.compile("|".join(_ENTITIES))
_MATH_TAGS = frozenset({"sub", "sup"})
_TAG = r"<(?P<close>/?)(?P<tag>sub|sup|i|b|em|strong)\b[^>]*>"
_TAG_PATTERN = re.compile(_TAG, re.IGNORECASE)
_TAG_OR_MATH_PATTERN = re.compile(
    rf"(?P<math>\$.*?\$)|{_TAG}", re.IGNORECASE | re.DOTALL
)


def _decode_entities(text: str) -> str:
    """Decode &amp;, &lt; and &gt; in one pass, so &amp;lt; becomes &lt;."""
    return _ENTITY_PATTERN.sub(lambda match: _ENTITIES[match.group()], text)


def html_to_latex(text: str, *, bibtex: bool = False) -> str:
    """Convert an HTML-like string to LaTeX.

    Handles the tags `<sub>`, `<sup>`, `<i>`, `<em>`, `<b>` and `<strong>`,
    which may be nested in any order. The entities `&amp;`, `&lt;` and `&gt;` are
    decoded once and the text is escaped for LaTeX. Existing `$...$` math is
    copied unchanged, also inside `<i>` or `<b>`. Inside `<sub>` or `<sup>` the text is
    always escaped, and a nested `<sub>` or `<sup>` continues the same math
    group (`_{...}` or `^{...}`) without a second pair of dollar signs.

    Other tags, stray closing tags, and tags that are never closed stay as
    literal text.

    Parameters
    ----------
    text : str
        Input string containing HTML tags.
    bibtex : bool, optional
        Write literal braces as LaTeX commands with balanced delimiters so
        the result can be stored in a BibTeX field. Existing dollar math is
        preserved.

    Returns
    -------
    str
        LaTeX formatted string.
    """
    if not text:
        return ""

    # Each open tag is (name, raw text, converted parts); the first entry is the root.
    stack: list[tuple[str | None, str, list[str]]] = [(None, "", [])]

    def in_math() -> bool:
        return any(name in _MATH_TAGS for name, _, _ in stack)

    def flatten_top() -> None:
        _, raw, parts = stack.pop()
        stack[-1][2].append(_escape_latex(raw, in_math=in_math(), bibtex=bibtex))
        stack[-1][2].extend(parts)

    position = 0
    while True:
        in_math_tag = any(name in _MATH_TAGS for name, _, _ in stack)
        pattern = _TAG_PATTERN if in_math_tag else _TAG_OR_MATH_PATTERN
        match = pattern.search(text, position)
        end = len(text) if match is None else match.start()
        chunk = _decode_entities(text[position:end])
        stack[-1][2].append(_escape_latex(chunk, in_math=in_math_tag, bibtex=bibtex))
        if match is None:
            break
        position = match.end()
        if pattern is _TAG_OR_MATH_PATTERN and match["math"]:
            stack[-1][2].append(match["math"])
            continue
        name = match["tag"].lower()
        if not match["close"]:
            stack.append((name, match.group(), []))
        elif any(open_name == name for open_name, _, _ in stack):
            while stack[-1][0] != name:
                flatten_top()
            _, _, parts = stack.pop()
            content = "".join(parts)
            if name in _MATH_TAGS:
                group = ("_" if name == "sub" else "^") + "{" + content + "}"
                nested = stack[-1][0] in _MATH_TAGS
                wrapped = group if nested else "$" + group + "$"
            elif name in ("i", "em"):
                wrapped = "\\textit{" + content + "}"
            else:
                wrapped = "\\textbf{" + content + "}"
            stack[-1][2].append(wrapped)
        else:
            stack[-1][2].append(
                _escape_latex(match.group(), in_math=in_math_tag, bibtex=bibtex)
            )

    while len(stack) > 1:
        flatten_top()
    return "".join(stack[0][2])
