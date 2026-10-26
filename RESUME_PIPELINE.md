Run the full workflow from the project folder:

```powershell
.venv/Scripts/python.exe main.py
```

The scraper runs, the experience classifier updates its CSV, and the local Ollama
title check adds `correct_domain`. A final routine adds `eligible`, then the adapter
calls `resume-tailor/resume_tailor.py run` sequentially for qualifying jobs.
The experience classifier, tailor script, and tailor configuration are unchanged.

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

`classify` performs all three checks: experience, title/domain, and final
eligibility. The resulting CSV is published together before tailoring starts.
`tailor` skips all classification checks and uses the existing CSV results.
Use `--start-at classify` to add or refresh correct_domain/eligible columns.

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

Eligibility uses `job_functions.EXPERIENCE` (currently 3). New filtered CSVs require
`correct_domain=true` AND a minimum experience at or below EXPERIENCE. A minimum
of 3 qualifies with EXPERIENCE=3; 4 does not. Unknown experience still qualifies
when the title check passes, preserving your earlier preference. Malformed values
do not qualify. Older CSVs without title-filter columns retain the adapter's
previous inclusive experience-only behavior when used directly. Classification,
resume selection and application selection share the inclusive experience policy
in `resume_pipeline.eligible()`; no separate strict check is applied downstream.
The full workflow reads `experience_range` because that is column AD, where
the current classifier writes its results.

Set `SEARCH_TERM` in `job_functions.py` to change the primary scraper search and
the title comparison target. The existing separate Google search parameter is
unchanged. A new workflow checkpoints its search term so resume uses the same
target even if settings are later edited. For an old checkpoint the current
SEARCH_TERM is used.

`check_job_titles(path, search_title=None)` sends only the target and job title to
the installed local model `qwen3.5:0.8b`. A short prompt with three examples helps
it accept the related roles you specified. Executive titles are rejected locally;
clear QA/Selenium/test-automation titles are also rejected locally for ML/AI
searches. Those QA exclusions do not apply to other search fields. You can
override `model` when calling this function. The experience classifier is unchanged.
AI/software/data roles are accepted as
related to machine learning engineering; unrelated roles are rejected. Ordinary
Senior/Lead/Staff/Principal titles are allowed, while explicit executive titles
(VP/AVP, chief/C-suite, director, head of department) are rejected. Executive and
empty titles are rejected locally; semantic checks use a small JSON boolean reply
with thinking disabled and at most 48 output tokens. See the
[Ollama structured-output API](https://github.com/ollama/ollama/blob/main/docs/api.md#structured-outputs).

Results are cached in `.workflow/title-cache.json` by search term, title, model,
endpoint and instructions. Duplicate titles and retries reuse successful checks.
An Ollama failure raises for the existing retry/recovery routine; it is not
recorded as a false domain decision. Pending rows have blank correct_domain and
false eligible until their check completes. The local model can still make
semantic mistakes; inspect these two columns for borderline titles.

`mark_eligible(path, experience=None, experience_column=None)` computes the final
column without model calls. Main runs both routines inside the classify stage,
after experience classification and before publishing its results. Resume generation and the application queue honor both new
columns when present. Standalone use on an already classified CSV:

```python
from job_functions import check_job_titles, mark_eligible
check_job_titles("jobs-2026-10-22.csv")
mark_eligible("jobs-2026-10-22.csv")
```

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
.venv/Scripts/python.exe -B -m unittest test_title_filter test_resume_pipeline test_workflow_recovery -v
```
