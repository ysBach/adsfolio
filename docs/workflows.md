# Daily workflows

ADSfolio helps with three tasks that researchers repeat often:

1. [Keep a BibTeX file for your paper](#keep-a-bibtex-file-for-your-paper),
   for example in Overleaf.
2. [Watch a folder and rename new papers](#watch-a-folder-and-rename-new-papers)
   automatically.
3. [Make your CV from your publications](#make-your-cv-from-your-publications)
   in LaTeX, HTML, Markdown, plain text, or your own format.

<video controls muted playsinline preload="metadata" width="100%"
  poster="_static/tutorial/export-ads-library.png">
  <source src="_static/tutorial/workflows.mp4" type="video/mp4">
</video>

The video shows these windows with fictional sample data. It also shows how
to save ADS fallback for repeated Identify runs.

## Keep ADS fallback enabled

1. Open **Identify → Advanced options…**.
2. Choose **All libraries** or **This library only**.
3. Enable **Allow ADS fallback**.
4. Choose **Save Defaults**.

Later Identify runs restore the saved choice. To change only the current run,
change the checkbox without saving. See [Identify defaults](gui.md#save-ads-fallback-for-later-runs).

## Keep a BibTeX file for your paper

Collect the papers you cite in a NASA ADS library. Then export that library as
BibTeX for LaTeX or Overleaf. ADS BibTeX uses the bibcode as the citation key,
for example `\cite{2024DEMO....1....1R}`.

1. Identify your PDFs. See [Getting started](getting-started.md).
2. Select papers, then choose **Tools → Add to ADS Library…**. Choose an
   existing ADS library or create one. ADSfolio adds papers to ADS libraries.
   It never removes papers from them.
3. Choose **Tools → Export ADS Library…**, select the library, keep
   **BibTeX**, and choose **Export**. Save the file, for example as
   `references.bib`.
4. Upload `references.bib` to your Overleaf project, or copy it next to your
   LaTeX file. Use `\bibliography{references}` in the document.

![Export ADS Library window with BibTeX selected](_static/tutorial/export-ads-library.png)

The export is a snapshot. Export again after you add papers. To update the
file automatically while you write, use the command-line library watcher. See
[Tutorial 3, step 4](cli-tutorials/03-library-workflows.md#4-watch-library-and-auto-refresh-bibtex).

## Watch a folder and rename new papers

Let ADSfolio identify and rename each PDF that arrives in a folder, for example
the folder where your browser saves papers.

1. Add the folder with **Add PDF Folder…** or **File → Add Library Root…**.
2. Right-click the library under **LIBRARIES**.
3. Choose **Watch Current Library** to start watching now.
4. Choose **Automatically Identify & Rename** to process new PDFs without a
   question. Without this choice, a notice offers **Identify & Rename** for
   each new PDF.
5. Optional: choose **Autowatch at Start** to watch the folder each time
   ADSfolio opens.

![Library menu with the three watch choices](_static/tutorial/watch-menu.png)

Renames use the saved settings from the **Rename** window. Set the filename
template there first, and check it with **Preview**. The watcher processes
only PDFs that arrive after watching starts. Existing PDFs stay unchanged.
Automatic renaming requires a verified
[PDF identity check](gui.md#pdf-identity-checks). A new PDF that states no
arXiv ID or DOI, or one that contradicts the found record, keeps its name and
shows a warning. A ``●``
beside the library name shows an active watcher. See
[Watching library roots](gui.md#watching-library-roots) for details. For the
command line, see [Tutorial 4, step 4](cli-tutorials/04-pdf-workflows.md#4-watch-a-folder-for-new-pdfs).

## Make your CV from your publications

**Tools → CV Generator…** makes a publication list from your NASA ADS records.

1. **Your publications:** choose **Fill from My Publication Profile**, or type
   your ADS query, ORCID iD, and name.
2. **Format:** choose a built-in LaTeX or Markdown style, or **Custom
   template**. A template starts from LaTeX, Markdown, HTML, or Plain text.
   Then you control every part:
   - **Highlight your name:** the markup around your name, for example
     `\textbf{{{}}}`, `<b>{}</b>`, or `**{}**`.
   - **Entry:** the text for one publication, with placeholders such as
     `{authors}`, `{year}`, `{title}`, `{venue}`, `{bibcode}`, `{url}`, and
     `{citations}`.
   - **Header, section start, section end, and footer:** the text around the
     list, for example `<ol>` and `</ol>`.
3. Check the **Preview**. **Sample papers** updates while you type.
   **Look Up in ADS** shows your real publications.
4. Choose **Save CV**.

![CV Generator with an HTML template and its preview](_static/tutorial/cv-generator.png)

This entry template makes one LaTeX line per paper:

```latex
\item {authors} ({year}) ``{title},'' \textit{{venue}}. \href{{url}}{\texttt{{bibcode}}}
```

The window keeps its settings in `cv.yaml`. See the [CV reference](cv.md) for
every option, and [Tutorial 6](cli-tutorials/06-cv-workflows.md) to make the CV
from a script.

### A list of any papers, without ADS

The same formats work for any papers in your library, for example a reading
list or a reference list for a proposal. Select the rows (**⌘**-click or
**Shift**-click), right-click, and choose **Export Selection As… → Publication list**. ADSfolio
uses the stored details and needs no ADS token.

![Export Selected Papers window with a rendered Markdown preview](_static/tutorial/export-selected.png)
