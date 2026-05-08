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

    def test_create_git_tag_reuses_existing_local_tag_on_same_commit(self):
        head_sha = "abc123"

        def fake_run_git(args):
            if args == ['merge-base', '--is-ancestor', 'HEAD', 'origin/main']:
                return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()
            if args == ['tag', '-l', 'v1.2.4']:
                return type("Result", (), {"returncode": 0, "stdout": "v1.2.4\n", "stderr": ""})()
            if args == ['rev-parse', 'v1.2.4^{}']:
                return type("Result", (), {"returncode": 0, "stdout": head_sha + "\n", "stderr": ""})()
            if args == ['rev-parse', 'HEAD']:
                return type("Result", (), {"returncode": 0, "stdout": head_sha + "\n", "stderr": ""})()
            raise AssertionError(f"Unexpected git command: {args}")

        with patch.object(bump_version, "current_branch", return_value="main"), \
                patch.object(bump_version, "run_git", side_effect=fake_run_git):
            tag_name = bump_version.create_git_tag("1.2.4")

        self.assertEqual(tag_name, "v1.2.4")

    def test_create_git_tag_pushes_release_commit_before_tag_when_requested(self):
        calls = []

        def fake_run_git(args):
            calls.append(args)
            if args == ['merge-base', '--is-ancestor', 'HEAD', 'origin/main']:
                return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()
            if args == ['tag', '-l', 'v1.2.4']:
                return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()
            if args == ['tag', '-a', 'v1.2.4', '-m', 'Release v1.2.4']:
                return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()
            if args == ['push', 'origin', 'HEAD:main']:
                return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()
            if args == ['fetch', 'origin', 'main']:
                return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()
            if args == ['push', 'origin', 'v1.2.4']:
                return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()
            raise AssertionError(f"Unexpected git command: {args}")

        with patch.object(bump_version, "read_version", return_value="1.2.3"), \
                patch.object(bump_version, "write_version"), \
                patch.object(bump_version, "update_changelog"), \
                patch.object(bump_version, "commit_release", return_value=True), \
                patch.object(bump_version, "current_branch", return_value="main"), \
                patch.object(bump_version, "run_git", side_effect=fake_run_git):
            result = bump_version.main(["patch", "--tag"])

        self.assertEqual(result, 0)
        self.assertEqual(
            calls,
            [
                ['push', 'origin', 'HEAD:main'],
                ['fetch', 'origin', 'main'],
                ['merge-base', '--is-ancestor', 'HEAD', 'origin/main'],
                ['tag', '-l', 'v1.2.4'],
                ['tag', '-a', 'v1.2.4', '-m', 'Release v1.2.4'],
                ['push', 'origin', 'v1.2.4'],
            ],
        )

    def test_main_stops_when_release_commit_push_fails(self):
        calls = []

        def fake_run_git(args):
            calls.append(args)
            if args == ['push', 'origin', 'HEAD:main']:
                return type("Result", (), {"returncode": 1, "stdout": "", "stderr": "push failed"})()
            raise AssertionError(f"Unexpected git command: {args}")

        with patch.object(bump_version, "read_version", return_value="1.2.3"), \
                patch.object(bump_version, "write_version"), \
                patch.object(bump_version, "update_changelog"), \
                patch.object(bump_version, "commit_release", return_value=True), \
                patch.object(bump_version, "run_git", side_effect=fake_run_git):
            result = bump_version.main(["patch", "--tag"])

        self.assertEqual(result, 1)
        self.assertEqual(calls, [['push', 'origin', 'HEAD:main']])

    def test_main_refreshes_origin_main_before_tagging(self):
        calls = []

        def fake_run_git(args):
            calls.append(args)
            if args == ['push', 'origin', 'HEAD:main']:
                return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()
            if args == ['fetch', 'origin', 'main']:
                return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()
            if args == ['merge-base', '--is-ancestor', 'HEAD', 'origin/main']:
                return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()
            if args == ['tag', '-l', 'v1.2.4']:
                return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()
            if args == ['rev-parse', 'v1.2.4^{}']:
                return type("Result", (), {"returncode": 0, "stdout": "abc123\n", "stderr": ""})()
            if args == ['rev-parse', 'HEAD']:
                return type("Result", (), {"returncode": 0, "stdout": "abc123\n", "stderr": ""})()
            if args == ['tag', '-a', 'v1.2.4', '-m', 'Release v1.2.4']:
                return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()
            if args == ['push', 'origin', 'v1.2.4']:
                return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()
            raise AssertionError(f"Unexpected git command: {args}")

        with patch.object(bump_version, "read_version", return_value="1.2.3"), \
                patch.object(bump_version, "write_version"), \
                patch.object(bump_version, "update_changelog"), \
                patch.object(bump_version, "commit_release", return_value=True), \
                patch.object(bump_version, "current_branch", return_value="main"), \
                patch.object(bump_version, "run_git", side_effect=fake_run_git):
            result = bump_version.main(["patch", "--tag"])

        self.assertEqual(result, 0)
        self.assertEqual(calls[0:2], [['push', 'origin', 'HEAD:main'], ['fetch', 'origin', 'main']])

    def test_tag_request_auto_commits_before_tagging(self):
        calls = []

        with patch.object(bump_version, "read_version", return_value="1.2.3"), \
                patch.object(bump_version, "write_version") as write_version, \
                patch.object(bump_version, "update_changelog") as update_changelog, \
                patch.object(bump_version, "commit_release", side_effect=lambda version: calls.append(("commit", version))), \
                patch.object(bump_version, "push_release_commit_to_main", side_effect=lambda: calls.append(("push-main",))), \
                patch.object(bump_version, "refresh_origin_main", side_effect=lambda: calls.append(("refresh",))), \
                patch.object(bump_version, "create_git_tag", side_effect=lambda version, allow_non_main=False: calls.append(("tag", version, allow_non_main)) or "v1.2.4"), \
                patch.object(bump_version, "push_git_tag", side_effect=lambda tag_name: calls.append(("push-tag", tag_name))):
            result = bump_version.main(["patch", "--tag"])

        self.assertEqual(result, 0)
        write_version.assert_called_once_with("1.2.4")
        update_changelog.assert_called_once_with("1.2.4", None)
        self.assertEqual(
            calls,
            [
                ("commit", "1.2.4"),
                ("push-main",),
                ("refresh",),
                ("tag", "1.2.4", False),
                ("push-tag", "v1.2.4"),
            ],
        )


if __name__ == "__main__":
    unittest.main()
