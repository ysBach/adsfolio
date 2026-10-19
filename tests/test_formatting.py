import unittest

from adsfolio.formatting import html_to_latex, html_to_text


class TestHtmlToLatex(unittest.TestCase):
    def test_basic_conversion(self):
        self.assertEqual(html_to_latex("Test"), "Test")
        self.assertEqual(html_to_latex(""), "")

    def test_escaping(self):
        self.assertEqual(html_to_latex("A & B"), r"A \& B")
        self.assertEqual(html_to_latex("50%"), r"50\%")

    def test_tags(self):
        self.assertEqual(html_to_latex("H<sub>2</sub>O"), r"H$_{2}$O")
        self.assertEqual(html_to_latex("E = mc<sup>2</sup>"), r"E = mc$^{2}$")
        self.assertEqual(html_to_latex("<i>Italic</i>"), r"\textit{Italic}")
        self.assertEqual(html_to_latex("<b>Bold</b>"), r"\textbf{Bold}")
        self.assertEqual(html_to_latex("<em>Em</em>"), r"\textit{Em}")
        self.assertEqual(html_to_latex("<strong>Strong</strong>"), r"\textbf{Strong}")

    def test_mixed(self):
        input_str = r"(3200) Phaethon polarimetry in the negative branch: new evidence for the anhydrous nature of the DESTINY<SUP>+</SUP> target asteroid"
        expected = r"(3200) Phaethon polarimetry in the negative branch: new evidence for the anhydrous nature of the DESTINY$^{+}$ target asteroid"
        self.assertEqual(html_to_latex(input_str), expected)

    def test_entities(self):
        self.assertEqual(html_to_latex("A &amp; B"), r"A \& B")
        self.assertEqual(html_to_latex("x &lt; y"), r"x < y")
        # Note: < and > are not escaped in LaTeX usually, except in specific contexts, but _escape_latex doesn't escape < >.

    def test_entities_inside_tags(self):
        self.assertEqual(html_to_latex("<b>A &amp; B</b>"), r"\textbf{A \& B}")

    def test_case_insensitive(self):
        self.assertEqual(html_to_latex("<SUP>x</SUP>"), r"$^{x}$")
        self.assertEqual(html_to_latex("<Sup>x</Sup>"), r"$^{x}$")
        self.assertEqual(html_to_latex("<SUB>21</SUB>"), r"$_{21}$")

    def test_mixed_case_attributes(self):
        self.assertEqual(html_to_latex('<sub class="foo">2</sub>'), r"$_{2}$")

    def test_latex_preservation(self):
        # Existing LaTeX math should be preserved
        self.assertEqual(html_to_latex(r"$\mathrm{H_2O}$"), r"$\mathrm{H_2O}$")
        self.assertEqual(
            html_to_latex(r"Study of CC<SUB>21</SUB> and $\mathrm{H_2O}$"),
            r"Study of CC$_{21}$ and $\mathrm{H_2O}$",
        )

        # Verify format_title_latex doesn't double-escape
        from adsfolio.formatting import format_title_latex

        title = r"SPHEREx Pre-Perihelion Mapping of $\mathrm{H_2O}$, $\mathrm{CO_2}$, and $\mathrm{CO}$"
        self.assertEqual(format_title_latex(title), title)

    def test_nested_same_and_mixed_tags(self):
        self.assertEqual(html_to_latex("<b>x<b>y</b>z</b>"), r"\textbf{x\textbf{y}z}")
        self.assertEqual(html_to_latex("<i>a<b>b</b></i>"), r"\textit{a\textbf{b}}")
        self.assertEqual(
            html_to_latex("<strong><em>c</em> d</strong>"),
            r"\textbf{\textit{c} d}",
        )
        self.assertEqual(html_to_latex("<B>x</b>"), r"\textbf{x}")

    def test_nested_sub_sup_share_one_math_group(self):
        self.assertEqual(html_to_latex("<sup>a<sub>b</sub></sup>"), "$^{a_{b}}$")
        self.assertEqual(html_to_latex("<sub>x<sup>2</sup></sub>"), "$_{x^{2}}$")
        self.assertEqual(html_to_latex("<sub>x<i>y</i></sub>"), r"$_{x\textit{y}}$")
        # A group inside text mode must open its own math mode.
        self.assertEqual(
            html_to_latex("<sup><i><sub>x</sub></i></sup>"),
            r"$^{\textit{$_{x}$}}$",
        )

    def test_math_is_preserved_inside_tags_and_across_tags(self):
        self.assertEqual(html_to_latex("<i>$x_1$</i>"), r"\textit{$x_1$}")
        self.assertEqual(html_to_latex("$a<b>c</b>$"), "$a<b>c</b>$")
        # Inside sub/sup the text is always escaped, so no math nests in math.
        self.assertEqual(html_to_latex("<sub>$x$</sub>"), r"$_{\$x\$}$")

    def test_unsupported_unmatched_and_unclosed_tags_stay_literal(self):
        self.assertEqual(html_to_latex("<i>a<u>b</u></i>"), r"\textit{a<u>b</u>}")
        self.assertEqual(html_to_latex("stray </b> end"), "stray </b> end")
        self.assertEqual(html_to_latex("<i>unclosed"), "<i>unclosed")
        # A closing tag keeps its outer match; the unclosed inner tag stays literal.
        self.assertEqual(html_to_latex("<i>a<b>b</i>c</b>"), r"\textit{a<b>b}c</b>")
        self.assertEqual(html_to_latex("x < y and y > z"), "x < y and y > z")

    def test_entities_are_decoded_once(self):
        self.assertEqual(html_to_latex("&amp;lt;"), r"\&lt;")
        self.assertEqual(html_to_latex("&lt;i&gt;lit&lt;/i&gt;"), "<i>lit</i>")

    def test_math_groups_typeset_caret_tilde_and_backslash_as_text(self):
        self.assertEqual(
            html_to_latex("<sup>a^b</sup>"), r"$^{a\mbox{\textasciicircum{}}b}$"
        )
        self.assertEqual(
            html_to_latex("<sub>x~y</sub>"), r"$_{x\mbox{\textasciitilde{}}y}$"
        )
        self.assertEqual(
            html_to_latex(r"<sub>a\b</sub>"), r"$_{a\mbox{\textbackslash{}}b}$"
        )
        # Characters that are valid in both modes keep their usual escape.
        self.assertEqual(html_to_latex("<sup>50%&amp;#_</sup>"), r"$^{50\%\&\#\_}$")

    def test_math_escapes_apply_to_text_nested_inside_math_groups(self):
        self.assertEqual(
            html_to_latex("<sup><i>a^b</i></sup>"),
            r"$^{\textit{a\mbox{\textasciicircum{}}b}}$",
        )
        self.assertEqual(
            html_to_latex("<sup>u<i>v^w</sup>"),
            r"$^{u<i>v\mbox{\textasciicircum{}}w}$",
        )

    def test_text_outside_math_groups_keeps_text_mode_escapes(self):
        self.assertEqual(html_to_latex("a^b ~ c"), r"a\^{}b \textasciitilde{} c")
        self.assertEqual(html_to_latex("<i>a^b</i>"), r"\textit{a\^{}b}")
        self.assertEqual(html_to_latex("x<sup>2</sup> ^ y"), r"x$^{2}$ \^{} y")

    def test_html_to_text_removes_markup_without_losing_subscript_content(self):
        self.assertEqual(
            html_to_text("98943 (2001 CC<SUB>21</SUB>) &amp; target"),
            "98943 (2001 CC21) & target",
        )


if __name__ == "__main__":
    unittest.main()


class TestEscapeAndAuthorFormatting(unittest.TestCase):
    def test_escape_latex_backslash_and_tilde_single_slash(self):
        self.assertEqual(html_to_latex(r"C:\path"), r"C:\textbackslash{}path")
        self.assertEqual(html_to_latex("a ~ b"), r"a \textasciitilde{} b")

    def test_format_author_list_cv_default_me_fmt(self):
        from adsfolio.formatting import format_author_list_cv

        names = ["Bach, Yoonsoo P."]
        res = format_author_list_cv(names, me_target="Bach, Y.", latex=True)
        self.assertEqual(res, r"\textbf{Bach, Y. P.}")

        # Custom template with plain {} placeholder remains exact without heuristics
        res_custom = format_author_list_cv(
            names, me_target="Bach, Y.", me_fmt="<b>{}</b>", latex=False
        )
        self.assertEqual(res_custom, "<b>Bach, Y. P.</b>")

    def test_format_author_list_cv_two_authors_use_and(self):
        from adsfolio.formatting import format_author_list_cv

        names = ["Author, One", "Author, Two"]
        # Baseline established behavior: two authors use "and" regardless of use_and string
        self.assertEqual(
            format_author_list_cv(names, use_and="and"), "Author, O. and Author, T."
        )
        self.assertEqual(
            format_author_list_cv(names, use_and=r"\&"), "Author, O. and Author, T."
        )

    def test_format_author_list_cv_multiple_authors_separators(self):
        from adsfolio.formatting import format_author_list_cv

        names = ["Author, One", "Author, Two", "Author, Three"]

        # Baseline established behavior: Oxford comma joins with ", and"
        res_comma = format_author_list_cv(names, authorsep=", ", oxford_comma=True)
        self.assertEqual(res_comma, "Author, O., Author, T., and Author, T.")

        res_semi = format_author_list_cv(names, authorsep="; ", oxford_comma=True)
        self.assertEqual(res_semi, "Author, O.; Author, T., and Author, T.")

        # oxford_comma=False joins with " and"
        res_no_oxford = format_author_list_cv(names, authorsep="; ", oxford_comma=False)
        self.assertEqual(res_no_oxford, "Author, O.; Author, T. and Author, T.")

        # use_and=None joins purely with authorsep
        res_no_and = format_author_list_cv(names, authorsep="; ", use_and=None)
        self.assertEqual(res_no_and, "Author, O.; Author, T.; Author, T.")

    def test_format_author_list_cv_regression_matches_docs_examples(self):
        from adsfolio.formatting import format_author_list_cv

        sample_names = [
            "Kim, James T.",
            "Doe, Jane",
            "Smith, John Quentin",
            "Longlastname, Alice Bob",
            "Lee, Charles",
        ]
        res = format_author_list_cv(
            sample_names,
            n_max=0,
            me_target="Kim, J.",
            authorsep=", ",
            latex=True,
        )
        self.assertEqual(
            res,
            r"\textbf{Kim, J. T.}, Doe, J., Smith, J. Q., Longlastname, A. B., and Lee, C.",
        )

    def test_format_author_list_cv_truncation_me_window(self):
        from adsfolio.formatting import format_author_list_cv

        names = [
            "Alpha, A.",
            "Beta, B.",
            "Gamma, C.",
            "Delta, D.",
            "Target, T.",
            "Zeta, Z.",
        ]
        res = format_author_list_cv(
            names,
            n_max=3,
            n_min=2,
            n_me=5,
            me_target="Target, T.",
            authorsep=", ",
            latex=False,
        )
        self.assertTrue(res.endswith("et al."))
        self.assertIn("Target, T.", res)
        self.assertNotIn("Zeta, Z.", res)
