from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

_HUNK_RE = re.compile(r"^@@ -(?P<old>\d+)(?:,\d+)? \+(?P<new>\d+)(?:,\d+)? @@")


@dataclass(frozen=True)
class DiffLinePosition:
    old_line: int | None
    new_line: int | None
    line_code: str
    range_type: str


@dataclass(frozen=True)
class DiffFile:
    old_path: str
    new_path: str
    by_old_line: dict[int, DiffLinePosition]
    by_new_line: dict[int, DiffLinePosition]


class GitLabDiffPositionResolver:
    def __init__(
        self,
        *,
        base_sha: str | None,
        start_sha: str | None,
        head_sha: str | None,
        changes: list[dict],
    ):
        self._base_sha = base_sha
        self._start_sha = start_sha
        self._head_sha = head_sha
        self._files = self._parse_changes(changes)

    def can_resolve(self) -> bool:
        return bool(
            self._base_sha and self._start_sha and self._head_sha and self._files
        )

    def resolve(
        self,
        *,
        file_path: str | None,
        line_number: int | None,
        line_end: int | None = None,
    ) -> dict | None:
        if not self.can_resolve() or not file_path or line_number is None:
            return None

        end_line = line_end or line_number
        if end_line < line_number:
            line_number, end_line = end_line, line_number

        for diff_file in self._files.get(file_path, []):
            start_pos, end_pos = self._resolve_line_range(
                diff_file, line_number, end_line
            )
            if not start_pos or not end_pos:
                continue

            position: dict = {
                "base_sha": self._base_sha,
                "start_sha": self._start_sha,
                "head_sha": self._head_sha,
                "position_type": "text",
                "old_path": diff_file.old_path,
                "new_path": diff_file.new_path,
            }

            # Single line (requested as single, or range collapsed to one diff line)
            if start_pos.line_code == end_pos.line_code:
                if start_pos.old_line is not None:
                    position["old_line"] = start_pos.old_line
                if start_pos.new_line is not None:
                    position["new_line"] = start_pos.new_line
                return position

            # Multiline range
            if end_pos.old_line is not None:
                position["old_line"] = end_pos.old_line
            if end_pos.new_line is not None:
                position["new_line"] = end_pos.new_line
            position["line_range"] = {
                "start": self._serialize_range_endpoint(start_pos),
                "end": self._serialize_range_endpoint(end_pos),
            }
            return position

        return None

    def _resolve_line_range(
        self, diff_file: DiffFile, start_line: int, end_line: int
    ) -> tuple[DiffLinePosition | None, DiffLinePosition | None]:
        """
        Find positions for a line range within a diff file.

        Strategy (in order of preference):
        1. Exact match for both endpoints on the new side (added/context lines).
        2. Exact match for both endpoints on the old side (removed/context lines).
        3. Scan all new-side lines within [start_line, end_line] and use the
           outermost available lines as endpoints — handles cases where the LLM
           references a range whose exact boundaries fall outside the hunk context.
        4. Same scan on the old side (covers comments about deleted code).
        """
        # 1. Exact match, new side
        s = diff_file.by_new_line.get(start_line)
        e = diff_file.by_new_line.get(end_line)
        if s and e:
            return s, e

        # 2. Exact match, old side (deleted lines)
        s = diff_file.by_old_line.get(start_line)
        e = diff_file.by_old_line.get(end_line)
        if s and e:
            return s, e

        # 3. Scan within range, new side
        new_hits = sorted(
            ln for ln in diff_file.by_new_line if start_line <= ln <= end_line
        )
        if new_hits:
            return (
                diff_file.by_new_line[new_hits[0]],
                diff_file.by_new_line[new_hits[-1]],
            )

        # 4. Scan within range, old side
        old_hits = sorted(
            ln for ln in diff_file.by_old_line if start_line <= ln <= end_line
        )
        if old_hits:
            return (
                diff_file.by_old_line[old_hits[0]],
                diff_file.by_old_line[old_hits[-1]],
            )

        return None, None

    @staticmethod
    def _serialize_range_endpoint(position: DiffLinePosition) -> dict:
        return {
            "line_code": position.line_code,
            "type": position.range_type,
        }

    @classmethod
    def _parse_changes(cls, changes: list[dict]) -> dict[str, list[DiffFile]]:
        parsed: dict[str, list[DiffFile]] = {}
        for change in changes:
            diff_file = cls._parse_change(change)
            if not diff_file:
                continue
            for path in {diff_file.old_path, diff_file.new_path}:
                if not path:
                    continue
                parsed.setdefault(path, []).append(diff_file)
        return parsed

    @classmethod
    def _parse_change(cls, change: dict) -> DiffFile | None:
        old_path = change.get("old_path") or change.get("new_path") or ""
        new_path = change.get("new_path") or change.get("old_path") or ""
        diff_text = change.get("diff") or ""
        code_path = new_path or old_path
        if not code_path or not diff_text:
            return None

        by_old_line: dict[int, DiffLinePosition] = {}
        by_new_line: dict[int, DiffLinePosition] = {}

        old_cursor: int | None = None
        new_cursor: int | None = None

        for raw_line in diff_text.splitlines():
            match = _HUNK_RE.match(raw_line)
            if match:
                old_cursor = int(match.group("old"))
                new_cursor = int(match.group("new"))
                continue

            if old_cursor is None or new_cursor is None:
                continue
            if raw_line.startswith("\\"):
                continue

            prefix = raw_line[:1]
            line_code = cls._build_line_code(code_path, old_cursor, new_cursor)

            if prefix == "+":
                by_new_line[new_cursor] = DiffLinePosition(
                    old_line=None,
                    new_line=new_cursor,
                    line_code=line_code,
                    range_type="new",
                )
                new_cursor += 1
                continue

            if prefix == "-":
                by_old_line[old_cursor] = DiffLinePosition(
                    old_line=old_cursor,
                    new_line=None,
                    line_code=line_code,
                    range_type="old",
                )
                old_cursor += 1
                continue

            # Context line — exists on both sides with matching type per side
            by_old_line[old_cursor] = DiffLinePosition(
                old_line=old_cursor,
                new_line=new_cursor,
                line_code=line_code,
                range_type="old",
            )
            by_new_line[new_cursor] = DiffLinePosition(
                old_line=old_cursor,
                new_line=new_cursor,
                line_code=line_code,
                range_type="new",
            )
            old_cursor += 1
            new_cursor += 1

        return DiffFile(
            old_path=old_path,
            new_path=new_path,
            by_old_line=by_old_line,
            by_new_line=by_new_line,
        )

    @staticmethod
    def _build_line_code(path: str, old_line: int, new_line: int) -> str:
        digest = hashlib.sha1(
            path.encode("utf-8"), usedforsecurity=False
        ).hexdigest()
        return f"{digest}_{old_line}_{new_line}"
