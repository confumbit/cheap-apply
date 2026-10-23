"""Offline integration tests: no scraping, NotebookLM, or LaTeX calls."""
import csv
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import resume_pipeline as pipeline


class ResumePipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=pipeline.ROOT)
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.csv = self.root / "jobs.csv"
        self.pdf = self.root / "final" / "resume.pdf"
        self.pdf.parent.mkdir()
        self.pdf.write_bytes(b"%PDF-test")

    def write(self, jobs, *, named=True):
        header = ["company", "title", "location", "description", "job_url"]
        if named:
            header += ["min_years_experience", "resume_pdf"]
        else:
            header += [f"field_{i}" for i in range(5, 29)] + ["experience_range"]
        rows = []
        for job in jobs:
            data = dict(company="Acme", title="Engineer", location="Pune", description="Full JD", job_url="")
            data.update(job)
            rows.append([data.get(h, "") for h in header])
        pipeline.save_csv(self.csv, header, rows)

    def read(self):
        with self.csv.open(newline="", encoding="utf-8") as stream:
            return list(csv.DictReader(stream))

    def test_current_threshold_and_missing(self):
        for value in ("", "null", "unknown", "0", "2", "2-4 Yrs", "2.5", "3", "3-5 Yrs", "3.0"):
            self.assertTrue(pipeline.eligible(value, 3), value)
        for value in ("4", "4-5 Yrs", "4.0"):
            self.assertFalse(pipeline.eligible(value, 3), value)
        with self.assertRaises(ValueError):
            pipeline.eligible("failed extraction", 3)

    @patch.object(pipeline, "tailor_one")
    def test_duplicates_filter_links_and_restart(self, tailor):
        tailor.return_value = self.pdf
        self.write([
            {"min_years_experience": "2", "job_url": "https://board-a/jobs/1"},
            {"min_years_experience": "", "company": "ACME", "job_url": "https://board-b/jobs/9", "description": "A fuller JD"},
            {"min_years_experience": "4", "resume_pdf": str(self.pdf)},
            {"min_years_experience": "5"},
            {"min_years_experience": "0", "title": "Data Scientist"},
        ])
        result = pipeline.tailor_jobs(self.csv, 3)
        self.assertEqual((result["eligible"], result["groups"], result["generated"]), (3, 2, 2))
        rows = self.read()
        self.assertEqual(rows[0]["resume_pdf"], rows[1]["resume_pdf"])
        self.assertEqual(rows[2]["resume_pdf"], "")
        self.assertEqual(rows[3]["resume_pdf"], "")
        self.assertEqual(rows[1]["description"], "A fuller JD")
        self.assertEqual(tailor.call_args_list[0].args[2], "A fuller JD")
        tailor.reset_mock()
        result = pipeline.tailor_jobs(self.csv, 3)
        tailor.assert_not_called()
        self.assertEqual(result["reused"], 2)

    @patch.object(pipeline, "tailor_one")
    def test_classifier_ad_column(self, tailor):
        tailor.return_value = self.pdf
        self.write([{"experience_range": "2"}, {"experience_range": "4", "title": "Other"}], named=False)
        pipeline.tailor_jobs(self.csv, 3)
        self.assertEqual(tailor.call_count, 1)
        self.assertEqual(self.read()[0]["resume_pdf"], str(self.pdf.resolve()))

    @patch.object(pipeline, "tailor_one")
    def test_explicit_classifier_column_overrides_old_named_column(self, tailor):
        tailor.return_value = self.pdf
        self.write([{"min_years_experience": "1"}])
        rows = self.read()
        header = list(rows[0]) + ["experience_range"]
        pipeline.save_csv(self.csv, header, [[rows[0][k] for k in rows[0]] + ["5"]])
        pipeline.tailor_jobs(self.csv, 3, experience_column="experience_range")
        tailor.assert_not_called()

    @patch.object(pipeline, "tailor_one")
    def test_dry_run_does_not_write_or_call_tailor(self, tailor):
        self.write([{"min_years_experience": "2"}, {"min_years_experience": ""}])
        original = self.csv.read_bytes()
        result = pipeline.tailor_jobs(self.csv, 3, dry_run=True)
        self.assertEqual((result["eligible"], result["groups"]), (2, 1))
        self.assertEqual(self.csv.read_bytes(), original)
        tailor.assert_not_called()

    @patch.object(pipeline, "tailor_one")
    def test_failure_is_once_per_group_and_later_jobs_complete(self, tailor):
        tailor.side_effect = [RuntimeError("offline"), self.pdf]
        self.write([
            {"min_years_experience": "2"}, {"min_years_experience": "1"},
            {"min_years_experience": "", "title": "Other"},
        ])
        with self.assertLogs(pipeline.logger, level="ERROR"):
            with self.assertRaises(RuntimeError):
                pipeline.tailor_jobs(self.csv, 3)
        self.assertEqual(tailor.call_count, 2)
        rows = self.read()
        self.assertEqual([r["resume_pdf"] for r in rows], ["", "", str(self.pdf.resolve())])

    @patch.object(pipeline, "tailor_one")
    def test_invalid_experience_is_skipped_and_reported(self, tailor):
        tailor.return_value = self.pdf
        self.write([
            {"min_years_experience": "classification error"},
            {"min_years_experience": "2", "title": "Other"},
        ])
        with self.assertLogs(pipeline.logger, level="WARNING"):
            with self.assertRaises(RuntimeError):
                pipeline.tailor_jobs(self.csv, 3)
        self.assertEqual(tailor.call_count, 1)
        self.assertEqual(self.read()[0]["resume_pdf"], "")

    def test_matching_jd_handles_different_location_labels(self):
        description = " ".join(f"word{i}" for i in range(70))
        self.write([
            {"description": description, "location": "Pune, India"},
            {"description": description + " Apply today", "location": "Pune, Maharashtra"},
            {"description": "Different job", "location": "Delhi"},
            {"description": description, "title": "Senior Engineer"},
        ])
        with self.csv.open(newline="", encoding="utf-8") as stream:
            records = list(csv.reader(stream))
        groups = pipeline.duplicate_groups(records[1:], {h: i for i, h in enumerate(records[0])})
        self.assertEqual(groups, [[0, 1], [2], [3]])

    def test_url_tracking_removed_but_requisition_ids_preserved(self):
        self.assertEqual(pipeline.canonical_url("https://jobs.test/view?id=1&utm_source=a"),
                         pipeline.canonical_url("http://jobs.test/view/?id=1&utm_source=b"))
        self.assertNotEqual(pipeline.canonical_url("https://jobs.test/view?id=1"),
                            pipeline.canonical_url("https://jobs.test/view?id=2"))

    @patch.object(pipeline.subprocess, "Popen")
    def test_existing_tailor_cli_and_output_directory(self, run):
        import io
        run.return_value.stdout = io.StringIO(f"Best attempt: round 1\nOutput: {self.pdf.parent}\n")
        run.return_value.wait.return_value = 0
        result = pipeline.tailor_one("Acme", "Engineer", "Full job description", pipeline.CONFIG)
        self.assertEqual(result, self.pdf.resolve())
        command = run.call_args.args[0]
        self.assertEqual(command[:6], [pipeline.sys.executable, "-u", str(pipeline.TAILOR), "--config", str(pipeline.CONFIG), "run"])
        self.assertNotIn("--output-dir", command)
        self.pdf.unlink()
        run.return_value.stdout = io.StringIO(f"Output: {self.pdf.parent}\n")
        with self.assertRaisesRegex(RuntimeError, "no PDF"):
            pipeline.tailor_one("Acme", "Engineer", "JD", pipeline.CONFIG)


if __name__ == "__main__":
    unittest.main()
