import unittest
from unittest.mock import patch

import bump_version


class BumpVersionTests(unittest.TestCase):
    def test_parse_args_supports_commit_before_tag_flow(self):
        bump_type, create_tag, commit, message = bump_version.parse_args([
            "minor",
            "--commit",
            "--tag",
            "--message",
            "Polish release",
        ])

        self.assertEqual(bump_type, "minor")
        self.assertTrue(create_tag)
        self.assertTrue(commit)
        self.assertEqual(message, "Polish release")

    def test_tag_request_auto_commits_before_tagging(self):
        calls = []

        with patch.object(bump_version, "read_version", return_value="1.2.3"), \
                patch.object(bump_version, "write_version") as write_version, \
                patch.object(bump_version, "update_changelog") as update_changelog, \
                patch.object(bump_version, "commit_release", side_effect=lambda version: calls.append(("commit", version))), \
                patch.object(bump_version, "create_git_tag", side_effect=lambda version: calls.append(("tag", version))):
            result = bump_version.main(["patch", "--tag"])

        self.assertEqual(result, 0)
        write_version.assert_called_once_with("1.2.4")
        update_changelog.assert_called_once_with("1.2.4", None)
        self.assertEqual(calls, [("commit", "1.2.4"), ("tag", "1.2.4")])


if __name__ == "__main__":
    unittest.main()

