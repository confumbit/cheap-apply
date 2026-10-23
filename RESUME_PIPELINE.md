Run the full workflow from the project folder:

```powershell
.venv/Scripts/python.exe main.py
```

The existing scraper runs, the existing classifier updates its CSV, and the
new adapter calls `resume-tailor/resume_tailor.py run` sequentially for eligible
jobs. The scraper, classifier, tailor script, and tailor configuration are unchanged.

Running that command again automatically resumes `.workflow/state.json`.
Completed workflows remain completed; start another scrape explicitly:

```powershell
.venv/Scripts/python.exe main.py --new
```

Start at a chosen point using an existing CSV:

```powershell
.venv/Scripts/python.exe main.py --csv jobs-2026-10-22.csv --start-at classify
.venv/Scripts/python.exe main.py --csv jobs-2026-10-22.csv --start-at tailor
```

`--csv` seeds a new workflow at that stage. After an interruption, use plain
`main.py` to resume it, without repeating `--new` or `--csv`. Use `--checkpoint`
to select a separate workflow state file, and use the same checkpoint when resuming.
Run only one process against a given checkpoint/CSV at a time.

The console logs the current stage, CSV path, classification job and row number,
eligible/duplicate counts, current resume group, reused PDFs, and live tailor
output (including NotebookLM rounds and validation).

When an error occurs in an interactive terminal, the script pauses. Fix the
issue in another window, then press Enter to retry, or type `q` to stop and resume
later. For example, close the CSV in Excel or run `.venv/Scripts/notebooklm.exe login`
in another terminal. The error message includes authentication guidance when relevant.
Ctrl+C stops the workflow with exit code 130. Use `--non-interactive` to exit on
errors without prompting; redirected/no-console input also disables prompts.

The workflow invokes the unchanged experience classifier one job at a time,
on a private CSV under `.workflow/runs/`. Each classified row is saved with a
next-row checkpoint; completed rows are skipped on restart. Errors that the
classifier prints instead of raising also trigger a retry prompt. The public
CSV is published after scraping and after classification. If publication is
blocked, the private results are retained and publication alone is retried.
Don't edit the private work files while the workflow runs.

Eligibility uses `job_functions.EXPERIENCE` (currently 3). The current adapter's
existing `<= EXPERIENCE` rule is preserved: a minimum of 3 or less, or an
unspecified minimum, qualifies. Malformed nonempty values are logged and skipped.
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
CSV. Keep those PDFs in place. A sidecar named `<csv>.resume-progress.json`
records completed PDFs before CSV writes, so closing/restarting after a CSV lock
doesn't regenerate them. A pending run also records its existing output folders;
if the tailor finished a matching PDF before the parent was interrupted, that
PDF is recovered from its run folder. Resume directories are unchanged.

A cancelled scrape restarts that scrape; a cancelled classifier call retries
the current row. A tailor interrupted before producing a final PDF retries that
job from the beginning. Resuming NotebookLM inside a partially completed round
would require changes to the tailor, so the workflow does not do that.

Changed descriptions do not automatically invalidate existing CSV links. To
force regeneration, clear the group's CSV links and remove its record from the
resume sidecar (or use a fresh CSV and sidecar). There is no cross-CSV cache.

Interactive tailoring failures pause at the current job for repair and retry.
In noninteractive mode, failed groups leave blank links and later groups can
continue. The batch reports an error at the end if any groups failed or
experience values were invalid, preserving completed links and workflow state.
The existing tailor may select a PDF with remaining validation issues; its
`final/issues.txt` and `final/report.md` retain those details unchanged.

Offline checks:

```powershell
.venv/Scripts/python.exe -B -m unittest test_resume_pipeline test_workflow_recovery -v
```
