import unittest
from unittest.mock import patch

import bump_version


class BumpVersionTests(unittest.TestCase):
    def test_parse_args_supports_commit_before_tag_flow(self):
        bump_type, create_tag, commit, message, allow_non_main = bump_version.parse_args([
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
        self.assertFalse(allow_non_main)

    def test_parse_args_supports_explicit_non_main_release_override(self):
        _bump_type, _create_tag, _commit, _message, allow_non_main = bump_version.parse_args([
            "--tag",
            "--allow-non-main-release",
        ])

        self.assertTrue(allow_non_main)

    def test_release_tag_rejects_feature_branch_without_override(self):
        with patch.object(bump_version, "current_branch", return_value="feature/demo"):
            with self.assertRaises(RuntimeError):
                bump_version.ensure_release_branch_allowed()

    def test_release_tag_allows_main_branch(self):
        with patch.object(bump_version, "current_branch", return_value="main"):
            bump_version.ensure_release_branch_allowed()

    def test_release_tag_requires_head_to_be_in_origin_main(self):
        calls = []

        def fake_run_git(args):
            calls.append(args)
            if args == ['merge-base', '--is-ancestor', 'HEAD', 'origin/main']:
                return type("Result", (), {"returncode": 1, "stderr": ""})()
            raise AssertionError(f"Unexpected git command: {args}")

        with patch.object(bump_version, "run_git", side_effect=fake_run_git):
            with self.assertRaises(RuntimeError):
                bump_version.ensure_release_commit_published()

        self.assertEqual(calls, [['merge-base', '--is-ancestor', 'HEAD', 'origin/main']])

    def test_tag_request_auto_commits_before_tagging(self):
        calls = []

        with patch.object(bump_version, "read_version", return_value="1.2.3"), \
                patch.object(bump_version, "write_version") as write_version, \
                patch.object(bump_version, "update_changelog") as update_changelog, \
                patch.object(bump_version, "commit_release", side_effect=lambda version: calls.append(("commit", version))), \
                patch.object(bump_version, "create_git_tag", side_effect=lambda version, allow_non_main=False: calls.append(("tag", version, allow_non_main))):
            result = bump_version.main(["patch", "--tag"])

        self.assertEqual(result, 0)
        write_version.assert_called_once_with("1.2.4")
        update_changelog.assert_called_once_with("1.2.4", None)
        self.assertEqual(calls, [("commit", "1.2.4"), ("tag", "1.2.4", False)])


if __name__ == "__main__":
    unittest.main()
