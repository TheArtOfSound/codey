from pathlib import Path


def _dependency_name(requirement: str) -> str:
    name = requirement
    for separator in ("==", ">=", "<=", "~=", "!=", ">", "<"):
        if separator in name:
            name = name.split(separator, 1)[0]
            break

    return name.split("[", 1)[0].strip().lower().replace("_", "-")


def _requirement_names_from(path: str) -> set[str]:
    names: set[str] = set()
    for raw_line in Path(path).read_text(encoding="utf-8").splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith(("-r ", "--requirement ")):
            continue

        name = _dependency_name(line)
        if name:
            names.add(name)

    return names


def _requirement_names() -> set[str]:
    return _requirement_names_from("requirements.txt")


def _pyproject_dependency_names() -> set[str]:
    names: set[str] = set()
    in_dependencies = False
    for raw_line in Path("pyproject.toml").read_text(encoding="utf-8").splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if line == "dependencies = [":
            in_dependencies = True
            continue
        if in_dependencies and line == "]":
            break
        if not in_dependencies or not line:
            continue

        name = _dependency_name(line.strip('",'))
        if name:
            names.add(name)

    return names


def _pyproject_requires_python() -> str:
    for raw_line in Path("pyproject.toml").read_text(encoding="utf-8").splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if line.startswith("requires-python"):
            return line.split("=", 1)[1].strip().strip('"')
    return ""


def test_pyproject_requires_python_310_for_union_type_syntax() -> None:
    assert _pyproject_requires_python() == ">=3.10"


def test_requirements_include_deployment_runtime_dependencies() -> None:
    assert {
        "websockets",
        "tree-sitter-java",
        "tree-sitter-go",
        "tree-sitter-rust",
    } <= _requirement_names()


def test_pyproject_includes_deployment_runtime_dependencies() -> None:
    assert {
        "flower",
        "psycopg2-binary",
        "uvicorn",
    } <= _pyproject_dependency_names()


def test_dev_requirements_include_local_test_runner_dependencies() -> None:
    assert {"pytest", "pytest-asyncio"} <= _requirement_names_from(
        "requirements-dev.txt"
    )


def test_ci_uses_dev_requirements_for_test_dependencies() -> None:
    workflow = Path(".github/workflows/deploy.yml").read_text(encoding="utf-8")
    assert "python -m pip install -r requirements-dev.txt" in workflow
    assert "python -m pytest -x -q" in workflow
    assert "pip install pytest pytest-asyncio" not in workflow
    assert "run: pytest -x -q" not in workflow
