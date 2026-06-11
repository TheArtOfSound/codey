from __future__ import annotations

from codey.saas.build_mode.path_utils import (
    MAX_PLAN_FILE_PATH_CHARS,
    normalize_plan_file_path,
)


def test_normalize_plan_file_path_unwraps_common_model_quotes() -> None:
    assert normalize_plan_file_path("`src/app.py`") == "src/app.py"
    assert normalize_plan_file_path("'src/app.py'") == "src/app.py"
    assert normalize_plan_file_path('"src/app.py"') == "src/app.py"


def test_normalize_plan_file_path_accepts_model_limit_length_paths() -> None:
    leaf = "a" * (MAX_PLAN_FILE_PATH_CHARS - len("src/.py"))

    assert normalize_plan_file_path(f"src/{leaf}.py") == f"src/{leaf}.py"


def test_normalize_plan_file_path_rejects_paths_over_model_limit() -> None:
    leaf = "a" * (MAX_PLAN_FILE_PATH_CHARS - len("src/.py") + 1)

    assert normalize_plan_file_path(f"src/{leaf}.py") is None


def test_normalize_plan_file_path_rejects_url_query_and_fragment_delimiters() -> None:
    assert normalize_plan_file_path("src/app.py?access_token=secret") is None
    assert normalize_plan_file_path("`src/app.py#client_secret=secret`") is None
