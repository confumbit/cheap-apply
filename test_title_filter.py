"""Offline title-filter checks. Ollama and resume generation are mocked."""
import csv
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import requests

import job_functions as jobs
import resume_pipeline as pipeline


class TitleFilterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=pipeline.ROOT)
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.csv = self.root / "jobs.csv"
        self.cache = self.root / "title-cache.json"

    def write(self, rows):
        header = list(dict.fromkeys(key for row in rows for key in row))
        pipeline.save_csv(self.csv, header, [[row.get(key, "") for key in header] for row in rows])

    def read(self):
        with self.csv.open(newline="", encoding="utf-8") as stream:
            return list(csv.DictReader(stream))

    def response(self, value):
        response = Mock()
        response.json.return_value = {"message": {"content": json.dumps({"correct_domain": value})}}
        return response

    def check(self, **kwargs):
        jobs.check_job_titles(self.csv, cache_path=self.cache, **kwargs)

    def test_related_titles_use_small_structured_requests_and_cache(self):
        titles = ["AI Engineer", "Software Developer", "Data Scientist", "Data Analyst", "Senior Developer", "AI Engineer"]
        self.write([{"title": title, "description": "MUST NOT be sent to the model"} for title in titles])
        with patch.object(jobs.requests, "post", return_value=self.response(True)) as ask:
            self.check()
            self.assertEqual(ask.call_count, 5)
            self.check()
            self.assertEqual(ask.call_count, 5)
        payload = ask.call_args.kwargs["json"]
        self.assertEqual(payload["model"], "qwen3.5:0.8b")
        self.assertLessEqual(payload["options"]["num_predict"], 64)
        self.assertFalse(payload["think"])
        self.assertNotIn("MUST NOT", json.dumps(payload))
        self.assertEqual([row["correct_domain"] for row in self.read()], ["true"] * 6)

    def test_executives_and_empty_titles_are_rejected_without_model_calls(self):
        titles = ["Assistant Vice President Automation Engineer with Java and Selenium",
                  "Vice President of Engineering", "AVP Data Science", "VP Engineering",
                  "CTO", "Director of AI", "Head of Engineering", ""]
        self.write([{"title": title} for title in titles])
        with patch.object(jobs.requests, "post") as ask:
            self.check()
        ask.assert_not_called()
        self.assertTrue(all(row["correct_domain"] == "false" for row in self.read()))

    def test_unrelated_role_and_changed_search_term(self):
        self.write([{"title": "Java Selenium Automation Engineer"}])
        with patch.object(jobs.requests, "post", return_value=self.response(False)) as ask:
            self.check(search_title="machine learning engineer")
            self.check(search_title="QA automation engineer")
        self.assertEqual(ask.call_count, 1)  # ML/AI QA exclusion is local; QA search uses the model.
        self.assertEqual(self.read()[0]["correct_domain"], "false")

    def test_clear_qa_titles_are_rejected_only_for_ml_ai_searches(self):
        self.write([{"title": title} for title in
                    ["Java Selenium Automation Engineer", "Software Test Engineer", "SDET", "QA Analyst"]])
        with patch.object(jobs.requests, "post", return_value=self.response(True)) as ask:
            self.check(search_title="machine learning engineer")
            ask.assert_not_called()
            self.assertTrue(all(row["correct_domain"] == "false" for row in self.read()))
            self.check(search_title="QA automation engineer")
            self.assertEqual(ask.call_count, 4)
            self.assertTrue(all(row["correct_domain"] == "true" for row in self.read()))

    def test_failed_or_invalid_answers_do_not_become_false_classifications(self):
        for result in [requests.ConnectionError("offline"), self.response("true")]:
            self.write([{"title": "AI Engineer", "correct_domain": "true", "eligible": "true"}])
            with patch.object(jobs.requests, "post", side_effect=[result]):
                with self.assertRaises((requests.ConnectionError, ValueError)):
                    self.check()
            row = self.read()[0]
            self.assertEqual(row["correct_domain"], "")
            self.assertEqual(row["eligible"], "false")

    def test_eligibility_is_domain_and_inclusive_threshold_with_unknowns(self):
        values = ["0", "2", "3", "4", "", "invalid", "2"]
        self.write([{"title": "Engineer", "correct_domain": "false" if i == 6 else "true",
                     "experience_range": value} for i, value in enumerate(values)])
        jobs.mark_eligible(self.csv, experience=3)
        self.assertEqual([row["eligible"] for row in self.read()],
                         ["true", "true", "true", "false", "true", "false", "false"])

    def test_resume_pipeline_honors_domain_and_final_eligibility(self):
        self.write([{"company": "Example", "title": title, "location": "India", "description": "JD",
                     "min_years_experience": "3", "correct_domain": domain, "eligible": qualified}
                    for title, domain, qualified in [("AI Engineer", "true", "true"),
                                                      ("Director of AI", "false", "false"),
                                                      ("Data Scientist", "true", "false")]])
        result = pipeline.tailor_jobs(self.csv, 3, dry_run=True)
        self.assertEqual(result["eligible"], 1)

    def test_classify_stage_includes_titles_and_eligibility_before_publishing(self):
        import main
        from workflow_support import Recovery
        self.write([{"title": "AI Engineer", "experience_range": "2"}])
        def filter_titles(path, search_title):
            jobs.check_job_titles(path, search_title, cache_path=self.cache)
        def tailor(path, **kwargs):
            self.assertEqual(self.read()[0]["correct_domain"], "true")
            self.assertEqual(self.read()[0]["eligible"], "true")
        with patch.object(jobs.requests, "post", return_value=self.response(True)), \
             patch.object(main, "classify_remaining"), \
             patch.object(main, "check_job_titles", side_effect=filter_titles), \
             patch.object(main, "tailor_jobs", side_effect=tailor) as generate:
            state = main.run_workflow(csv_path=self.csv, start_at="classify",
                                      checkpoint=self.root / "state.json", recovery=Recovery(False))
        generate.assert_called_once()
        self.assertTrue(state["title_filter_complete"])
        self.assertEqual(state["stage"], "complete")


if __name__ == "__main__":
    unittest.main()
