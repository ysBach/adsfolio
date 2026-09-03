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


def _escape_latex(s: str) -> str:
    """Escape special characters for LaTeX."""
    if not s:
        return ""

    repl = {
        "\\": r"\\textbackslash{}",
        "&": r"\&",
        "%": r"\%",
        "$": r"\$",
        "#": r"\#",
        "_": r"\_",
        "{": r"\{",
        "}": r"\}",
        "^": r"\^{}",
        "~": r"\\textasciitilde{}",
    }
    out = []
    for ch in s:
        out.append(repl.get(ch, ch))
    return "".join(out)


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
    me_fmt=r"\\textbf{}",
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

    n_max : int, default=0
        Maximum number of authors to display.

        - If 0, all authors are shown.
        - If > 0 and the list exceeds this length, truncation may occur
          depending on n_min and n_me.

    n_min : int, default=0
        Minimum number of authors to show when truncation is applied.
        Used together with n_max to control how many leading authors
        are preserved before replacing the remainder with "et al.".

    n_me : int or None, default=None
        Index (1-based) of the target author ("me") in the original list.
        If provided, the function ensures that the target author is included
        in the displayed portion whenever truncation is applied, even if this
        requires extending beyond n_min.

    me_target : str, list, or None, default=None
        Short-form identifier(s) for the target author(s), typically "Last, F.".
        Can be a single string or a list of strings.
        If provided and matched, these authors are emphasized.

    me_fmt : str, default="\\textbf{}"
        LaTeX formatting string to apply to target authors. It wll be used
        as ``me_fmt.format(author)``. Thus, it should contain maximum one
        placeholder for the author name (``r"\\textbf{}"``,
        ``r"\\textit{}"`` for one placeholder and ``"this is me"`` for no
        placeholder).
        Set to `None` to disable highlighting.

    latex : bool, default=False
        If True, output is formatted for LaTeX:

        - Target author matches are wrapped in `me_fmt`
        - "and" is used before the final author
        - An Oxford comma is applied

        If False, a plain-text representation is returned.

    authorsep : str, default="; "
        Separator between authors.

    use_and : str or None, default="\&"
        String to use before the final author.
        If None, no "and" is used.

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

        \\textbf{Kim, J. T.}, Doe, J., Smith, J. Q., Longlastname, A. B., and Lee, C.

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

        \\textbf{Kim, J. T.}, \\textbf{Doe, J.} et al.
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

    # Identify the LAST matching author within the allowable range (n_me)
    # matching indices 0-based, n_me is 1-based count (or is it?)
    # Docstring says "n_me : int ... Index (1-based) ... ensures that the target author is included"
    # So if n_me=5, we allow up to index 4.

    valid_matches = [idx for idx in me_indices if idx < n_me]
    last_valid_match = max(valid_matches) if valid_matches else -1

    # Logic: if we have a valid match ("me") and the list is long enough to trigger
    # potential aggressive truncation (longer than n_me, assuming n_me is our separate threshold for importance),
    # we override the standard cut.
    # Note: Using valid_matches logic similar to previous code's (0 <= me_index < n_me)

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


def html_to_latex(text: str) -> str:
    """
    Convert an HTML-like string to LaTeX.

    Handles common tags: <sub>, <sup>, <i>, <b>, <em>, <strong>.
    Also handles HTML entities &amp;, &lt;, &gt; by converting them
    to characters and then escaping for LaTeX.

    Parameters
    ----------
    text : str
        Input string containing HTML tags.

    Returns
    -------
    str
        LaTeX formatted string.
    """
    if not text:
        return ""

    # Regex to split by tags OR LaTeX math blocks.
    # We capture the tag name to identify it, and use backreference for the closing tag.
    # Group structure: (full_match (html_tag_name) (math_block) )
    # But re.split with capturing groups returns all groups.
    # Group 1: full match
    # Group 3: named group 'tag'
    # Group 4: named group 'math'
    pattern = (
        r"(<(?P<tag>sub|sup|i|b|em|strong)\b[^>]*>.*?</(?P=tag)>|(?P<math>\$.*?\$))"
    )

    chunks = re.split(pattern, text, flags=re.IGNORECASE | re.DOTALL)

    # re.split returns:
    # [plain0, full_match1, tag_name1, math_block1, plain1, ...]
    # So we iterate in steps of 4.

    out = []
    i = 0
    n = len(chunks)

    while i < n:
        # 1. Plain text part
        plain = chunks[i]
        if plain:
            # Decode common entities first
            plain = (
                plain.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
            )
            out.append(_escape_latex(plain))

        i += 1
        if i >= n:
            break

        # 2. Match (HTML Tag or Math Block)
        full_match = chunks[i]
        tag_name = chunks[i + 1]  # The named group 'tag'
        math_block = chunks[i + 2]  # The named group 'math'

        if math_block:
            # Preserve existing LaTeX math blocks as-is
            out.append(math_block)
        elif tag_name:
            # Extract content from HTML tag
            # <TAG ...>content</TAG>
            # We find the indices of the first > and last < to extract content.
            start = full_match.find(">") + 1
            end = full_match.rfind("<")

            content = ""
            if start > 0 and end > start:
                content = full_match[start:end]

            content = (
                content.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
            )
            escaped_content = _escape_latex(content)

            t = tag_name.lower()
            if t == "sub":
                out.append(f"$_{{{escaped_content}}}$")
            elif t == "sup":
                out.append(f"$^{{{escaped_content}}}$")
            elif t in ("i", "em"):
                out.append(f"\\textit{{{escaped_content}}}")
            elif t in ("b", "strong"):
                out.append(f"\\textbf{{{escaped_content}}}")
            else:
                # Fallback
                out.append(_escape_latex(full_match))

        # Skip the full_match, tag_name, and math_block groups
        i += 3

    return "".join(out)
