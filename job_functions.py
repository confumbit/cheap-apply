import re, tempfile, os, logging, csv, requests, json

from datetime import date

from jobspy import scrape_jobs

logger = logging.getLogger("main")

EXPERIENCE = 3
CSV_PATH = "jobs-2026-10-19.csv"
HR_PROMPT = """
Find up to 3 work emails for recruiters or hiring managers
relevant to this specific job. SEARCH THE WEB; never rely
on memory.

Job:
{job}

SEARCH ORDER:
1. Inspect the posting for the job poster, hiring team,
   recruiter, job ID, or a published contact email.
2. Search the employer's careers pages and hiring posts.
3. Identify recruiters and hiring managers for the role's
   function and location.
4. Find their publicly published professional emails.
5. If unavailable, infer an address ONLY when:
   - The person's current employment is evidenced.
   - At least 2 different, publicly published employee
     addresses demonstrate the SAME company email format.
   - The inferred address follows that format exactly.

PRIORITY:
A. Recruiter explicitly associated with this vacancy
B. Recruiter/talent partner covering its function and location
C. Hiring manager responsible for this team
D. General company recruitment mailbox

RULES:
- Prefer direct contacts over general mailboxes.
- Do not confuse a relevant employee with a confirmed
  recruiter for this specific vacancy.
- Do not invent names, emails, source URLs or pattern examples.
- LinkedIn search snippets may identify people, but are not
  evidence of their email address.
- Use only professional contact details, not personal emails.
- Inferred emails are UNVERIFIED, not confirmed mailboxes.
- Return fewer than 3 results if necessary, including zero.
- Keep research focused; avoid unrelated leadership searches.
- Return compact JSON ONLY. No Markdown.

SCHEMA:
{{
  "contacts": [
    {{
      "name": "Person Name",
      "title": "Technical Recruiter",
      "email": "name@company.com",
      "type": "public",
      "source": "https://page-containing-email",
      "person_url": "https://page-showing-employment",
      "pattern_examples": []
    }}
  ]
}}

For inferred emails:
- Set type to "inferred".
- Include person_url proving current employment.
- Include 2 distinct published employee addresses
  and their source URLs in pattern_examples:
  [{{"email":"", "url":""}}, {{"email":"", "url":""}}]
- Both examples must belong to the same company domain
  and demonstrate the same naming pattern.
"""


def scraper() -> None:
    jobs = scrape_jobs(
        site_name=[
            "indeed",
            "linkedin",
            "zip_recruiter",
            "naukri",
            "bayt",
            "google",
        ],  # "glassdoor", "bdjobs"
        # site_name="linkedin",
        search_term="machine learning engineer",
        google_search_term="flutter",
        location="India",
        distance=None,
        # is_remote=True,
        results_wanted=50,
        hours_old=720,
        fetch_description=True,
        country_indeed="India",
        # fetch_description=True # for boards whose search results don't include the description (slower)
        # proxies=["208.195.175.46:65095", "208.195.175.45:65095", "localhost"],
    )
    logger.info(f"Found {len(jobs)} jobs")

    path = f"jobs-{date.today()}.csv"
    jobs.to_csv(
        path, quoting=csv.QUOTE_NONNUMERIC, escapechar="\\", index=False
    )  # to_excel
    if len(jobs) < 50:
        logger.warning("Found lesser than 50 jobs!")

    return path


def classify_jobs(
    path, model="qwen3.5:0.8b", url="http://localhost:11434/api/chat", header=True
):
    T, AD = 19, 29  # zero-based indexes of columns T and AD
    schema = {
        "type": "object",
        "properties": {"min_years_experience": {"type": ["integer", "null"]}},
        "required": ["min_years_experience"],
    }
    system = (
        "You read lines from a job posting and report the minimum years of professional "
        "experience it explicitly requires, as an integer. Use 0 for entry-level or "
        "freshers, and null if it is not stated. Do not guess."
    )
    clue = re.compile(
        r"\d|\byears?\b|\byrs?\b|\byoe\b|experience|\bexp\b|fresher|entry[- ]level",
        re.I,
    )
    explicit_min = re.compile(
        r"(?<![\w.])(\d+)\s*\+?\s*(?:(?:-|\u2010|\u2011|\u2012|\u2013|\u2014|\u2212|\bto\b)\s*\d+\s*\+?\s*)?"
        r"(?:years?|yrs?|yoe)\b",
        re.I,
    )

    def snip(desc):  # keep only sentences/lines containing a clue
        parts = re.split(r"(?<=[.!?;])\s+|\n+|\u2022", desc or "")
        return "\n".join(p.strip() for p in parts if clue.search(p))

    def minimum_from_text(text):
        # Only use a numeric value when the same line ties it to experience.
        for part in re.split(r"\n+|(?<=[.!?;])\s+|\u2022", text or ""):
            if re.search(r"\b(?:more than|over)\s+\d+\s+(?:years?|yrs?)\b", part, re.I):
                continue
            if re.search(r"\b(?:experience|exp|yoe|years?|yrs?)\b", part, re.I):
                m = explicit_min.search(part)
                if m:
                    return int(m.group(1))
        return None

    def ask(text):
        need = (len(system) + len(text)) // 3 + 512  # rough token estimate + headroom
        ctx = max(4096, 1 << (need - 1).bit_length())  # round up to a power of two
        r = requests.post(
            url,
            timeout=600,
            json={
                "model": model,
                "stream": False,
                "think": False,
                "format": schema,
                "options": {"temperature": 0, "num_ctx": ctx},
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": text},
                ],
            },
        )
        r.raise_for_status()
        return json.loads(r.json()["message"]["content"])["min_years_experience"]

    def save():  # temp file + atomic swap, so a crash can't corrupt the CSV
        fd, tmp = tempfile.mkstemp(
            dir=os.path.dirname(os.path.abspath(path)), suffix=".csv"
        )
        with os.fdopen(fd, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerows(rows)
        os.replace(tmp, path)

    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))

    try:
        for i, row in enumerate(rows):
            if not row or (header and i == 0):
                continue
            row += [""] * (AD + 1 - len(row))
            cell = row[AD].strip()
            if cell:  # "a-b Yrs" -> "a"
                if m := re.match(r"(\d+)\s*[-\u2010-\u2015\u2212]\s*\d+", cell):
                    row[AD] = m.group(1)
            else:
                years = minimum_from_text(row[T])
                if years is not None and 0 <= years <= 40:
                    row[AD] = str(years)
                elif text := snip(row[T]):
                    try:
                        years = ask(text)
                        if isinstance(years, int) and 0 <= years <= 40:
                            row[AD] = str(years)
                    except Exception as e:
                        print(f"row {i + 1}: {e}")
            print(f"row {i + 1}/{len(rows)} -> {row[AD]!r}")
    finally:
        save()  # also runs on Ctrl-C, so partial progress is kept


def scrape_hr():
    from urllib.parse import urlparse
    from hermex import ChatGPT

    AD = 29  # Experience column
    EMAIL = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+$")
    FIELDS = ("company", "title", "job_url", "location", "description")

    with open(CSV_PATH, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.reader(f))

    if not rows:
        return

    cols = {h.strip().lower(): i for i, h in enumerate(rows[0])}
    missing = set(FIELDS) - cols.keys()
    if missing:
        raise ValueError(f"Missing columns: {sorted(missing)}")

    for field in ("hr_emails", "hr_evidence", "hr_status"):
        if field not in cols:
            cols[field] = len(rows[0])
            rows[0].append(field)

    width = max(len(rows[0]), AD + 1)

    def chrome_version():
        if os.name != "nt":
            return None

        import winreg

        keys = (
            (winreg.HKEY_CURRENT_USER, r"Software\Google\Chrome\BLBeacon"),
            (winreg.HKEY_LOCAL_MACHINE, r"Software\Google\Chrome\BLBeacon"),
            (winreg.HKEY_LOCAL_MACHINE, r"Software\WOW6432Node\Google\Chrome\BLBeacon"),
        )

        for root, path in keys:
            try:
                with winreg.OpenKey(root, path) as key:
                    version, _ = winreg.QueryValueEx(key, "version")

                match = re.match(r"(\d+)\.", version)
                if match:
                    return int(match.group(1))

            except FileNotFoundError:
                continue

        raise RuntimeError("Could not determine Chrome version from Windows registry.")

    def save():
        fd, tmp = tempfile.mkstemp(
            dir=os.path.dirname(os.path.abspath(CSV_PATH)), suffix=".csv"
        )
        try:
            with os.fdopen(fd, "w", newline="", encoding="utf-8-sig") as f:
                csv.writer(f).writerows(rows)
            os.replace(tmp, CSV_PATH)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def valid_url(url):
        try:
            p = urlparse(url)
            return p.scheme in ("https", "http") and bool(p.netloc)
        except (TypeError, ValueError):
            return False

    def parse(text):
        # Decode first JSON object; tolerate surrounding code fences.
        start = (text or "").find("{")
        if start < 0:
            raise ValueError("Missing JSON response")

        data, _ = json.JSONDecoder().raw_decode(text[start:])
        contacts = data["contacts"]
        if not isinstance(contacts, list):
            raise ValueError("Invalid contacts array")

        found, seen = [], set()

        for c in contacts:
            if not isinstance(c, dict):
                continue

            email = str(c.get("email") or "").strip().lower()
            kind = c.get("type")
            source = c.get("source")
            person_url = c.get("person_url")
            examples = c.get("pattern_examples") or []

            if not EMAIL.fullmatch(email) or email in seen:
                continue

            if kind == "public":
                if not valid_url(source):
                    continue

            elif kind == "inferred":
                if not valid_url(person_url):
                    continue
                if not isinstance(examples, list) or len(examples) < 2:
                    continue

                # Require two different published examples
                # from the proposed company's email domain.
                domain = email.rsplit("@", 1)[1]
                verified_examples = {
                    str(e.get("email", "")).lower()
                    for e in examples
                    if isinstance(e, dict)
                    and valid_url(e.get("url"))
                    and EMAIL.fullmatch(str(e.get("email", "")))
                    and str(e["email"]).lower().endswith("@" + domain)
                }

                if len(verified_examples) < 2:
                    continue
            else:
                continue

            c["email"] = email
            found.append(c)
            seen.add(email)

            if len(found) == 3:
                break

        return found

    def job_key(row):
        return tuple(
            row[cols[k]].strip().lower()
            for k in ("company", "title", "location", "job_url")
        )

    # Normalize rows once, then cache previously completed jobs.
    cache = {}

    for row in rows[1:]:
        row.extend([""] * (width - len(row)))
        if row[cols["hr_status"]] in ("found", "none"):
            cache[job_key(row)] = tuple(
                row[cols[k]] for k in ("hr_emails", "hr_evidence", "hr_status")
            )

    chatgpt = None

    try:
        for i, row in enumerate(rows[1:], start=2):
            experience = re.match(r"\s*(\d+)", row[AD])
            if not experience or int(experience.group(1)) > EXPERIENCE:
                continue

            if row[cols["hr_status"]] in ("found", "none"):
                continue

            # Preserve existing emails from older successful runs.
            if row[cols["hr_emails"]].strip():
                row[cols["hr_status"]] = "found"
                save()
                continue

            key = job_key(row)
            if key in cache:
                for field, value in zip(
                    ("hr_emails", "hr_evidence", "hr_status"), cache[key]
                ):
                    row[cols[field]] = value
                save()
                continue

            job = {k: row[cols[k]].strip() for k in FIELDS}

            if not job["company"] or not job["title"]:
                continue

            # Keep both ends of the JD: the ending sometimes
            # contains recruiter details or an application email.
            jd = job["description"]
            job["description"] = (
                jd if len(jd) <= 1400 else jd[:1050] + "\n...\n" + jd[-350:]
            )

            try:
                if chatgpt is None:
                    chatgpt = ChatGPT(
                        chrome_version=chrome_version(),
                        data_dir=os.getenv(
                            "HERMEX_DATA_DIR",
                            os.path.join(
                                os.path.dirname(os.path.abspath(CSV_PATH)), ".hermex"
                            ),
                        ),
                    )

                chatgpt.open_url()
                response = chatgpt.query(
                    HR_PROMPT.format(job=json.dumps(job, ensure_ascii=False)), paste=True
                )

                contacts = parse(response.text)
                values = (
                    ",".join(c["email"] for c in contacts),
                    json.dumps(contacts, ensure_ascii=False),
                    "found" if contacts else "none",
                )

                for field, value in zip(
                    ("hr_emails", "hr_evidence", "hr_status"), values
                ):
                    row[cols[field]] = value

                cache[key] = values
                save()
                print(f"{i}/{len(rows)}: {values[2]} -> {values[0]}")

            except Exception as e:
                # Retry on next run, without marking the job complete.
                print(f"Row {i}: {e}")

    finally:
        if chatgpt is not None:
            chatgpt.close()
        save()
