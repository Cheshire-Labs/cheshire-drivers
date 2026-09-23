"""Read source as text for the source-level CI guards in every Cheshire repo.

Parsing source into a syntax tree and walking it is banned everywhere in this
codebase except `tests/` and `bench/`, so every structural rule the guards
enforce is expressed as a pattern over code text instead. This module supplies
the two things that make that workable: string and comment bodies blanked out,
and bracket continuations joined so a call split over several lines still reads
as one line.

It is its own top-level package rather than a module inside `cheshire_drivers`
so that importing it costs nothing: the guards are stdlib-only and must not drag
in the driver stack, and through it pylabrobot, just to read a file. It ships
from the cheshire-drivers distribution because every other repo already depends
on that, and a per-repo copy would let the guards drift into disagreeing about
what a logical line is.
"""

import re
from dataclasses import dataclass
from pathlib import Path

_TRIPLE_QUOTES = ('"""', "'''")
_OPENERS = "([{"
_CLOSERS = ")]}"


def blank_strings_and_comments(source: str, keep_strings: bool = False) -> str:
    """`source` with comment bodies and string contents replaced by spaces.

    Quote characters and line breaks stay put, so `x == ""` survives as an
    empty-string comparison while `x == "abc"` becomes `x == "   "`. Blanking is
    what stops a docstring quoting a banned pattern from reading as the pattern.

    `keep_strings` blanks only the comments, for a rule about what a literal
    says (`importlib.import_module("pylabrobot")`). Quotes are still tracked
    either way, so a `#` inside a string never reads as a comment.
    """
    out = list(source)
    i = 0
    end = len(source)
    while i < end:
        char = source[i]
        if char == "#":
            while i < end and source[i] != "\n":
                out[i] = " "
                i += 1
            continue
        if char not in "\"'":
            i += 1
            continue
        quote = source[i:i + 3] if source[i:i + 3] in _TRIPLE_QUOTES else char
        i += len(quote)
        while i < end:
            if source[i] == "\\":
                if not keep_strings:
                    out[i] = " "
                    if i + 1 < end and source[i + 1] != "\n":
                        out[i + 1] = " "
                i += 2
                continue
            if source.startswith(quote, i):
                i += len(quote)
                break
            if source[i] != "\n" and not keep_strings:
                out[i] = " "
            i += 1
    return "".join(out)


@dataclass(frozen=True)
class CodeLine:
    """One logical line of code: no strings, no comments, continuations joined."""

    lineno: int
    indent: int
    text: str


def code_lines(source: str, keep_strings: bool = False) -> list[CodeLine]:
    """Split `source` into logical lines with strings and comments blanked.

    A statement spread over several physical lines by brackets or a trailing
    backslash comes back as a single `CodeLine` carrying the line number and
    indent of its first physical line. `keep_strings` leaves literal contents
    intact for rules about what a literal says.
    """
    lines: list[CodeLine] = []
    parts: list[str] = []
    start = 0
    indent = 0
    depth = 0

    kept = blank_strings_and_comments(source, keep_strings).splitlines()
    # Brackets are always counted on the fully-blanked text: a `(` inside a
    # kept literal is not a real open bracket and would swallow later lines.
    bare = blank_strings_and_comments(source).splitlines()
    for lineno, (raw, counted) in enumerate(zip(kept, bare), start=1):
        stripped = raw.strip()
        if not parts:
            if not stripped:
                continue
            start = lineno
            indent = len(raw) - len(raw.lstrip())
        continued = stripped.endswith("\\")
        parts.append(stripped.rstrip("\\").strip())
        depth += sum(counted.count(c) for c in _OPENERS)
        depth -= sum(counted.count(c) for c in _CLOSERS)
        if depth <= 0 and not continued:
            depth = 0
            lines.append(CodeLine(start, indent, " ".join(p for p in parts if p)))
            parts = []

    if parts:
        lines.append(CodeLine(start, indent, " ".join(p for p in parts if p)))
    return lines


def code_lines_of(path: Path, keep_strings: bool = False) -> list[CodeLine]:
    """`code_lines` for a file on disk."""
    return code_lines(path.read_text(encoding="utf-8"), keep_strings)


_NOT_REPO_CODE = frozenset({"__pycache__", "venv", "node_modules", "build", "dist", "site-packages"})


def _is_nested_checkout(directory: Path, root: Path) -> bool:
    """A directory under `root` that is its own git repo, so its code is not ours."""
    while directory != root and directory.parent != directory:
        if (directory / ".git").exists():
            return True
        directory = directory.parent
    return False


def python_files(root: Path) -> list[Path]:
    """Every .py file under `root` that is this repo's own code.

    Dot-directories are excluded wholesale. They hold tooling and runtime
    state, not repo code: environments, caches, the agent worktrees under
    `.claude` (whole copies of the repo at other revisions), and a service's
    own generated data. Scanning those would make the file list depend on
    what a previous run happened to leave behind.

    So are nested checkouts. CI clones the sibling repos into this one's
    working directory, and their files are theirs to police: a guard here
    failing on a line another repo owns cannot be fixed from this one, and
    it would go red for a change nobody made here.
    """
    nested: dict[Path, bool] = {}

    def under_a_sibling(path: Path) -> bool:
        parent = path.parent
        if parent not in nested:
            nested[parent] = _is_nested_checkout(parent, root)
        return nested[parent]

    return sorted(
        path for path in root.rglob("*.py")
        if not any(part.startswith(".") for part in path.parts)
        and not _NOT_REPO_CODE & set(path.parts)
        and not under_a_sibling(path)
    )


_FROM_IMPORT = re.compile(r"^from\s+(\.*[\w.]*)\s+import\b")
_PLAIN_IMPORT = re.compile(r"^import\s+(.+)$")
_IMPORT_TARGET = re.compile(r"^([\w.]+)(?:\s+as\s+\w+)?$")


@dataclass(frozen=True)
class ImportedModule:
    """One module named by an import statement."""

    lineno: int
    module: str
    is_from: bool

    def rendered(self) -> str:
        return (
            f"line {self.lineno}: from {self.module} import ..."
            if self.is_from
            else f"line {self.lineno}: import {self.module}"
        )


def imported_modules(lines: list[CodeLine]) -> list[ImportedModule]:
    """Every module named by an import statement in `lines`.

    `from x import y` yields `x`; `import a, b.c as d` yields `a` and `b.c`.
    """
    found: list[ImportedModule] = []
    for line in lines:
        from_import = _FROM_IMPORT.match(line.text)
        if from_import:
            found.append(ImportedModule(line.lineno, from_import.group(1), True))
            continue
        plain = _PLAIN_IMPORT.match(line.text)
        if not plain:
            continue
        for target in plain.group(1).split(","):
            named = _IMPORT_TARGET.match(target.strip())
            if named:
                found.append(ImportedModule(line.lineno, named.group(1), False))
    return found


def imports_rooted_at(lines: list[CodeLine], roots: frozenset[str]) -> list[ImportedModule]:
    """The imports whose top-level package is one of `roots`."""
    return [
        found for found in imported_modules(lines)
        if found.module.split(".", 1)[0] in roots
    ]
