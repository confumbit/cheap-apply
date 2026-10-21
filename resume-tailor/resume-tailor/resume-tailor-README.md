# Resume Tailor: NotebookLM prompt + LaTeX validator loop

Tailors the Experience / Projects / Skills section of a LaTeX resume to one job description. NotebookLM writes the content and a local Python script compiles, measures and checks it. Any problems go back to NotebookLM as precise, machine-generated feedback until the output is clean.

**Goal:** get through ATS and LLM screeners by surfacing the JD's keywords inside the candidate's *real* projects. The JD should not replace what the candidate actually did.

---

## 1. Why this exists (the problem it fixes)

The original one-paragraph-per-step prompt made NotebookLM produce resumes that:

| Failure | Example from the first NotebookLM output (`main.pdf`) |
| --- | --- |
| **Mutated projects** | The WhatsApp order parser became a "multi-agent RAG ingestion system"; the Kia conference app became an "ML Engagement App" |
| **Invented skills** | PyTorch, TensorFlow, GCP, "hallucination rates", "cloud infrastructure governance" appeared with no basis in any source |
| **Filler** | "intelligent", "with high precision", "ensuring high throughput", "robust feature matching" |
| **Keyword lists instead of evidence** | Bullets that list competencies rather than describing something built |
| **Broke the layout** | +3 rendered lines, a second page, an extra bullet, and project headings that wrapped |

The redesign rests on three ideas:
1. **Reframe rather than replace.** Keep each project's identity and describe it in the JD's vocabulary.
2. **A truth model** (Tier 1/2/3) that says exactly how far the model may go beyond the sources.
3. **Don't trust the LLM to count.** Compile the LaTeX and measure the real PDF, then send exact numbers back.

---

## 2. Files

| File | What it is | Where it goes |
| --- | --- | --- |
| `resume-modifier.md` | Full prompt (~18k chars): truth model, metrics, titles, project selection, ATS strategy, anti-filler rules, length budget, validator protocol, output format, checklist | NotebookLM **source** titled `resume-modifier` |
| `resume-modifier-chat.md` | Condensed rules (~7.5k chars, under the 10k limit) | NotebookLM **Configure chat → Custom** (`setup` pushes it automatically) |
| `resume_tailor.py` | Driver + validator. Commands: `setup`, `run`, `check`, `add-jd` | Your resume folder or anywhere |
| `resume_tailor.example.json` | Config template, already set up for this resume | Copy to `resume_tailor.json` |
| `expandskills.original.tex` | The **original** Experience + Skills section, rebuilt from the original PDF and verified to render the same 50 lines | Replace your current `expandskills.tex` with it |
| `resume-modifier-usage.md` | Short how-to (a subset of this README) | Reference |

Other NotebookLM sources (unchanged, they are the candidate data):
- `experience-skills-section`: must be **identical** to `expandskills.tex`
- `projects-list.txt`, `skills-list.txt`, `questions-list.txt`
- At most one `JD - <company> - <role>` source at a time

---

## 3. Requirements

- **Python 3.10+.** `notebooklm-py` (tested API: v0.8.5) is needed only for `setup`, `run` and `add-jd`; `check` runs offline with the standard library alone.
- **pdflatex** and **pdftotext.** MiKTeX ships both. The template needs `marvosym`, `titlesec`, `enumitem`, `fancyhdr`, `lmodern`, `accsupp` and `glyphtounicode`. If MiKTeX is not set to install packages on the fly, install any missing ones first, for example `miktex packages install marvosym`.
- A logged-in NotebookLM session: run `notebooklm login` once.

---

## 4. Setup (one time)

1. **Restore the original section.** Your current `expandskills.tex` is the hallucinated NotebookLM version. Replace it with `expandskills.original.tex` (rename it to `expandskills.tex`). This file is the baseline that every run is measured against.
2. **Sync NotebookLM sources:**
   - Make `experience-skills-section` match `expandskills.tex` exactly.
   - Upload `resume-modifier.md` as a source titled `resume-modifier`.
3. **Install and log in:**
   ```
   pip install notebooklm-py
   notebooklm login
   ```
4. **Configure:** copy `resume_tailor.example.json` to `resume_tailor.json` and set `notebook_id` (from the notebook URL) and `resume_dir` (the folder that holds `main.tex` and `expandskills.tex`). Everything else is already filled in.
5. **Push the chat config and verify the sources:**
   ```
   python resume_tailor.py setup
   ```
   This sets Configure chat to *Custom* + *Longer* with `resume-modifier-chat.md`, warns about any source title it can't find, and writes the current region to `tailor-runs/experience-skills-section.txt` so you can diff it against the NotebookLM source.

---

## 5. Usage

### A. Automated (recommended)
```
python resume_tailor.py run --company "Acme" --role "Backend Engineer" --jd jd.txt
```
What happens:
1. The original resume is compiled to get the baseline: page count and rendered line count.
2. The previous chat is backed up to `previous-chat-backup.json` and deleted (`fresh_conversation`).
3. Any old `JD - ` source is deleted and this JD is uploaded as `JD - Acme - Backend Engineer`.
4. NotebookLM is asked, restricted to the pinned sources (the five candidate/instruction sources plus this JD).
5. The answer is parsed: the LaTeX block is extracted, `[n]` citations are stripped, and the rest is kept as the report.
6. Static checks run, the LaTeX is compiled into `main.tex`, and the rendered lines are measured.
7. If there are blocking or soft issues, a `VALIDATOR FEEDBACK` message is sent in the **same** conversation. Steps 5-7 repeat up to `max_rounds` (default 4).
8. The best round is picked and copied to `final/`.

Output folder: `tailor-runs/<timestamp>-<company>-<role>/`

| File | Contents |
| --- | --- |
| `final/resume.pdf`, `final/resume.tex` | Full compiled resume |
| `final/section.tex` | The new `expandskills.tex` content |
| `final/report.md` | NotebookLM's report: title choice, keywords covered/skipped, **Interview prep** (every extension and estimated number to be ready to defend) |
| `final/issues.txt` | Anything still unresolved |
| `roundN-answer.md`, `roundN-feedback.txt`, `jd.txt`, `work/` | Audit trail |

### B. Web UI with offline checking
1. Upload the JD: run `python resume_tailor.py add-jd --company "Acme" --role "Backend Engineer" --jd jd.txt`. It also prints the exact message to send. Alternatively, add it by hand as *Copied text* titled `JD - Acme - Backend Engineer`, and delete or untick any older `JD - ` source.
2. Start a **new chat** and send the printed message.
3. Save the whole answer to `answer.txt` and run `python resume_tailor.py check answer.txt`.
4. Paste the printed `VALIDATOR FEEDBACK` block back into the chat. Repeat until it reports no issues.

`check` also accepts a raw LaTeX section without a code fence, which is handy for checking your own hand edits.

---

## 6. Configuration reference (`resume_tailor.json`)

| Key | Default | Meaning |
| --- | --- | --- |
| `notebook_id` | — | NotebookLM notebook ID |
| `profile` | `null` | notebooklm-py storage profile (for multiple Google accounts) |
| `source_titles` | 5 titles | Sources pinned to every question. Titles are matched case-insensitively, with or without `.txt/.md/.tex/.pdf` |
| `custom_prompt_file` | `resume-modifier-chat.md` | Pushed by `setup`; must be ≤ 10,000 chars |
| `resume_dir` | `.` | Folder with `main.tex`; relative paths resolve from the config file's folder |
| `main_tex` | `main.tex` | Template file |
| `section_file` | `""` (example: `expandskills.tex`) | If set, the region is this file and is inlined in place of its `\input{...}` line. If empty, `main.tex` must contain `%% TAILOR-START` / `%% TAILOR-END` |
| `output_dir` | `tailor-runs` | Where runs are written |
| `latex_engine`, `pdftotext` | `pdflatex`, `pdftotext` | Full paths if they are not on PATH |
| `immutable_strings` | companies, location, dates | Must appear verbatim (whitespace-normalized) in the output |
| `bullet_macros` | `resumeItem`, `item` | What counts as a bullet |
| `chars_per_line` | 100 | Lower bound for the short-last-line estimate (auto-raised from the original bullets) |
| `max_bullet_chars` | 205 | Lower bound for LONG_BULLET (auto-raised to the longest original bullet) |
| `heading_max_chars` | 95 | Project heading length before it wraps |
| `short_last_line_chars` | 25 | A last line shorter than this is called a cheap line to cut |
| `underflow_min_lines` | 3 | Offer extra room once, if the output is this many lines shorter |
| `max_rounds` | 4 | Ask/feedback rounds |
| `fresh_conversation` | `true` | Back up, then **delete**, the notebook's current chat before a run |
| `jd_as_source` | `true` | Upload the JD as a source instead of putting it in the message |
| `jd_source_prefix` | `JD - ` | Any source starting with this is treated as a JD and deleted on upload |
| `banned_phrases` | regex list | Filler detector; edit freely |

---

## 7. How it works, and why

### 7.1 Prompt design (`resume-modifier.md` + `resume-modifier-chat.md`)

**Why two files.** NotebookLM treats sources as retrieval material: long documents get chunked, and only the relevant chunks reach the model. The *Configure chat* prompt is applied on every turn but is capped at 10k chars. So the non-negotiable rules (truth model, length, output format, feedback protocol) go in the chat config, where they are guaranteed to be applied. Detail and worked examples live in the source. The trigger message names the `resume-modifier` source explicitly so it gets retrieved.

**Truth model.** This is the core fix for hallucination:
- **Tier 1, stated:** directly in a candidate source. Always allowed.
- **Tier 2, reframed:** the same fact in the JD's vocabulary or at a different level of abstraction (e.g. "WhatsApp order parser" → "event-driven ingestion pipeline"). Encouraged.
- **Tier 3, plausible extension:** a tool or technique not in the sources. It must fit the original project's purpose, be something the candidate could explain in an interview, and be a concrete mechanism rather than a vague capability. Allowed at most once per bullet, and every one is listed in the report under *Interview prep*.
- **Forbidden:**
  - renaming a project into a different one
  - skills with no source or bullet backing
  - treating JD text as evidence about the candidate

**The interview test** ("could the candidate walk an interviewer through this?") is the single rule the model applies to every bullet.

**Skipping is allowed.** A missing keyword costs less than a fabrication a screener or interviewer catches. Skipped must-haves are reported so you can decide.

**ATS placement order:** role title → project heading tech list → first bullet under each role → skills section. Every must-have keyword should appear in the skills section *and* as evidence in a bullet. Each keyword is used at most 3 times.

**Anti-filler rules.** Concrete bad/good example pairs, a banned-word list, and a bullet shape of *what was built + how + for whom / at what scale*. Metrics are conservative: at most 3 estimated numbers, all reported.

**Length budget in characters.** NotebookLM can't compile, so it gets per-element capacities (≈100-110 visible chars per line, headings ≤ 95) and must not add bullets, headings, categories or spacing.

**Validator feedback protocol.** The prompt defines every issue code and the order in which to cut lines. That lets the script's terse feedback ("OVERFLOW: 3 lines") turn into the right edits without NotebookLM rewriting everything.

**JD handling.** The JD comes either from the message or from the one source the message names. It is the employer's description, never candidate data, and the model must read all of it.

### 7.2 Script architecture (`resume_tailor.py`)

```
JD ──► upload_jd ──► chat.ask ──► extract_answer ──► static_checks ─┐
                         ▲                                           ▼
                         │                         compile_tex ──► layout_rows ──► evaluate
                         └──────── Evaluation.feedback() ◄── issues? ◄────────────┘
                                                              │ no / max rounds
                                                              ▼
                                                  pick_best ──► finalize (final/)
```

| Piece | How | Why |
| --- | --- | --- |
| `load_template` / `split_template` | Splits `main.tex` into prefix + region + suffix. With `section_file`, the region is that file's text and it replaces the `\input{expandskills.tex}` line (commented-out `\input`s are ignored) | Lets each candidate section be compiled into the real template without touching your files |
| `measure_baseline` | Compiles the original once per run | Every comparison is relative to *your* resume, so the absolute line widths of the template don't matter |
| `extract_answer` | Takes the longest ```` ```latex ```` block and strips NotebookLM's `[1]`, `[2-4]` citation markers | NotebookLM inserts citations into code blocks, and they would show up in the PDF |
| `compile_tex` | Copies the `.tex` into the work dir, runs pdflatex there with a bare filename, and puts `resume_dir` on `TEXINPUTS`; stdin is closed | TeX fails on `~` in Windows 8.3 short paths (`AKSHAT~1.DAL`). `TEXINPUTS` still resolves local files. A closed stdin stops a missing package from hanging the run |
| `layout_rows` | `pdftotext -bbox-layout` → groups words whose baselines are within 2pt into one row → rows per page | Counts *rendered* lines, which is what really decides whether it fits on one page. Character counting can't see wraps, fonts or `\small` |
| `evaluate` | Static checks + compile + row delta vs baseline → OVERFLOW / PAGES / UNDERFLOW | Makes the length rule enforceable |
| `static_checks` | See §8 | Catches structure, fact and style problems without an LLM |
| `short_line_hints` | Estimates each changed bullet's last-line length from chars-per-line, calibrated as half the longest original bullet | Only sent on overflow, and points NotebookLM to the cheapest lines to save |
| `Evaluation.feedback` | Issues sorted block → soft → hint, one line each | A compact, unambiguous message that matches the codes defined in the prompt |
| `pick_best` | Sort key: compiled? → fewest blocking → fewest real soft issues → latest round | LLM fix rounds sometimes regress, so you always get the best attempt, not just the last one |
| `upload_jd` | Deletes every `JD - *` source, then `add_text(wait=True)`, then pins the new source ID | Avoids the message length limit, and only one JD can ever be in context |
| `fresh_conversation` | Backs up the history to JSON, then `delete_conversation` | Stops a previous job's JD or answers from leaking into this one |

**Design choices worth knowing:**
- **Bullets you didn't change are never flagged for wording.** The text checks compare against the original's visible text, so your own phrasing is never "corrected".
- **Skills lines** (one `\item` with `\\` separators) are left out of the per-bullet length and verb checks. Their wrapping shows up in the rendered line count instead.
- **UNDERFLOW is offered only once.** It is optional room to strengthen key bullets, and offering it once avoids an endless grow/shrink cycle.
- **Hints are dropped when the compile fails.** Once it doesn't compile, fixing that is the only thing that matters.
- **Brace imbalance short-circuits the bullet checks.** An unescaped `%` comments out a closing brace, which would make the bullet parser return garbage.

---

## 8. Validator issue codes

| Code | Severity | Trigger |
| --- | --- | --- |
| `COMPILE_ERROR` | block | pdflatex failed (first `!` error from the log), or unbalanced braces |
| `PAGES` | block | More pages than the original |
| `OVERFLOW` | block | More rendered lines than the original |
| `STRUCTURE` | block | More bullets than the original; extra `\resume*` / `\section` / `heading` commands or environments; added `\vspace`, `\medskip` etc., `\\`, or blank lines inside lists; no LaTeX block found |
| `IMMUTABLE_CHANGED` | block | An `immutable_strings` entry or a date (`Mon. YYYY` / `Present`) is missing or altered |
| `ESCAPE` | block | More unescaped `& % # _ $` than the original (URLs excluded) |
| `BANNED_PHRASE` | soft | Filler from `banned_phrases`; "scalable"/"high-volume" with no number; "end-to-end" more than once |
| `DASH` | soft | Em or en dash in a changed bullet (a common LLM tell) |
| `REPEATED_VERB` | soft | The same opening verb in more than 2 bullets (more than 1 for Architected/Engineered), counted only when a changed bullet is involved |
| `LONG_BULLET` | soft / hint | A changed bullet longer than your longest original bullet (soft); a project heading over `heading_max_chars` (hint, overflow only) |
| `SHORT_LAST_LINE` | hint | A bullet's last line is likely shorter than `short_last_line_chars` (overflow only) |
| `UNDERFLOW` | soft (optional) | At least `underflow_min_lines` lines of spare room, offered once |

---

## 9. What has been tested

- **Real template.** Your `main.tex` with the rebuilt original `expandskills.tex`:
  - Compiles to 1 page, 50 rendered lines.
  - `pdftotext` output matches `hiten-dalmia-resume-06-10-2026.pdf` line for line.
  - Checking the original against itself gives **no issues**.
- **The current NotebookLM `expandskills.tex`** is caught with 3 blocking issues (2 pages, +3 lines, 15 vs 14 bullets), 7 soft issues (5 filler phrases, 2 repeated verbs) and 2 wrapping project headings.
- **Mocked `run` loop.** A fake NotebookLM client converged in round 2. Bad first answers are detected and the best round is selected.
- **API compatibility.** Every notebooklm-py call (`chat.configure/ask/get_history/delete_conversation/get_settings`, `sources.list/add_text/delete`, `ChatGoal`, `ChatResponseLength`) was checked against v0.8.5 signatures.
- **Path bug.** The Windows short-path compile bug was found and fixed during this testing.

---

## 10. Known limitations

- **No plausibility check.** The validator can't tell whether a claim is believable or defensible; that's the prompt's job. **Always read `report.md` → Interview prep before sending.**
- **No keyword-coverage score.** The script doesn't measure how many JD keywords made it in; NotebookLM's report lists them, but nothing verifies that list.
- **notebooklm-py is unofficial.** It reverse-engineers NotebookLM's web API, so Google changes can break `setup`/`run`/`add-jd`. `check` plus the web UI is the fallback.
- **NotebookLM may ignore instructions** or retrieve only parts of the long prompt source. The chat config is the safety net for the core rules.
- **Line estimates are heuristic.** SHORT_LAST_LINE and the prompt's character budgets are approximations; only OVERFLOW/PAGES are measured.
- **Destructive defaults:**
  - `fresh_conversation` deletes the notebook chat (after a backup).
  - Uploading a JD deletes **every** source whose title starts with `JD - `, so don't use that prefix for anything else.
- **JD source titles** are truncated to 100 chars.
- **Single region only.** The script can't tailor parts of `main.tex` outside `expandskills.tex` (Education, Achievements, Publications).

---

## 11. Not done yet / next steps

**Required before first real use:**
- [ ] Replace `expandskills.tex` with `expandskills.original.tex`, and update the `experience-skills-section` source to match.
- [ ] Upload `resume-modifier.md` as the `resume-modifier` source.
- [ ] Install Python 3.10+, `pip install notebooklm-py`, `notebooklm login`, fill in `resume_tailor.json`, run `setup`.
- [ ] Install any missing LaTeX packages (`marvosym` was missing on the test machine), or enable MiKTeX on-the-fly installs.
- [ ] **First live run.** `setup`, `run` and `add-jd` have only been tested against a mock client, never against real NotebookLM. Watch the first run's output and `roundN-answer.md` files closely.

**Recommended:**
- [ ] **A/B test on 2-3 JDs.** Compare the hybrid setup (chat config + source, the default) with pasting all of `resume-modifier.md` into the message. Keep whichever needs fewer rounds and gives better bullets.
- [ ] **Review the content sources.** Make sure `projects-list.txt`, `skills-list.txt` and `questions-list.txt` contain everything you'd want used, including any NotebookLM-invented content to delete. The model can only reframe what's there.
- [ ] Tune `banned_phrases` after a few runs, based on the filler that slips through.

**Possible improvements (not implemented):**
- [ ] **Keyword-coverage check.** Extract terms from the JD and report which ones appear in the output and where (title, heading, bullet, skills). This would verify NotebookLM's own keyword report.
- [ ] **Grounding check.** Flag skills-section items, or heading tech-list items, that don't appear in any candidate source file (they would need local copies of the `.txt` sources).
- [ ] **Auto-sync the `experience-skills-section` source** from `expandskills.tex` in `setup`, so the two can't drift.
- [ ] **Batch mode:** run several JDs from a folder, one fresh conversation each.
- [ ] **Unit tests** for the parsers (`find_bullets`, `visible_text`, `load_template`) and an offline regression test using the original and NotebookLM versions as fixtures.
- [ ] **ATS text-extraction check:** confirm `pdftotext` output of the final PDF reads cleanly (bullets, ligatures, `&`), as an approximation of what an ATS parser sees.
- [ ] Optionally tailor a one-line summary or the Achievements ordering. This is out of scope today.
