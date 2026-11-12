"""Checks for the independent stored-Markdown validator."""
import unittest

from audit.audit_dataset import norm, remove_chrome, strict_body, verify_raw_chrome


class ValidationTests(unittest.TestCase):
    def test_expected_chrome_and_following_body(self):
        text = ("# Article\nBody\n\n## Site Navigation\n\nHome | Help\n\n"
                "## Footer\n\n© 2024 Example. | Privacy\n\n## Details\nKeep 1200 dollars")
        clean, blocks = verify_raw_chrome(text)
        self.assertEqual(len(blocks), 2)
        self.assertEqual(strict_body(clean), "# Article\nBody\n## Details\nKeep 1200 dollars")
        self.assertEqual(norm(clean), norm(remove_chrome(text)))

    def test_suspicious_chrome_body_is_preserved(self):
        text = "## Footer\n\nThe laptop costs 1200 dollars\n"
        self.assertEqual(verify_raw_chrome(text), (text, []))

    def test_following_unheaded_content_is_preserved(self):
        text = "## Footer\n\n© 2024 Example. | Privacy\n\nCritical content costs 1200 dollars"
        clean, _ = verify_raw_chrome(text)
        self.assertIn("Critical content costs 1200 dollars", clean)
        self.assertNotIn("Critical content", remove_chrome(text))

    def test_strict_comparison_exposes_normalization_collisions(self):
        for left, right in [("Case", "case"), ("A  B", "A B"), ("A\nB", "A B")]:
            self.assertEqual(norm(left), norm(right))
            self.assertNotEqual(strict_body(left), strict_body(right))

    def test_numeric_changes_remain_visible(self):
        self.assertNotEqual(strict_body("Costs 1200"), strict_body("Costs 120"))


if __name__ == "__main__":
    unittest.main()
