# resume-modifier: usage

Quick how-to. For design rationale, config reference, test results and open tasks, see `resume-tailor-README.md`.

## Files
| File | Where it goes |
| --- | --- |
| `resume-modifier.md` | Upload to NotebookLM as a source titled **resume-modifier** (full rules and examples) |
| `resume-modifier-chat.md` | NotebookLM **Configure chat → Custom** (about 7.5k of the 10k-char limit). `setup` does this for you |
| `resume_tailor.py` | Driver and validator (needs notebooklm-py for `setup`/`run`; `check` works offline) |
| `resume_tailor.example.json` | Copy to `resume_tailor.json` and fill it in |

Your other sources stay as they are: `experience-skills-section`, `projects-list.txt`, `skills-list.txt`, `questions-list.txt`.

## One-time setup
1. `pip install notebooklm-py`, then `notebooklm login`.
2. Point the script at the tailored region. Your setup already works: `main.tex` does `\input{expandskills.tex}`, so set `"section_file": "expandskills.tex"` and the script inlines the candidate section in place of that line. No markers are needed.
   - **`expandskills.tex` must hold your ORIGINAL content** (Full-Stack Developer / Order Procurement / Software Developer / Cross-platform AR App / Kia app). It is the baseline for length and the "unchanged bullet" checks.
   - The `experience-skills-section` source in NotebookLM must be **identical** to that file.
   - *Alternative:* without `section_file`, wrap the region inside `main.tex` in `%% TAILOR-START` / `%% TAILOR-END` marker lines.
3. Copy `resume_tailor.example.json` to `resume_tailor.json` and set:
   - `notebook_id` (from the notebook URL)
   - `resume_dir` (the folder containing `main.tex` and `expandskills.tex`); `main_tex` and `section_file` are already filled in
   - `immutable_strings`: already set to your companies, location and dates, exactly as written in the .tex
   - `bullet_macros`: already `resumeItem` and `item`
4. `python resume_tailor.py setup` configures the chat, checks that the source titles resolve, and writes the current region to `tailor-runs/experience-skills-section.txt` so you can compare it with the source.
5. Compiling is done locally with MiKTeX `pdflatex`. If a package is missing (for example `marvosym`), run `miktex packages install marvosym` once, or enable on-the-fly installs in MiKTeX Console.

## Per application
Save the JD to a text file, then run:
```
python resume_tailor.py run --company "Acme" --role "Backend Engineer" --jd jd.txt
```
- It asks NotebookLM, strips `[n]` citations, checks the LaTeX, compiles it into your template, compares the rendered line count with the original, and sends exact fixes back. This repeats up to `max_rounds` (default 4).
- **The JD goes in as a source**, not in the message (`jd_as_source: true`), so long JDs never hit the message limit. It is uploaded as `JD - <company> - <role>`. Any older `JD - ` source is deleted first, so the notebook only ever holds one JD and earlier jobs can't leak in. The latest JD stays in the notebook, so you can keep chatting about it in the web UI afterwards.
- The output goes to `tailor-runs/<timestamp>-<company>-<role>/final/` and contains `resume.pdf`, `resume.tex`, `section.tex`, `report.md` (NotebookLM's change report) and `issues.txt`.
- It always picks the best round, even if no round came back fully clean.
- **`fresh_conversation: true` deletes the notebook's current chat** (after backing it up to `previous-chat-backup.json`), so earlier jobs don't leak into this one. Set it to `false` to keep the chat.

## Web UI only (no automation)
1. Add the JD as a source:
   - **With the script:** `python resume_tailor.py add-jd --company "Acme" --role "Backend Engineer" --jd jd.txt`. It replaces the old JD source and prints the exact message to send.
   - **By hand:** Add source → *Copied text*, paste the JD, and title it `JD - Acme - Backend Engineer`. Delete (or untick) any older `JD - ` source.
2. Start a **new chat** and send:
   ```
   Tailor my resume by following ALL instructions in the source "resume-modifier" and the chat instructions.
   Treat "resume-modifier" as instructions only. The job description is the source "JD - Acme - Backend Engineer": read all of it and ignore any other "JD - " sources. The JD describes the employer, not me. All remaining sources are my candidate data.

   Company: Acme
   Target role title: Backend Engineer
   ```
3. Save NotebookLM's whole answer to `answer.txt`.
4. Run `python resume_tailor.py check answer.txt` and paste the printed `VALIDATOR FEEDBACK` block back into the chat. Repeat until it reports no issues.

**Why keep only one JD source:** NotebookLM retrieves from every ticked source. A leftover JD can mix another company's requirements into the tailoring, and the bigger risk is that the model starts treating JD text as evidence about you. The prompt forbids both, but removing old JDs takes the temptation away.

## What the validator checks
| Severity | Codes |
| --- | --- |
| Blocking | COMPILE_ERROR, OVERFLOW / PAGES (measured from the compiled PDF), STRUCTURE (extra bullets, sections or spacing commands), IMMUTABLE_CHANGED, ESCAPE (unescaped `% & # _ $`) |
| Soft | BANNED_PHRASE (filler words; editable via `banned_phrases` in the config), DASH, REPEATED_VERB, LONG_BULLET (longer than your longest original bullet), UNDERFLOW (offered once) |
| Hint | SHORT_LAST_LINE, long project headings (only sent when there is an overflow) |

Bullets copied unchanged from your original are never flagged for wording.

## What it cannot check
Whether a claim is plausible, or interview-defensible. That is the prompt's job (truth tiers, interview test). Read `report.md`, especially its **Interview prep** list of extensions and estimated numbers, before you send anything.

## Worth testing once
An A/B comparison on 2-3 JDs:
- **Hybrid:** custom chat plus the source (the default).
- **Full prompt:** paste all of `resume-modifier.md` into the message, above the trigger.

Keep whichever produces fewer validator rounds and better bullets.
