from __future__ import annotations

from codey.saas.build_mode.decomposer import TaskDecomposer


def test_decompose_returns_empty_for_non_mapping_file_tree() -> None:
    decomposer = TaskDecomposer()

    assert decomposer.decompose({"file_tree": ["not", "a", "mapping"]}) == []


def test_decompose_treats_string_phase_files_as_single_path() -> None:
    decomposer = TaskDecomposer()

    tasks = decomposer.decompose(
        {
            "file_tree": {"app/main.py": "service"},
            "phases": [{"files": "app/main.py"}],
        }
    )

    assert len(tasks) == 1
    assert tasks[0].file_path == "app/main.py"
    assert tasks[0].phase == 0


def test_decompose_keeps_first_phase_for_duplicate_normalized_paths() -> None:
    decomposer = TaskDecomposer()

    tasks = decomposer.decompose(
        {
            "file_tree": {"app/main.py": "service"},
            "phases": [
                {"files": ["./app//main.py"]},
                {"files": ["app/main.py"]},
            ],
        }
    )

    assert len(tasks) == 1
    assert tasks[0].file_path == "app/main.py"
    assert tasks[0].phase == 0


def test_decompose_rejects_unsafe_file_paths() -> None:
    decomposer = TaskDecomposer()

    tasks = decomposer.decompose(
        {
            "file_tree": {
                "/tmp/secret.py": "service",
                "../secret.py": "service",
                "C:\\tmp\\secret.py": "service",
                "bad\x00name.py": "service",
                "./app//main.py": "service",
                "app\\models\\.\\user.py": "model",
                "app/../escape.py": "service",
            },
            "phases": [
                {"files": ["../secret.py", "app\\models\\.\\user.py"]},
                {"files": "app\\main.py"},
            ],
        }
    )

    task_by_path = {task.file_path: task for task in tasks}

    assert set(task_by_path) == {"app/main.py", "app/models/user.py"}
    assert task_by_path["app/models/user.py"].phase == 0
    assert task_by_path["app/main.py"].phase == 1
