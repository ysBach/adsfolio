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

    def test_html_to_text_removes_markup_without_losing_subscript_content(self):
        self.assertEqual(
            html_to_text("98943 (2001 CC<SUB>21</SUB>) &amp; target"),
            "98943 (2001 CC21) & target",
        )


if __name__ == "__main__":
    unittest.main()
