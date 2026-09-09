"""Unit tests for SWEBench patch exclusion patterns."""

from __future__ import annotations

import pytest
from acb.benchmarks.swebench import SWEBench


class TestFilterExcludedPaths:
    """Test the _filter_excluded_paths method."""

    def test_empty_patterns_returns_all_paths(self):
        """With no patterns configured, all paths should be returned."""
        bench = SWEBench(config={"patch_exclude_patterns": []})
        paths = ["file1.py", "file2.py", ".goosehints"]
        result = bench._filter_excluded_paths(paths)
        assert result == paths

    def test_no_patterns_config_returns_all_paths(self):
        """When patterns key is missing from config, all paths should be returned."""
        bench = SWEBench(config={})
        paths = ["file1.py", "file2.py", ".goosehints"]
        result = bench._filter_excluded_paths(paths)
        assert result == paths

    def test_exact_file_match(self):
        """Exact filename matches should be excluded."""
        bench = SWEBench(config={"patch_exclude_patterns": [".goosehints"]})
        paths = ["file1.py", ".goosehints", "file2.py"]
        result = bench._filter_excluded_paths(paths)
        assert result == ["file1.py", "file2.py"]

    def test_multiple_exact_matches(self):
        """Multiple exact matches should all be excluded."""
        bench = SWEBench(config={"patch_exclude_patterns": [".goosehints", ".DS_Store"]})
        paths = ["file1.py", ".goosehints", ".DS_Store", "file2.py"]
        result = bench._filter_excluded_paths(paths)
        assert result == ["file1.py", "file2.py"]

    def test_wildcard_glob_pattern(self):
        """Glob patterns with wildcards should work."""
        bench = SWEBench(config={"patch_exclude_patterns": ["*.swp"]})
        paths = ["file1.py", "file.swp", "file.txt", ".file.py.swp"]
        result = bench._filter_excluded_paths(paths)
        assert result == ["file1.py", "file.txt"]

    def test_prefix_wildcard_pattern(self):
        """Prefix wildcard patterns should work."""
        bench = SWEBench(config={"patch_exclude_patterns": [".aider*"]})
        paths = ["file.py", ".aider_config", ".aider.log", ".aiderconfig"]
        result = bench._filter_excluded_paths(paths)
        assert result == ["file.py"]

    def test_directory_recursive_pattern(self):
        """Directory recursive patterns with /** should match the dir and all contents."""
        bench = SWEBench(config={"patch_exclude_patterns": [".rgctl/**"]})
        paths = [
            "file.py",
            ".rgctl",
            ".rgctl/index.db",
            ".rgctl/cache/data.json",
            ".rgctl/sub/dir/file.txt",
            "other/.rgctl/file.py",  # This should NOT match (not at root)
        ]
        result = bench._filter_excluded_paths(paths)
        # .rgctl itself matches the directory pattern, and all .rgctl/* subdirs match
        assert result == ["file.py", "other/.rgctl/file.py"]

    def test_directory_pattern_without_recursive(self):
        """Pattern without /** should only match the exact directory name."""
        bench = SWEBench(config={"patch_exclude_patterns": [".rgctl"]})
        paths = [
            "file.py",
            ".rgctl",
            ".rgctl/index.db",
            ".rgctl/cache/data.json",
        ]
        result = bench._filter_excluded_paths(paths)
        # Only exact match .rgctl is excluded, not its contents
        assert result == ["file.py", ".rgctl/index.db", ".rgctl/cache/data.json"]

    def test_complex_mixed_patterns(self):
        """Test combination of different pattern types."""
        bench = SWEBench(config={
            "patch_exclude_patterns": [
                ".goosehints",           # exact match
                "*.swp",                 # glob pattern
                ".rgctl/**",             # recursive directory
                ".aider*",               # prefix wildcard
            ]
        })
        paths = [
            "models.py",               # should keep
            "test.py",                 # should keep
            ".goosehints",             # excluded: exact
            "file.swp",                # excluded: glob
            ".aider_config",           # excluded: prefix
            ".rgctl",                  # excluded: directory
            ".rgctl/index.db",         # excluded: recursive
            ".rgctl/sub/file.json",    # excluded: recursive
            "docs/.rgctl/test",        # should keep (not at root)
        ]
        result = bench._filter_excluded_paths(paths)
        assert result == ["models.py", "test.py", "docs/.rgctl/test"]

    def test_preserves_path_order(self):
        """Filtering should preserve the order of remaining paths."""
        bench = SWEBench(config={"patch_exclude_patterns": ["*.swp"]})
        paths = ["z.py", "a.swp", "m.py", "b.swp", "c.py"]
        result = bench._filter_excluded_paths(paths)
        assert result == ["z.py", "m.py", "c.py"]

    def test_handles_empty_path_list(self):
        """Empty path list should return empty list."""
        bench = SWEBench(config={"patch_exclude_patterns": [".goosehints"]})
        result = bench._filter_excluded_paths([])
        assert result == []

    def test_swo_vim_swap_pattern(self):
        """Test matching vim .swo backup files."""
        bench = SWEBench(config={"patch_exclude_patterns": ["*.swo"]})
        paths = ["file.py", "editor.py.swo", ".test.swo"]
        result = bench._filter_excluded_paths(paths)
        assert result == ["file.py"]

    def test_cursor_pattern(self):
        """Test matching Cursor IDE artifacts."""
        bench = SWEBench(config={"patch_exclude_patterns": [".cursor*"]})
        paths = ["main.py", ".cursor_settings", ".cursorignore", "cursor.py"]
        result = bench._filter_excluded_paths(paths)
        assert result == ["main.py", "cursor.py"]

    def test_real_world_goose_scenario(self):
        """Realistic scenario: agent creates .goosehints and modifies code."""
        bench = SWEBench(config={
            "patch_exclude_patterns": [".goosehints", ".rgctl", ".rgctl/**"]
        })
        paths = [
            "requests/models.py",      # Real code changes - keep
            "tests/test_requests.py",  # Real test changes - keep
            ".goosehints",             # Skill hints - exclude
            ".rgctl/index.db",         # Tool artifacts - exclude
            ".rgctl/cache/queries.db", # Tool artifacts - exclude
        ]
        result = bench._filter_excluded_paths(paths)
        assert result == ["requests/models.py", "tests/test_requests.py"]
