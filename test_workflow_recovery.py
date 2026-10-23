"""Recovery tests using temporary CSVs and fake external operations."""
import csv
from contextlib import redirect_stdout
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import main
import resume_pipeline as pipeline
from workflow_support import Recovery, WorkflowStopped, load_json, save_json


class WorkflowRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=pipeline.ROOT)
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.checkpoint = self.root / "workflow" / "state.json"
        self.csv = self.root / "jobs.csv"
        self.config = self.root / "resume_tailor.json"
        self.output = self.root / "tailor-runs"
        save_json(self.config, {"output_dir": "tailor-runs"})
        self.recovery = Recovery(False)
        self.header = ["company", "title", "location", "description", "job_url"]
        self.header += [f"col_{i}" for i in range(5, 29)] + ["experience_range"]
        self.rows = []
        for title in ("Engineer", "Scientist", "Analyst"):
            row = [""] * len(self.header)
            row[:4] = ["Acme", title, "Pune", "JD: " + title]
            self.rows.append(row)
        pipeline.save_csv(self.csv, self.header, self.rows)

    def classifier(self, path):
        with open(path, newline="", encoding="utf-8") as stream:
            records = list(csv.reader(stream))
        records[1][29] = "2"
        pipeline.save_csv(Path(path), records[0], records[1:])

    def fake_pdf(self, company, title, jd, config):
        final = self.output / title / "final"
        final.mkdir(parents=True, exist_ok=True)
        pdf = final / "resume.pdf"
        pdf.write_bytes(b"%PDF-test")
        (final / "issues.txt").write_text("", encoding="utf-8")
        return pdf.resolve()

    def test_interactive_login_retry(self):
        action_calls = []

        def action():
            action_calls.append(1)
            if len(action_calls) == 1:
                raise RuntimeError("NotebookLM authentication failed; run notebooklm login")
            return "done"

        with patch("builtins.input", return_value="") as prompt, self.assertLogs(level="INFO") as logs:
            self.assertEqual(Recovery(True).run("Tailor Acme", action), "done")
        self.assertEqual(len(action_calls), 2)
        self.assertIn("retry", prompt.call_args.args[0])
        self.assertTrue(any("notebooklm login" in line for line in logs.output))

    def test_quit_and_noninteractive_do_not_loop(self):
        with patch("builtins.input", return_value="q"), self.assertLogs(level="ERROR"):
            with self.assertRaises(WorkflowStopped):
                Recovery(True).run("Save CSV", lambda: (_ for _ in ()).throw(PermissionError("open in Excel")))
        with patch("builtins.input") as prompt, self.assertLogs(level="ERROR"):
            with self.assertRaises(PermissionError):
                self.recovery.run("Save CSV", lambda: (_ for _ in ()).throw(PermissionError("locked")))
        prompt.assert_not_called()

    def test_resume_classification_at_next_row_without_scraping(self):
        classified = []

        def interrupt_second(path):
            if len(classified) == 1:
                raise KeyboardInterrupt
            self.classifier(path)
            classified.append(path)

        with patch.object(main, "scraper") as scrape, patch.object(main, "classify_jobs", side_effect=interrupt_second):
            with self.assertRaises(KeyboardInterrupt):
                main.run_workflow(checkpoint=self.checkpoint, csv_path=self.csv, recovery=self.recovery)
        scrape.assert_not_called()
        state = load_json(self.checkpoint)
        self.assertEqual((state["stage"], state["next_row"]), ("classify", 1))
        with patch.object(main, "scraper") as scrape, patch.object(main, "classify_jobs", side_effect=self.classifier) as classify, \
             patch.object(main, "tailor_jobs") as tailor:
            result = main.run_workflow(checkpoint=self.checkpoint, recovery=self.recovery)
        self.assertEqual(classify.call_count, 2)
        scrape.assert_not_called()
        tailor.assert_called_once()
        self.assertEqual(result["stage"], "complete")
        with self.csv.open(newline="", encoding="utf-8") as stream:
            self.assertEqual([row[29] for row in list(csv.reader(stream))[1:]], ["2", "2", "2"])

    def test_classifier_printed_error_requires_retry_same_row(self):
        calls = []

        def flaky(path):
            calls.append(path)
            if len(calls) == 1:
                print("row 2: Ollama connection refused")
            else:
                self.classifier(path)

        with patch.object(main, "classify_jobs", side_effect=flaky), patch.object(main, "tailor_jobs"), \
             patch("builtins.input", return_value=""), self.assertLogs(level="ERROR"):
            result = main.run_workflow(checkpoint=self.checkpoint, csv_path=self.csv, recovery=Recovery(True))
        self.assertEqual(len(calls), 4)
        self.assertEqual(result["stage"], "complete")

    def test_real_classifier_results_equal_batch_results(self):
        header = [f"column_{i}" for i in range(30)]
        header[0], header[1], header[19], header[29] = "company", "title", "description", "experience_range"
        rows = []
        for jd, years in [("Minimum 2 years experience.", ""), ("Requires 5-7 years experience.", ""),
                          ("Develop software.", ""), ("Develop software.", "3-5 Yrs")]:
            row = [""] * 30
            row[0], row[1], row[19], row[29] = "Acme", "Engineer", jd, years
            rows.append(row)
        pipeline.save_csv(self.csv, header, rows)
        batch = self.root / "batch.csv"
        pipeline.save_csv(batch, header, rows)
        with redirect_stdout(io.StringIO()), patch.object(main, "tailor_jobs"):
            main.classify_jobs(str(batch))
            main.run_workflow(checkpoint=self.checkpoint, csv_path=self.csv, recovery=self.recovery)
        self.assertEqual(self.csv.read_bytes(), batch.read_bytes())

    def test_start_at_tailoring_skips_scraper_and_classifier(self):
        with patch.object(main, "scraper") as scrape, patch.object(main, "classify_jobs") as classify, \
             patch.object(main, "tailor_jobs") as tailor:
            result = main.run_workflow(checkpoint=self.checkpoint, csv_path=self.csv,
                                       start_at="tailor", recovery=self.recovery)
        scrape.assert_not_called()
        classify.assert_not_called()
        self.assertEqual(tailor.call_args.kwargs["experience_column"], None)
        self.assertEqual(result["stage"], "complete")

    def test_locked_csv_after_classification_does_not_reclassify_on_restart(self):
        real_atomic = main.atomic_bytes

        def locked_publish(path, data):
            if path == self.csv and self.checkpoint.exists() and load_json(self.checkpoint)["stage"] == "publish_classification":
                raise PermissionError("CSV open in Excel")
            return real_atomic(path, data)

        with patch.object(main, "classify_jobs", side_effect=self.classifier), patch.object(main, "atomic_bytes", side_effect=locked_publish), \
             self.assertLogs(level="ERROR"):
            with self.assertRaises(PermissionError):
                main.run_workflow(checkpoint=self.checkpoint, csv_path=self.csv, recovery=self.recovery)
        self.assertEqual(load_json(self.checkpoint)["stage"], "publish_classification")
        with patch.object(main, "classify_jobs") as classify, patch.object(main, "scraper") as scrape, patch.object(main, "tailor_jobs"):
            main.run_workflow(checkpoint=self.checkpoint, recovery=self.recovery)
        classify.assert_not_called()
        scrape.assert_not_called()

    def test_full_scrape_publish_classify_tailor_order_and_completed_rerun(self):
        calls = []

        def scrape():
            calls.append("scrape")
            pipeline.save_csv(Path("scraped.csv"), self.header, self.rows[:1])
            return "scraped.csv"

        def classify(path):
            calls.append("classify")
            self.classifier(path)

        with patch.object(main, "ROOT", self.root), patch.object(main, "scraper", side_effect=scrape), \
             patch.object(main, "classify_jobs", side_effect=classify), \
             patch.object(main, "tailor_jobs", side_effect=lambda *a, **k: calls.append("tailor")):
            main.run_workflow(checkpoint=self.checkpoint, recovery=self.recovery)
            main.run_workflow(checkpoint=self.checkpoint, recovery=self.recovery)
        self.assertEqual(calls, ["scrape", "classify", "tailor"])
        self.assertTrue((self.root / "scraped.csv").is_file())

    def test_receipt_survives_csv_save_failure_and_restart(self):
        real_save = pipeline.save_csv
        written = []

        def locked(path, header, rows):
            # The CSV becomes locked after the first PDF was generated.
            if written:
                raise PermissionError("CSV locked")
            return real_save(path, header, rows)

        def tailor(*args):
            pdf = self.fake_pdf(*args)
            written.append(pdf)
            return pdf

        with patch.object(pipeline, "tailor_one", side_effect=tailor) as first, \
             patch.object(pipeline, "save_csv", side_effect=locked), self.assertLogs(level="ERROR"):
            with self.assertRaises(PermissionError):
                pipeline.tailor_jobs(self.csv, 3, config=self.config)
        self.assertEqual(first.call_count, 1)
        with patch.object(pipeline, "tailor_one", side_effect=self.fake_pdf) as second:
            pipeline.tailor_jobs(self.csv, 3, config=self.config)
        self.assertEqual(second.call_count, 2)  # first PDF recovered, remaining two generated
        with self.csv.open(newline="", encoding="utf-8") as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual(rows[0]["resume_pdf"], str(written[0]))

    def test_locked_csv_retry_in_same_process_does_not_regenerate(self):
        real_save = pipeline.save_csv
        locked = [False]

        def tailor(*args):
            locked[0] = True
            return self.fake_pdf(*args)

        def save(path, header, rows):
            if locked[0]:
                raise PermissionError("Excel lock")
            return real_save(path, header, rows)

        def unlock(prompt):
            locked[0] = False
            return ""

        with patch.object(pipeline, "tailor_one", side_effect=tailor) as run, \
             patch.object(pipeline, "save_csv", side_effect=save), patch("builtins.input", side_effect=unlock), \
             self.assertLogs(level="ERROR"):
            pipeline.tailor_jobs(self.csv, 3, config=self.config, recovery=Recovery(True))
        self.assertEqual(run.call_count, 3)

    def test_finished_pdf_recovered_when_interrupt_prevents_receipt(self):
        journal = pipeline.ResumeJournal(self.csv, self.config, self.recovery)

        def finish_and_interrupt(company, title, jd, config):
            pdf = self.fake_pdf(company, title, jd, config)
            jd = jd.replace("\r\n", "\n").replace("\r", "\n")
            (pdf.parent.parent / "jd.txt").write_text(f"{company}\n{title}\n\n{jd}", encoding="utf-8")
            raise KeyboardInterrupt

        with patch.object(pipeline, "tailor_one", side_effect=finish_and_interrupt):
            with self.assertRaises(KeyboardInterrupt):
                journal.tailor("Acme", "Engineer", "JD\r\nwith Windows line breaks")
        restarted = pipeline.ResumeJournal(self.csv, self.config, self.recovery)
        with patch.object(pipeline, "tailor_one") as run:
            pdf = restarted.tailor("Acme", "Engineer", "JD\r\nwith Windows line breaks")
        run.assert_not_called()
        self.assertTrue(pdf.is_file())

    def test_live_tailor_logs_before_process_exits(self):
        pdf = self.fake_pdf("Acme", "Engineer", "JD", self.config)
        with patch.object(pipeline.subprocess, "Popen") as spawn, self.assertLogs(pipeline.logger, "INFO") as logs:
            spawn.return_value.stdout = io.StringIO(f"Asking NotebookLM (round 1)...\nOutput: {pdf.parent}\n")

            def wait():
                self.assertTrue(any("round 1" in line for line in logs.output))
                return 0

            spawn.return_value.wait.side_effect = wait
            self.assertEqual(pipeline.tailor_one("Acme", "Engineer", "JD", self.config), pdf)

    def test_stops_child_on_interrupt(self):
        class InterruptStream:
            def __iter__(self):
                return self

            def __next__(self):
                raise KeyboardInterrupt

            def close(self):
                pass

        with patch.object(pipeline.subprocess, "Popen") as spawn:
            spawn.return_value.stdout = InterruptStream()
            with self.assertRaises(KeyboardInterrupt):
                pipeline.tailor_one("Acme", "Engineer", "JD", self.config)
        spawn.return_value.terminate.assert_called_once()
        spawn.return_value.wait.assert_called_once_with(timeout=10)

    def test_cli_interrupt_returns_130(self):
        with patch.object(main, "run_workflow", side_effect=KeyboardInterrupt):
            self.assertEqual(main.main(["--non-interactive", "--checkpoint", str(self.checkpoint)]), 130)


if __name__ == "__main__":
    unittest.main()
