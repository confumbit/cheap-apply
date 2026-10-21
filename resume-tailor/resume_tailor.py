#!/usr/bin/env python3
"""
resume_tailor.py: drive NotebookLM (via notebooklm-py) to tailor a LaTeX resume
to a job description, then compile, validate, and feed precise fixes back in a loop.

Commands
  setup   Push the condensed instructions into NotebookLM's chat config, and verify sources.
  run     Tailor for one job: ask -> extract LaTeX -> check -> compile -> measure -> feedback loop.
  check   Offline: validate a saved NotebookLM answer (or raw LaTeX) and print the feedback
          message to paste back into the NotebookLM web UI. No NotebookLM access needed.
  add-jd  Web-UI helper: upload a JD as a "JD - <company> - <role>" source (replacing the
          previous one) and print the message to send in a new chat.

Requirements
  - Python 3.10+, `pip install notebooklm-py` (only needed for setup/run), then `notebooklm login`
  - A LaTeX engine (pdflatex by default) and poppler's `pdftotext` on PATH (MiKTeX ships both)
  - The tailored region is either a separate file pulled in by \\input (set "section_file",
    e.g. "expandskills.tex"; the script inlines it in place of that line), or a block in
    main.tex wrapped in two marker lines:
        %% TAILOR-START
        ... experience / projects / skills LaTeX ...
        %% TAILOR-END
    That region must be identical to the `experience-skills-section` source in NotebookLM.
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import math
import os
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

START_MARK = "%% TAILOR-START"
END_MARK = "%% TAILOR-END"
CUSTOM_PROMPT_LIMIT = 10_000

DEFAULT_CONFIG: dict = {
    "notebook_id": "",
    "profile": None,
    "source_titles": [
        "resume-modifier",
        "experience-skills-section",
        "projects-list.txt",
        "skills-list.txt",
        "questions-list.txt",
    ],
    "custom_prompt_file": "resume-modifier-chat.md",
    "resume_dir": ".",
    "main_tex": "main.tex",
    "section_file": "",
    "output_dir": "tailor-runs",
    "latex_engine": "pdflatex",
    "pdftotext": "pdftotext",
    "immutable_strings": [],
    "bullet_macros": ["item", "resumeItem"],
    "chars_per_line": 100,
    "max_bullet_chars": 205,
    "heading_max_chars": 95,
    "short_last_line_chars": 25,
    "underflow_min_lines": 3,
    "max_rounds": 4,
    "fresh_conversation": True,
    "jd_as_source": True,
    "jd_source_prefix": "JD - ",
    "banned_phrases": [
        r"\brobust\b", r"\bseamless(ly)?\b", r"\bcutting[- ]edge\b", r"\bstate[- ]of[- ]the[- ]art\b",
        r"\bleverag(e|ed|es|ing)\b", r"\butili[sz](e|ed|es|ing)\b", r"\bspearhead(ed|ing)?\b",
        r"\bsynerg(y|ies|istic)\b", r"\bdynamic\b", r"\binnovative\b", r"\bcomprehensive\b",
        r"\bworld[- ]class\b", r"\bintelligent\b", r"with high precision", r"ensuring high",
        r"best practices", r"\bgovernance\b", r"\bvarious\b", r"a wide range of",
    ],
}

TRIGGER_TEMPLATE = """Tailor my resume by following ALL instructions in the source "resume-modifier" and the chat instructions.
Treat "resume-modifier" as instructions only. All other sources are my candidate data.

Company: {company}
Target role title: {role}
Job description:
{jd}"""

TRIGGER_JD_SOURCE_TEMPLATE = """Tailor my resume by following ALL instructions in the source "resume-modifier" and the chat instructions.
Treat "resume-modifier" as instructions only. The job description is the source "{jd_title}": read all of it and ignore any other "JD - " sources. The JD describes the employer, not me. All remaining sources are my candidate data.

Company: {company}
Target role title: {role}"""

SPACING_COMMANDS = ["vspace", "vskip", "smallskip", "medskip", "bigskip", "newline",
                    "par", "linebreak", "pagebreak", "newpage", "clearpage"]
DATE_RE = re.compile(
    r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?\s+\d{4}\b|\bPresent\b")
CITATION_RE = re.compile(r"[ \t]?\[\d+(?:\s*[-\u2013,]\s*\d+)*\]")
FENCE_RE = re.compile(r"```[ \t]*(?:latex|tex)?[ \t]*\r?\n(.*?)```", re.DOTALL | re.IGNORECASE)


# --------------------------------------------------------------------------- config / IO

def load_config(path: Path) -> dict:
    cfg = dict(DEFAULT_CONFIG)
    if path.exists():
        cfg.update(json.loads(path.read_text(encoding="utf-8-sig")))
    else:
        sys.exit(f"Config not found: {path}. Copy resume_tailor.example.json to {path.name} and edit it.")
    base = path.resolve().parent
    for key in ("resume_dir", "output_dir", "custom_prompt_file"):
        p = Path(cfg[key])
        cfg[key] = (p if p.is_absolute() else (base / p)).resolve()
    cfg["main_tex"] = cfg["resume_dir"] / cfg["main_tex"]
    if cfg.get("section_file"):
        cfg["section_file"] = cfg["resume_dir"] / cfg["section_file"]
    return cfg


def load_template(cfg: dict) -> tuple[str, str, str]:
    """(prefix, tailored section, suffix). With section_file, the section replaces its \\input line."""
    main = cfg["main_tex"].read_text(encoding="utf-8-sig")
    sec_file = cfg.get("section_file")
    if not sec_file:
        return split_template(main)
    if not sec_file.exists():
        sys.exit(f"section_file not found: {sec_file}")
    stem = re.escape(sec_file.stem)
    pat = re.compile(r"^[^%\n]*?\\(?:input|include)\{(?:\./)?" + stem + r"(?:\.tex)?\}[^\n]*\n?", re.M)
    m = pat.search(main)
    if not m:
        sys.exit(f"{cfg['main_tex'].name} has no uncommented \\input{{{sec_file.name}}} line.")
    section = sec_file.read_text(encoding="utf-8-sig")
    if not section.endswith("\n"):
        section += "\n"
    return main[:m.start()], section, main[m.end():]


def split_template(text: str) -> tuple[str, str, str]:
    s, e = text.find(START_MARK), text.find(END_MARK)
    if s < 0 or e < 0 or e < s:
        sys.exit(f"main.tex must contain '{START_MARK}' and '{END_MARK}' marker lines (in that order).")
    after_start = text.index("\n", s) + 1
    end_line_start = text.rfind("\n", 0, e) + 1
    return text[:after_start], text[after_start:end_line_start], text[end_line_start:]


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:40] or "x"


# --------------------------------------------------------------------------- LaTeX text helpers

def strip_comments(tex: str) -> str:
    return "\n".join(re.sub(r"(?<!\\)%.*$", "", ln) for ln in tex.splitlines())


def drop_urls(tex: str) -> str:
    tex = re.sub(r"\\href\{[^{}]*\}", r"\\href", tex)
    return re.sub(r"\\url\{[^{}]*\}", "", tex)


_ESC = {"&": "\ue000", "%": "\ue001", "$": "\ue002", "#": "\ue003", "_": "\ue004", "{": "\ue005", "}": "\ue006"}


def visible_text(tex: str) -> str:
    t = drop_urls(strip_comments(tex))
    t = re.sub(r"\\([&%$#_{}])", lambda m: _ESC[m.group(1)], t)
    t = t.replace("---", "\u2014").replace("--", "\u2013").replace("~", " ")
    t = re.sub(r"\\\\(\[[^\]]*\])?", " ", t)
    t = re.sub(r"\\[a-zA-Z@]+\*?(\[[^\]]*\])?", "", t)
    t = re.sub(r"[{}$]", "", t)
    for ch, ph in _ESC.items():
        t = t.replace(ph, ch)
    return re.sub(r"\s+", " ", t).strip()


def read_braced(tex: str, i: int) -> tuple[str, int]:
    """Read a balanced {...} group starting at/after index i. Returns (content, end_index)."""
    while i < len(tex) and tex[i].isspace():
        i += 1
    if i >= len(tex) or tex[i] != "{":
        return "", i
    depth, j = 0, i
    while j < len(tex):
        c = tex[j]
        if c == "\\":
            j += 2
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return tex[i + 1:j], j + 1
        j += 1
    return tex[i + 1:], len(tex)


def find_bullets(tex: str, macros: list[str]) -> list[str]:
    body = strip_comments(tex)
    pat = re.compile(r"\\(" + "|".join(map(re.escape, macros)) + r")(?![A-Za-z@])")
    boundary = re.compile(r"\\item(?![A-Za-z])|\\end\{|\\begin\{|\n[ \t]*\\resume|\n[ \t]*\\(?:sub)*section")
    out = []
    for m in pat.finditer(body):
        if m.group(1) == "item":
            start = m.end()
            opt = re.match(r"\s*\[[^\]]*\]", body[start:])
            if opt:
                start += opt.end()
            b = boundary.search(body, start)
            out.append(body[start:b.start() if b else len(body)])
        else:
            content, _ = read_braced(body, m.end())
            out.append(content)
    return out


def is_heading(raw: str, vis: str) -> bool:
    return " | " in vis or "$|$" in raw or raw.lstrip().startswith("\\textbf")


def snippet(vis: str, n: int = 6) -> str:
    return '"' + " ".join(vis.split()[:n]) + '..."'


def command_counts(tex: str) -> Counter:
    return Counter(re.findall(r"\\([a-zA-Z@]+)", strip_comments(tex)))


def env_counts(tex: str) -> Counter:
    return Counter(re.findall(r"\\begin\{([^}]+)\}", strip_comments(tex)))


def blank_lines_in_lists(tex: str) -> int:
    depth, blanks = 0, 0
    for ln in strip_comments(tex).splitlines():
        if not ln.strip():
            blanks += depth > 0
            continue
        depth += len(re.findall(r"\\begin\{(?:itemize|enumerate|description)\}|\\[A-Za-z]*ListStart\b", ln))
        depth -= len(re.findall(r"\\end\{(?:itemize|enumerate|description)\}|\\[A-Za-z]*ListEnd\b", ln))
        depth = max(depth, 0)
    return blanks


def unescaped_counts(tex: str) -> Counter:
    t = drop_urls(tex)
    c = Counter()
    for ln in t.splitlines():
        code = ln.lstrip()
        if code.startswith("%"):
            continue
        for ch in "&$#_":
            c[ch] += len(re.findall(r"(?<!\\)" + re.escape(ch), ln))
        c["%mid"] += len(re.findall(r"(?<!\\)%", ln))
    return c


def brace_balance_ok(tex: str) -> bool:
    depth = 0
    for ch in re.sub(r"\\[{}\\]", "", strip_comments(tex)):
        depth += ch == "{"
        depth -= ch == "}"
        if depth < 0:
            return False
    return depth == 0


# --------------------------------------------------------------------------- answer parsing

def extract_answer(answer: str) -> tuple[str | None, str, int]:
    blocks = FENCE_RE.findall(answer)
    if not blocks:
        return None, answer.strip(), 0
    latex = max(blocks, key=len)
    cleaned, n = CITATION_RE.subn("", latex)
    report = FENCE_RE.sub("", answer, count=len(blocks)).strip()
    return cleaned.strip("\n") + "\n", report, n


# --------------------------------------------------------------------------- compile / layout

def compile_tex(cfg: dict, tex_path: Path, outdir: Path) -> tuple[Path | None, str]:
    # TeX chokes on '~' (e.g. Windows 8.3 short paths), so run from the output dir with a bare
    # filename and expose the resume dir (local .tex/.cls/images) through TEXINPUTS.
    outdir = outdir.resolve()
    outdir.mkdir(parents=True, exist_ok=True)
    tex_path = tex_path.resolve()
    if tex_path.parent != outdir:
        shutil.copy(tex_path, outdir / tex_path.name)
        tex_path = outdir / tex_path.name
    env = dict(os.environ)
    env["TEXINPUTS"] = f"{Path(cfg['resume_dir']).resolve()}{os.pathsep}{env.get('TEXINPUTS', '')}"
    cmd = [cfg["latex_engine"], "-interaction=nonstopmode", "-halt-on-error", tex_path.name]
    try:
        subprocess.run(cmd, cwd=outdir, env=env, capture_output=True, timeout=180,
                       stdin=subprocess.DEVNULL)
    except FileNotFoundError:
        sys.exit(f"LaTeX engine not found: {cfg['latex_engine']}")
    except subprocess.TimeoutExpired:
        return None, "LaTeX compile timed out (is MiKTeX waiting to install a package?)"
    pdf = outdir / (tex_path.stem + ".pdf")
    log = outdir / (tex_path.stem + ".log")
    if not log.exists():
        log = outdir / "texput.log"
    if pdf.exists() and pdf.stat().st_mtime >= tex_path.stat().st_mtime:
        return pdf, ""
    err = "unknown error"
    if log.exists():
        lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
        for i, ln in enumerate(lines):
            if ln.startswith("!"):
                err = " ".join(x.strip() for x in lines[i:i + 3])
                break
    return None, err


def layout_rows(cfg: dict, pdf: Path) -> list[list[str]]:
    """Rendered text rows per page (words sharing a baseline form one row)."""
    out = pdf.with_suffix(".bbox.html")
    try:
        subprocess.run([cfg["pdftotext"], "-bbox-layout", str(pdf), str(out)],
                       capture_output=True, check=True, timeout=60)
    except FileNotFoundError:
        sys.exit(f"pdftotext not found: {cfg['pdftotext']} (install poppler or set 'pdftotext' in config)")
    raw = out.read_text(encoding="utf-8", errors="replace")
    raw = re.sub(r"<!DOCTYPE[^>]*>", "", raw)
    raw = re.sub(r'\sxmlns="[^"]*"', "", raw, count=1)
    root = ET.fromstring(raw)
    pages = []
    for page in root.iter("page"):
        lines = []
        for line in page.iter("line"):
            words = [w.text or "" for w in line.iter("word")]
            lines.append((float(line.get("yMax")), float(line.get("xMin")), " ".join(words)))
        lines.sort()
        rows: list[list] = []
        for y, x, text in lines:
            if rows and abs(rows[-1][0] - y) <= 2.0:
                rows[-1][1].append((x, text))
            else:
                rows.append([y, [(x, text)]])
        pages.append([" ".join(t for _, t in sorted(parts)) for _, parts in rows])
    return pages


# --------------------------------------------------------------------------- evaluation

@dataclass
class Issue:
    code: str
    msg: str
    severity: str  # block | soft | hint | info


@dataclass
class Evaluation:
    round_no: int
    latex: str | None
    report: str
    issues: list[Issue] = field(default_factory=list)
    pdf: Path | None = None
    tex: Path | None = None
    line_delta: int | None = None

    def count(self, sev: str) -> int:
        return sum(i.severity == sev for i in self.issues)

    @property
    def needs_feedback(self) -> bool:
        return any(i.severity in ("block", "soft") for i in self.issues)

    def feedback(self) -> str:
        head = (f"VALIDATOR FEEDBACK (round {self.round_no}). Fix every issue below, change nothing else, "
                "and return the full LaTeX block and report again.")
        order = {"block": 0, "soft": 1, "hint": 2}
        lines = [f"- {i.code}: {i.msg}" for i in sorted(self.issues, key=lambda i: order.get(i.severity, 3))
                 if i.severity in order]
        return "\n".join([head, *lines])


@dataclass
class Baseline:
    prefix: str
    section: str
    suffix: str
    pages: int
    rows: int


def measure_baseline(cfg: dict, work: Path) -> Baseline:
    prefix, section, suffix = load_template(cfg)
    tex = work / "original.tex"
    tex.write_text(prefix + section + suffix, encoding="utf-8")
    pdf, err = compile_tex(cfg, tex, work)
    if not pdf:
        sys.exit(f"Original resume failed to compile: {err}")
    pages = layout_rows(cfg, pdf)
    return Baseline(prefix, section, suffix, len(pages), sum(len(p) for p in pages))


def static_checks(cfg: dict, orig: str, new: str) -> list[Issue]:
    issues: list[Issue] = []
    macros = cfg["bullet_macros"]

    balanced = brace_balance_ok(new)
    if not balanced:
        issues.append(Issue("COMPILE_ERROR", "Unbalanced braces { } in the LaTeX (an unescaped % comments out the rest of its line, including any closing brace).", "block"))

    ob, nb = find_bullets(orig, macros), find_bullets(new, macros) if balanced else []
    if len(nb) > len(ob):
        issues.append(Issue("STRUCTURE", f"{len(nb)} bullets but the original has {len(ob)}. Remove {len(nb) - len(ob)}.", "block"))

    oc, nc = command_counts(orig), command_counts(new)
    for name in sorted(set(nc) | set(oc)):
        low = name.lower()
        if name in macros or not any(k in low for k in ("resume", "section", "heading")):
            continue
        if nc[name] > oc[name]:
            issues.append(Issue("STRUCTURE", f"\\{name} used {nc[name]} times, original {oc[name]}. Do not add headings/sections.", "block"))
    oe, ne = env_counts(orig), env_counts(new)
    for env in ne:
        if ne[env] > oe[env]:
            issues.append(Issue("STRUCTURE", f"\\begin{{{env}}} used {ne[env]} times, original {oe[env]}.", "block"))
    for cmd in SPACING_COMMANDS:
        if nc[cmd] > oc[cmd]:
            issues.append(Issue("STRUCTURE", f"Remove added \\{cmd} (no vertical spacing commands).", "block"))
    if new.count("\\\\") > orig.count("\\\\"):
        issues.append(Issue("STRUCTURE", "Remove added \\\\ line breaks.", "block"))
    if blank_lines_in_lists(new) > blank_lines_in_lists(orig):
        issues.append(Issue("STRUCTURE", "Remove blank lines inside list environments (they add vertical space).", "block"))

    norm = lambda s: re.sub(r"\s+", " ", s)
    for s in cfg["immutable_strings"]:
        if norm(s) not in norm(new):
            issues.append(Issue("IMMUTABLE_CHANGED", f'"{s}" is missing or altered. Restore it exactly.', "block"))
    od, nd = Counter(DATE_RE.findall(orig)), Counter(DATE_RE.findall(new))
    for d, k in od.items():
        if nd[d] < k:
            issues.append(Issue("IMMUTABLE_CHANGED", f'Date "{d}" is missing or altered. Restore it exactly.', "block"))

    ou, nu = unescaped_counts(orig), unescaped_counts(new)
    for ch, label in (("%mid", "%"), ("&", "&"), ("#", "#"), ("_", "_"), ("$", "$")):
        if nu[ch] > ou[ch]:
            issues.append(Issue("ESCAPE", f'Unescaped "{label}" added ({nu[ch] - ou[ch]}x). Write \\{label}.', "block"))

    # Text-quality checks only apply to bullets the model wrote or changed; the candidate's
    # own unchanged wording is never flagged. Multi-line blocks (skills, joined with \\) are
    # covered by the layout check instead of the per-bullet length/verb checks.
    squash = lambda s: re.sub(r"\s+", " ", s).strip()
    orig_vis = {squash(visible_text(b)) for b in ob}
    max_chars = max([cfg["max_bullet_chars"]] + [len(squash(visible_text(b))) for b in ob
                    if "\\\\" not in b and not is_heading(b, visible_text(b))])
    verbs: Counter = Counter()
    changed_verbs: set[str] = set()
    banned = [re.compile(p, re.IGNORECASE) for p in cfg["banned_phrases"]]
    e2e = 0
    for raw in nb:
        vis = visible_text(raw)
        if not vis:
            continue
        sn = snippet(vis)
        unchanged = squash(vis) in orig_vis
        if is_heading(raw, vis):
            if len(vis) > cfg["heading_max_chars"] and not unchanged:
                issues.append(Issue("LONG_BULLET", f"Project heading {sn} is ~{len(vis)} chars; keep it <= {cfg['heading_max_chars']} so it stays on one line.", "hint"))
            continue
        multiline = "\\\\" in raw
        if not multiline:
            w = re.sub(r"^[^A-Za-z]+", "", vis).split(" ")[0]
            verbs[w] += 1
            if not unchanged:
                changed_verbs.add(w)
        if unchanged:
            continue
        for rx in banned:
            m = rx.search(vis)
            if m:
                issues.append(Issue("BANNED_PHRASE", f'"{m.group(0)}" in bullet {sn}', "soft"))
        if "\u2014" in vis or "\u2013" in vis:
            issues.append(Issue("DASH", f"Em/en dash in bullet {sn}. Rephrase without it.", "soft"))
        if re.search(r"\bscalable\b|\bhigh-volume\b", vis, re.I) and not re.search(r"\d", vis):
            issues.append(Issue("BANNED_PHRASE", f'"scalable"/"high-volume" without a number in bullet {sn}', "soft"))
        e2e += len(re.findall(r"end-to-end", vis, re.I))
        if not multiline and len(squash(vis)) > max_chars:
            issues.append(Issue("LONG_BULLET", f"Bullet {sn} is ~{len(squash(vis))} visible chars (max {max_chars}, i.e. 2 lines). Shorten it.", "soft"))
    if e2e > 1:
        issues.append(Issue("BANNED_PHRASE", f'"end-to-end" used {e2e} times (max 1).', "soft"))
    for v, k in verbs.items():
        limit = 1 if v.lower() in ("architected", "engineered") else 2
        if k > limit and v in changed_verbs:
            issues.append(Issue("REPEATED_VERB", f'"{v}" opens {k} bullets (max {limit}). Vary the verbs.', "soft"))
    return issues


def short_line_hints(cfg: dict, orig: str, new: str) -> list[Issue]:
    hints = []
    macros = cfg["bullet_macros"]
    squash = lambda s: re.sub(r"\s+", " ", s).strip()
    orig_plain = [squash(visible_text(b)) for b in find_bullets(orig, macros)
                  if "\\\\" not in b and not is_heading(b, visible_text(b))]
    # The longest original bullet fits in 2 lines, so a line holds at least half of it.
    cpl = max([cfg["chars_per_line"]] + [math.ceil(len(v) / 2) for v in orig_plain])
    thr = cfg["short_last_line_chars"]
    for raw in find_bullets(new, macros):
        vis = squash(visible_text(raw))
        if not vis or is_heading(raw, vis) or "\\\\" in raw or vis in orig_plain:
            continue
        lines = math.ceil(len(vis) / cpl)
        rem = len(vis) - (lines - 1) * cpl
        if lines > 1 and rem < thr:
            hints.append(Issue("SHORT_LAST_LINE", f"Bullet {snippet(vis)} likely ends with a ~{rem}-char last line; cut >= {rem + 3} chars to save a line.", "hint"))
    return hints


def evaluate(cfg: dict, base: Baseline, answer: str, round_no: int, work: Path, allow_underflow: bool) -> Evaluation:
    latex, report, n_cites = extract_answer(answer)
    ev = Evaluation(round_no, latex, report)
    if latex is None:
        ev.issues.append(Issue("STRUCTURE", "No LaTeX code block found. Return the full LaTeX in ONE ```latex code block.", "block"))
        return ev
    if n_cites:
        ev.issues.append(Issue("INFO", f"Stripped {n_cites} citation markers from the LaTeX.", "info"))
    ev.issues += static_checks(cfg, base.section, latex)

    tex = work / f"round{round_no}.tex"
    tex.write_text(base.prefix + latex + base.suffix, encoding="utf-8")
    ev.tex = tex
    pdf, err = compile_tex(cfg, tex, work)
    if not pdf:
        ev.issues = [i for i in ev.issues if i.severity != "hint"]
        ev.issues.append(Issue("COMPILE_ERROR", err, "block"))
        return ev
    ev.pdf = pdf
    pages = layout_rows(cfg, pdf)
    rows = sum(len(p) for p in pages)
    ev.line_delta = rows - base.rows
    overflow = ev.line_delta
    if len(pages) > base.pages:
        overflow = max(overflow, sum(len(p) for p in pages[base.pages:]))
        ev.issues.append(Issue("PAGES", f"Output runs to {len(pages)} pages; must fit in {base.pages}.", "block"))
    if overflow > 0:
        ev.issues.append(Issue("OVERFLOW", f"{overflow} rendered line(s) longer than the original. Cut at least {overflow} line(s): shorten the bullets you lengthened, starting with any flagged below.", "block"))
        ev.issues += short_line_hints(cfg, base.section, latex)
    else:
        ev.issues = [i for i in ev.issues if not (i.code == "LONG_BULLET" and i.severity == "hint")]
        if allow_underflow and cfg["underflow_min_lines"] and -ev.line_delta >= cfg["underflow_min_lines"] \
                and not any(i.severity in ("block", "soft") for i in ev.issues):
            ev.issues.append(Issue("UNDERFLOW", f"{-ev.line_delta} lines (optional: use them to strengthen the most JD-relevant bullets).", "soft"))
    return ev


def pick_best(attempts: list[Evaluation]) -> Evaluation:
    def key(e: Evaluation):
        real_soft = sum(i.severity == "soft" and i.code != "UNDERFLOW" for i in e.issues)
        return (e.pdf is None, e.count("block"), real_soft, -e.round_no)
    return min(attempts, key=key)


def print_eval(ev: Evaluation) -> None:
    delta = "n/a" if ev.line_delta is None else f"{ev.line_delta:+d} lines vs original"
    print(f"  round {ev.round_no}: {ev.count('block')} blocking, {ev.count('soft')} soft, layout {delta}")
    for i in ev.issues:
        if i.severity != "hint":
            print(f"    [{i.severity}] {i.code}: {i.msg}")


def finalize(best: Evaluation, run_dir: Path) -> None:
    final = run_dir / "final"
    final.mkdir(exist_ok=True)
    if best.tex:
        shutil.copy(best.tex, final / "resume.tex")
    if best.pdf:
        shutil.copy(best.pdf, final / "resume.pdf")
    if best.latex:
        (final / "section.tex").write_text(best.latex, encoding="utf-8")
    (final / "report.md").write_text(best.report, encoding="utf-8")
    status = "clean" if not best.needs_feedback else "has remaining issues (see issues.txt)"
    (final / "issues.txt").write_text("\n".join(f"[{i.severity}] {i.code}: {i.msg}" for i in best.issues), encoding="utf-8")
    print(f"\nBest attempt: round {best.round_no} ({status})\nOutput: {final}")


# --------------------------------------------------------------------------- commands

async def resolve_source_ids(client, nb_id: str, titles: list[str] | None) -> list[str] | None:
    if not titles:
        return None
    sources = await client.sources.list(nb_id)
    by_title = {}
    for s in sources:
        t = (s.title or "").strip().lower()
        by_title[t] = s.id
        by_title[re.sub(r"\.(md|txt|tex|pdf)$", "", t)] = s.id
    ids, missing = [], []
    for t in titles:
        key = t.strip().lower()
        sid = by_title.get(key) or by_title.get(re.sub(r"\.(md|txt|tex|pdf)$", "", key))
        (ids.append(sid) if sid else missing.append(t))
    if missing:
        print("WARNING: sources not found by title:", ", ".join(missing))
        print("         available:", ", ".join(s.title or s.id for s in sources))
    return ids or None


def jd_title(cfg: dict, company: str, role: str) -> str:
    title = re.sub(r"\s+", " ", f"{cfg['jd_source_prefix']}{company} - {role}").strip()
    return title[:100]


async def upload_jd(client, nb_id: str, cfg: dict, company: str, role: str, jd: str) -> tuple[str, str]:
    """Replace any previous JD source with this one, so the notebook holds exactly one JD."""
    prefix = cfg["jd_source_prefix"].strip().lower()
    for s in await client.sources.list(nb_id):
        if (s.title or "").strip().lower().startswith(prefix):
            await client.sources.delete(nb_id, s.id)
            print(f"Removed old JD source: {s.title}")
    title = jd_title(cfg, company, role)
    src = await client.sources.add_text(nb_id, title, f"{company}\n{role}\n\n{jd.strip()}", wait=True)
    print(f"Uploaded JD source: {title}")
    return title, src.id


async def cmd_add_jd(cfg: dict, company: str, role: str, jd: str) -> None:
    from notebooklm import NotebookLMClient
    async with NotebookLMClient.from_storage(profile=cfg["profile"]) as client:
        title, _ = await upload_jd(client, cfg["notebook_id"], cfg, company, role, jd)
    print("\nStart a NEW chat in NotebookLM and send:\n")
    print(TRIGGER_JD_SOURCE_TEMPLATE.format(jd_title=title, company=company, role=role))


async def cmd_setup(cfg: dict) -> None:
    from notebooklm import ChatGoal, ChatResponseLength, NotebookLMClient
    prompt = cfg["custom_prompt_file"].read_text(encoding="utf-8-sig").strip()
    if len(prompt) > CUSTOM_PROMPT_LIMIT:
        sys.exit(f"custom prompt is {len(prompt)} chars; limit is {CUSTOM_PROMPT_LIMIT}.")
    async with NotebookLMClient.from_storage(profile=cfg["profile"]) as client:
        nb = cfg["notebook_id"]
        await client.chat.configure(nb, goal=ChatGoal.CUSTOM, response_length=ChatResponseLength.LONGER,
                                    custom_prompt=prompt)
        settings = await client.chat.get_settings(nb)
        print(f"Chat configured ({len(prompt)} chars): {settings}")
        await resolve_source_ids(client, nb, cfg["source_titles"])
    _, section, _ = load_template(cfg)
    cfg["output_dir"].mkdir(parents=True, exist_ok=True)
    out = cfg["output_dir"] / "experience-skills-section.txt"
    out.write_text(section, encoding="utf-8")
    print(f"Wrote current tailored region to {out}. Make sure the NotebookLM source "
          f"'experience-skills-section' matches it.")


async def cmd_run(cfg: dict, company: str, role: str, jd: str) -> None:
    from notebooklm import NotebookLMClient
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = cfg["output_dir"] / f"{stamp}-{slug(company)}-{slug(role)}"
    work = run_dir / "work"
    work.mkdir(parents=True)
    (run_dir / "jd.txt").write_text(f"{company}\n{role}\n\n{jd}", encoding="utf-8")
    base = measure_baseline(cfg, work)
    print(f"Baseline: {base.pages} page(s), {base.rows} rendered lines")

    attempts: list[Evaluation] = []
    async with NotebookLMClient.from_storage(profile=cfg["profile"]) as client:
        nb = cfg["notebook_id"]
        source_ids = await resolve_source_ids(client, nb, cfg["source_titles"])
        conv_id = None
        if cfg["fresh_conversation"]:
            last = await client.chat.get_conversation_id(nb)
            if last:
                hist = await client.chat.get_history(nb, conversation_id=last)
                (run_dir / "previous-chat-backup.json").write_text(json.dumps(hist, default=str, indent=1), encoding="utf-8")
                await client.chat.delete_conversation(nb, last)
                print("Started a fresh conversation (previous chat backed up to previous-chat-backup.json)")
        message = TRIGGER_TEMPLATE.format(company=company, role=role, jd=jd.strip())
        if cfg["jd_as_source"]:
            title, jd_id = await upload_jd(client, nb, cfg, company, role, jd)
            if source_ids is not None:
                source_ids = source_ids + [jd_id]
            message = TRIGGER_JD_SOURCE_TEMPLATE.format(jd_title=title, company=company, role=role)
        underflow_used = False
        for r in range(1, cfg["max_rounds"] + 1):
            print(f"Asking NotebookLM (round {r})...")
            result = await client.chat.ask(nb, message, source_ids=source_ids, conversation_id=conv_id)
            conv_id = result.conversation_id
            (run_dir / f"round{r}-answer.md").write_text(result.answer, encoding="utf-8")
            ev = evaluate(cfg, base, result.answer, r, work, allow_underflow=not underflow_used)
            attempts.append(ev)
            print_eval(ev)
            if not ev.needs_feedback or r == cfg["max_rounds"]:
                break
            underflow_used = underflow_used or any(i.code == "UNDERFLOW" for i in ev.issues)
            message = ev.feedback()
            (run_dir / f"round{r}-feedback.txt").write_text(message, encoding="utf-8")
    finalize(pick_best(attempts), run_dir)


def cmd_check(cfg: dict, answer_file: Path) -> None:
    text = answer_file.read_text(encoding="utf-8-sig")
    if "```" not in text:
        text = f"```latex\n{text}\n```"
    run_dir = cfg["output_dir"] / f"check-{dt.datetime.now().strftime('%Y%m%d-%H%M%S')}"
    work = run_dir / "work"
    work.mkdir(parents=True)
    base = measure_baseline(cfg, work)
    print(f"Baseline: {base.pages} page(s), {base.rows} rendered lines")
    ev = evaluate(cfg, base, text, 1, work, allow_underflow=True)
    print_eval(ev)
    if ev.needs_feedback:
        print("\n----- paste this back into NotebookLM -----\n" + ev.feedback())
    else:
        print("\nNo issues. ", end="")
    finalize(ev, run_dir)


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default="resume_tailor.json", type=Path)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("setup")
    r = sub.add_parser("run")
    r.add_argument("--company", required=True)
    r.add_argument("--role", required=True)
    r.add_argument("--jd", required=True, type=Path, help="text file containing the full job description")
    c = sub.add_parser("check")
    c.add_argument("answer", type=Path, help="file with a NotebookLM answer (or raw LaTeX section)")
    j = sub.add_parser("add-jd", help="upload a JD as a source for web-UI use and print the message to send")
    j.add_argument("--company", required=True)
    j.add_argument("--role", required=True)
    j.add_argument("--jd", required=True, type=Path, help="text file containing the full job description")
    args = ap.parse_args()
    cfg = load_config(args.config)

    if args.cmd == "setup":
        asyncio.run(cmd_setup(cfg))
    elif args.cmd == "run":
        asyncio.run(cmd_run(cfg, args.company, args.role, args.jd.read_text(encoding="utf-8-sig")))
    elif args.cmd == "add-jd":
        asyncio.run(cmd_add_jd(cfg, args.company, args.role, args.jd.read_text(encoding="utf-8-sig")))
    else:
        cmd_check(cfg, args.answer)


if __name__ == "__main__":
    main()
