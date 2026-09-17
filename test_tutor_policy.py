"""Regression checks for policy boundaries. Does not import the service app.
These checks are provided for CI; they were not run locally for this change.
"""
import unittest
from tutor_policy import normalize_policy, allowed_tools, validate_artifact, policy_prompt


class TutorPolicyTests(unittest.TestCase):
    def test_safe_defaults_and_study_permission_dependency(self):
        policy = normalize_policy()
        self.assertTrue(all(policy["base"].values()))
        self.assertEqual(allowed_tools(policy), {})
        policy["toolkits"]["study"] = True
        policy["toolkits"]["files"] = True
        self.assertEqual(allowed_tools(policy), {})
        policy["base"]["no_study_materials"] = False
        self.assertIn("file", allowed_tools(policy))
        self.assertIn("worksheet", allowed_tools(policy))

    def test_disabled_tool_descriptions_are_not_in_prompt(self):
        policy = normalize_policy()
        self.assertNotIn("quiz:", policy_prompt(policy, []))
        policy["toolkits"]["practice"] = True
        self.assertIn("quiz:", policy_prompt(policy, []))
        self.assertNotIn("file:", policy_prompt(policy, []))

    def test_invented_source_and_invalid_answer_rejected(self):
        payload = {"items": [{"prompt": "A?", "answer": "B", "options": ["A", "B"], "source_ids": ["invented"]}]}
        with self.assertRaises(ValueError):
            validate_artifact("quiz", payload, {"S1"})
        payload["items"][0]["source_ids"] = ["S1"]
        payload["items"][0]["answer"] = "not an option"
        with self.assertRaises(ValueError):
            validate_artifact("quiz", payload, {"S1"})
        payload["items"][0]["answer"] = "B"
        self.assertEqual(validate_artifact("quiz", payload, {"S1"})["items"][0]["answer"], "B")

    def test_hint_cards_cannot_have_an_answer_field(self):
        payload = {"items": [{"prompt": "What have you tried?", "answer": "an answer", "source_ids": ["S1"]}]}
        self.assertEqual(validate_artifact("hints", payload, {"S1"})["items"][0]["answer"], "")


if __name__ == "__main__":
    unittest.main()
