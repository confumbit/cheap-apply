Run the full workflow from the project folder:

```powershell
.venv/Scripts/python.exe main.py
```

The existing scraper runs, the existing classifier updates its CSV, and the
new adapter calls `resume-tailor/resume_tailor.py run` sequentially for eligible
jobs. The scraper, classifier, tailor script, and tailor configuration are unchanged.

Eligibility uses `job_functions.EXPERIENCE` (currently 3). A minimum strictly
below that number, or an unspecified minimum, qualifies. Exactly 3 years does
not qualify when EXPERIENCE is 3. Malformed nonempty values are logged and skipped.
The full workflow reads `experience_range` because that is column AD, where
the current classifier writes its results.

To process an existing classified CSV without scraping or classifying again:

```powershell
.venv/Scripts/python.exe resume_pipeline.py jobs-2026-10-22.csv --dry-run
.venv/Scripts/python.exe resume_pipeline.py jobs-2026-10-22.csv
```

This standalone command prefers `min_years_experience` if present; otherwise
it uses column AD (`experience_range`). Use `--experience-column experience_range`
to explicitly use the current classifier's output when both columns exist.
Dry runs only report counts; they do not alter files or call NotebookLM.

Duplicates share a normalized posting URL, or normalized company, title, and
location. A company/title match with a nearly identical description (90% overlap
of five-word sequences, minimum 20 words) also groups board-specific location
labels. Case, punctuation, HTML, and common URL tracking parameters are normalized.
Different role titles remain separate unless they share a posting URL.
This is heuristic: separate requisitions with identical company/title/location
can group together, while substantially rewritten postings can remain separate.

Each group uses the fullest available description and gets one tailoring call.
Eligible duplicate rows receive the same absolute PDF path in `resume_pdf`;
the column is added if absent. Ineligible rows have an empty `resume_pdf`.
All original rows, their order, and other columns are retained. PDFs remain in
the tailor's configured `output_dir` (currently `resume-tailor/tailor-runs`).

CSV updates use atomic replacement after each group. Existing links to nonempty
files are reused on reruns, including for newly encountered duplicates in that
CSV. Keep those PDFs in place. Changed descriptions do not automatically invalidate
existing links; clear a group's links to request a new resume. Reuse is within
the supplied CSV; there is no separate cross-CSV cache.

Failures leave blank links and do not stop later groups. The batch reports an
error at the end if any groups failed or experience values were invalid, so
the main workflow returns a failure status while preserving completed links.
The existing tailor may select a PDF with remaining validation issues; its
`final/issues.txt` and `final/report.md` retain those details unchanged.

Offline checks:

```powershell
.venv/Scripts/python.exe -m unittest test_resume_pipeline -v
```
