import unittest

from engine import build_draft_prompt, classify
from app.workflows import verified_context


class EngineTests(unittest.TestCase):
    def test_scraped_site_text_is_not_used_as_hook(self):
        row = {"name": "Acme", "website": "acme.com"}
        self.assertEqual(verified_context(row), "")
        prompt = build_draft_prompt("Acme", "acme.com", "good fit", verified_context(row))
        self.assertIn("NO_HOOK", prompt)

    def test_explicit_context_is_preserved(self):
        row = {"context": "Hiring a founding engineer", "job_post": "Remote role"}
        result = verified_context(row)
        self.assertIn("CONTEXT: Hiring a founding engineer", result)
        self.assertIn("JOB POST: Remote role", result)

    def test_unsubscribe_and_out_of_office_do_not_call_model(self):
        def fail(*args, **kwargs):
            raise AssertionError("model should not be called")

        self.assertEqual(classify("Please remove me", ask=fail), "unsubscribe")
        self.assertEqual(classify("I am out of office until Monday", ask=fail), "out_of_office")


if __name__ == "__main__":
    unittest.main()
