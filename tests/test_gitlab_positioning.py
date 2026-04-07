from app.services.gitlab_positioning import GitLabDiffPositionResolver


def test_resolve_multiline_new_range_includes_top_level_end_line_and_line_range():
    resolver = GitLabDiffPositionResolver(
        base_sha="base",
        start_sha="start",
        head_sha="head",
        changes=[
            {
                "old_path": "README.md",
                "new_path": "README.md",
                "diff": "@@ -6,0 +6,9 @@\n"
                + "\n".join(f"+line {line}" for line in range(6, 15)),
            }
        ],
    )

    position = resolver.resolve(file_path="README.md", line_number=6, line_end=14)

    assert position == {
        "base_sha": "base",
        "start_sha": "start",
        "head_sha": "head",
        "position_type": "text",
        "old_path": "README.md",
        "new_path": "README.md",
        "new_line": 14,
        "line_range": {
            "start": {
                "line_code": "8ec9a00bfd09b3190ac6b22251dbb1aa95a0579d_6_6",
                "type": "new",
                "new_line": 6,
            },
            "end": {
                "line_code": "8ec9a00bfd09b3190ac6b22251dbb1aa95a0579d_6_14",
                "type": "new",
                "new_line": 14,
            },
        },
    }


def test_resolve_single_line_unchanged_position_keeps_old_and_new_lines():
    resolver = GitLabDiffPositionResolver(
        base_sha="base",
        start_sha="start",
        head_sha="head",
        changes=[
            {
                "old_path": "app/example.py",
                "new_path": "app/example.py",
                "diff": "@@ -10,3 +10,3 @@\n unchanged\n-old\n+new\n unchanged2",
            }
        ],
    )

    position = resolver.resolve(file_path="app/example.py", line_number=10, line_end=10)

    assert position == {
        "base_sha": "base",
        "start_sha": "start",
        "head_sha": "head",
        "position_type": "text",
        "old_path": "app/example.py",
        "new_path": "app/example.py",
        "old_line": 10,
        "new_line": 10,
    }
