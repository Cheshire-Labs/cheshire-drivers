"""Tests for the text reader every Cheshire repo's source-level CI guards use.

The guards themselves pass vacuously on a clean tree, so a broken reader would
show up as a guard that quietly stops catching anything, here and in every repo
that imports this module. These pin the reader directly: what blanking
preserves, what it hides, and where a logical line starts and ends.
"""

import subprocess
import sys
from pathlib import Path

import pytest

from cheshire_source_text import (
    blank_strings_and_comments,
    code_lines,
    imported_modules,
    imports_rooted_at,
    python_files,
)


class TestBlanking:
    def test_string_contents_go_but_the_quotes_stay(self) -> None:
        """`x == ""` must stay distinguishable from `x == "abc"`, which is how
        the identifier guard tells an empty-string cheat from any literal."""
        assert blank_strings_and_comments('x = "abc"') == 'x = "   "'
        assert blank_strings_and_comments('x = ""') == 'x = ""'

    def test_comment_bodies_go(self) -> None:
        assert blank_strings_and_comments("x = 1  # import ast").rstrip() == "x = 1"

    def test_a_hash_inside_a_string_is_not_a_comment(self) -> None:
        assert blank_strings_and_comments("x = '#a' + y") == "x = '  ' + y"

    def test_a_quote_inside_a_comment_does_not_open_a_string(self) -> None:
        source = "# don't\nimport ast\n"
        assert "import ast" in blank_strings_and_comments(source)

    def test_triple_quoted_bodies_go_but_line_numbers_survive(self) -> None:
        source = '"""\nimport ast\n"""\nx = 1\n'
        blanked = blank_strings_and_comments(source)
        assert "import ast" not in blanked
        assert blanked.splitlines()[3] == "x = 1"

    def test_keep_strings_leaves_literals_alone(self) -> None:
        source = 'import_module("pylabrobot")  # note'
        assert blank_strings_and_comments(source, keep_strings=True).rstrip() == (
            'import_module("pylabrobot")'
        )


class TestLogicalLines:
    def test_a_bracketed_call_becomes_one_line(self) -> None:
        source = "wait_for(\n    thing,\n    timeout=30,\n)\n"
        lines = code_lines(source)
        assert [(line.lineno, line.text) for line in lines] == [
            (1, "wait_for( thing, timeout=30, )")
        ]

    def test_a_backslash_continuation_becomes_one_line(self) -> None:
        lines = code_lines("x = 1 + \\\n    2\n")
        assert [line.text for line in lines] == ["x = 1 + 2"]

    def test_indent_is_the_first_physical_lines_indent(self) -> None:
        lines = code_lines("def f():\n    if x:\n        return 1\n")
        assert [(line.indent, line.text) for line in lines] == [
            (0, "def f():"),
            (4, "if x:"),
            (8, "return 1"),
        ]

    def test_a_bracket_inside_a_kept_literal_does_not_swallow_the_next_line(self) -> None:
        """With `keep_strings`, a `(` in a literal is not a real open bracket.
        Counting it would merge every following line into one."""
        lines = code_lines('x = "("\ny = 2\n', keep_strings=True)
        assert [line.text for line in lines] == ['x = "("', "y = 2"]

    def test_blank_lines_are_dropped(self) -> None:
        assert [line.lineno for line in code_lines("x = 1\n\n\ny = 2\n")] == [1, 4]


class TestImports:
    @pytest.mark.parametrize(
        "source,expected",
        [
            ("import ast", ["ast"]),
            ("import ast, re", ["ast", "re"]),
            ("import ast as syntax", ["ast"]),
            ("import a.b.c", ["a.b.c"]),
            ("from ast import parse", ["ast"]),
            ("from . import thing", ["."]),
            ("from .sibling import thing", [".sibling"]),
            (
                "from orca.sdk import (\n    Plate,\n    Tip,\n)",
                ["orca.sdk"],
            ),
        ],
    )
    def test_every_import_shape_is_read(self, source: str, expected: list[str]) -> None:
        assert [found.module for found in imported_modules(code_lines(source))] == expected

    @pytest.mark.parametrize(
        "source",
        ["x = 'import ast'", "# import ast", "importlib.import_module('ast')"],
    )
    def test_non_imports_are_not_read_as_imports(self, source: str) -> None:
        assert imported_modules(code_lines(source)) == []

    def test_root_matching_does_not_match_a_longer_package(self) -> None:
        lines = code_lines("import pylabrobotics\nimport pylabrobot.resources\n")
        found = imports_rooted_at(lines, frozenset({"pylabrobot"}))
        assert [f.module for f in found] == ["pylabrobot.resources"]

    def test_rendered_says_which_form_it_was(self) -> None:
        lines = code_lines("import ast\nfrom ast import parse\n")
        assert [f.rendered() for f in imported_modules(lines)] == [
            "line 1: import ast",
            "line 2: from ast import ...",
        ]


class TestWhatCountsAsThisRepo:
    def test_a_nested_checkout_is_not_this_repo_s_code(self, tmp_path: Path) -> None:
        '''CI clones the sibling repos into this one's working directory.

        Their files are theirs to police. Scanning them makes this repo's
        guards fail on a line nobody here can change, for a commit nobody
        here made.
        '''
        (tmp_path / '.git').mkdir()
        (tmp_path / 'ours.py').write_text('x = 1', encoding='utf-8')
        sibling = tmp_path / 'cheshire-drivers'
        (sibling / '.git').mkdir(parents=True)
        (sibling / 'src').mkdir()
        (sibling / 'src' / 'theirs.py').write_text('import ast', encoding='utf-8')

        found = [path.name for path in python_files(tmp_path)]

        assert found == ['ours.py']

    def test_a_plain_subdirectory_is_still_scanned(self, tmp_path: Path) -> None:
        '''Negative control: a checkout is skipped, not every subdirectory.'''
        (tmp_path / 'pkg').mkdir()
        (tmp_path / 'pkg' / 'deep.py').write_text('x = 1', encoding='utf-8')

        assert [path.name for path in python_files(tmp_path)] == ['deep.py']


class TestImportCost:
    def test_reading_source_does_not_import_the_driver_stack(self) -> None:
        """Every repo's CI guards import this, so a driver import here would put
        pylabrobot resolving between them and any result."""
        probe = (
            "import sys, cheshire_source_text; "
            "print(sorted(m for m in sys.modules "
            "if m.split('.')[0] in {'pylabrobot', 'cheshire_drivers'}))"
        )
        result = subprocess.run(
            [sys.executable, "-c", probe], capture_output=True, text=True, check=True
        )

        assert result.stdout.strip() == "[]", f"pulled in: {result.stdout.strip()}"
