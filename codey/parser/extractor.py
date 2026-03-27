"""AST extraction via tree-sitter — turns source files into CodeNode/CodeEdge graphs."""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import tree_sitter
import tree_sitter_javascript
import tree_sitter_python
import tree_sitter_typescript

logger = logging.getLogger(__name__)

NodeKind = Literal["file", "function", "class", "method", "variable"]
EdgeKind = Literal["import", "call", "inheritance", "state_dep", "data_flow"]

SKIP_DIRS = {"node_modules", "__pycache__", ".git", ".venv", "dist", "build"}
DEFAULT_EXTENSIONS = {".py", ".js", ".ts", ".jsx", ".tsx"}

# tree-sitter node types that represent decision points for cyclomatic complexity
_PYTHON_DECISION_TYPES = {
    "if_statement",
    "elif_clause",
    "for_statement",
    "while_statement",
    "except_clause",
    "with_statement",
    "boolean_operator",  # `and` / `or`
    "conditional_expression",  # ternary
    "list_comprehension",
    "set_comprehension",
    "dictionary_comprehension",
    "generator_expression",
}

_JS_DECISION_TYPES = {
    "if_statement",
    "for_statement",
    "for_in_statement",
    "while_statement",
    "do_statement",
    "switch_case",
    "catch_clause",
    "ternary_expression",
    "binary_expression",  # filtered to && / || at check time
    "optional_chain_expression",
}


def _node_id(file_path: str, name: str, line: int) -> str:
    """Deterministic ID for a code entity."""
    raw = f"{file_path}::{name}::{line}"
    return hashlib.sha1(raw.encode()).hexdigest()[:12]


def _text(node: tree_sitter.Node) -> str:
    """Decode a tree-sitter node's text."""
    return node.text.decode("utf-8", errors="replace") if node else ""


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class CodeNode:
    id: str
    kind: NodeKind
    name: str
    file_path: str
    line_start: int
    line_end: int
    complexity: float = 0.0
    cohesion: float = 1.0
    properties: dict = field(default_factory=dict)


@dataclass
class CodeEdge:
    source: str
    target: str
    kind: EdgeKind
    weight: float = 1.0
    properties: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# LanguageParser
# ---------------------------------------------------------------------------


class LanguageParser:
    """Parses source files using tree-sitter and extracts structural nodes & edges."""

    def __init__(self) -> None:
        py_lang = tree_sitter.Language(tree_sitter_python.language())
        js_lang = tree_sitter.Language(tree_sitter_javascript.language())
        ts_lang = tree_sitter.Language(tree_sitter_typescript.language_typescript())
        tsx_lang = tree_sitter.Language(tree_sitter_typescript.language_tsx())

        py_parser = tree_sitter.Parser(py_lang)
        js_parser = tree_sitter.Parser(js_lang)
        ts_parser = tree_sitter.Parser(ts_lang)
        tsx_parser = tree_sitter.Parser(tsx_lang)

        self._parsers: dict[str, tree_sitter.Parser] = {
            ".py": py_parser,
            ".js": js_parser,
            ".jsx": js_parser,
            ".ts": ts_parser,
            ".tsx": tsx_parser,
        }

        # Track which extensions use which extractor
        self._js_extensions = {".js", ".jsx", ".ts", ".tsx"}

    def parse_file(self, file_path: Path) -> tuple[list[CodeNode], list[CodeEdge]]:
        """Parse a single file and return extracted nodes and edges."""
        ext = file_path.suffix.lower()
        parser = self._parsers.get(ext)
        if parser is None:
            logger.debug("No parser for extension %s (file: %s)", ext, file_path)
            return [], []

        try:
            source_bytes = file_path.read_bytes()
        except (OSError, PermissionError) as exc:
            logger.warning("Could not read %s: %s", file_path, exc)
            return [], []

        try:
            tree = parser.parse(source_bytes)
        except Exception as exc:
            logger.warning("tree-sitter parse failed for %s: %s", file_path, exc)
            return [], []

        fp = str(file_path)
        if ext == ".py":
            return self._extract_python(tree, fp, source_bytes)
        elif ext in self._js_extensions:
            return self._extract_javascript(tree, fp, source_bytes)

        return [], []

    # ------------------------------------------------------------------
    # Python extraction
    # ------------------------------------------------------------------

    def _extract_python(
        self, tree: tree_sitter.Tree, file_path: str, source_bytes: bytes
    ) -> tuple[list[CodeNode], list[CodeEdge]]:
        nodes: list[CodeNode] = []
        edges: list[CodeEdge] = []
        root = tree.root_node

        # File node
        file_id = _node_id(file_path, "<file>", 0)
        nodes.append(
            CodeNode(
                id=file_id,
                kind="file",
                name=Path(file_path).name,
                file_path=file_path,
                line_start=root.start_point.row + 1,
                line_end=root.end_point.row + 1,
            )
        )

        # Class-name -> class node-id mapping for resolving method parents
        class_id_map: dict[str, str] = {}

        self._walk_python_node(
            root, file_path, file_id, nodes, edges, class_id_map, source_bytes
        )
        return nodes, edges

    def _walk_python_node(
        self,
        node: tree_sitter.Node,
        file_path: str,
        file_id: str,
        nodes: list[CodeNode],
        edges: list[CodeEdge],
        class_id_map: dict[str, str],
        source_bytes: bytes,
        current_class_id: str | None = None,
    ) -> None:
        for child in node.children:
            if child.type == "class_definition":
                self._handle_python_class(
                    child, file_path, file_id, nodes, edges, class_id_map, source_bytes
                )
            elif child.type == "function_definition":
                self._handle_python_function(
                    child,
                    file_path,
                    file_id,
                    nodes,
                    edges,
                    source_bytes,
                    current_class_id,
                )
            elif child.type in ("import_statement", "import_from_statement"):
                self._handle_python_import(child, file_path, file_id, edges)
            elif child.type == "expression_statement":
                # Look for top-level calls
                self._extract_python_calls(
                    child, file_path, file_id, edges, current_class_id
                )
            else:
                # Recurse into compound statements (if/for/with blocks etc.)
                self._walk_python_node(
                    child,
                    file_path,
                    file_id,
                    nodes,
                    edges,
                    class_id_map,
                    source_bytes,
                    current_class_id,
                )

    def _handle_python_class(
        self,
        node: tree_sitter.Node,
        file_path: str,
        file_id: str,
        nodes: list[CodeNode],
        edges: list[CodeEdge],
        class_id_map: dict[str, str],
        source_bytes: bytes,
    ) -> None:
        name_node = node.child_by_field_name("name")
        class_name = _text(name_node) if name_node else "<anonymous>"
        class_id = _node_id(file_path, class_name, node.start_point.row)
        class_id_map[class_name] = class_id

        nodes.append(
            CodeNode(
                id=class_id,
                kind="class",
                name=class_name,
                file_path=file_path,
                line_start=node.start_point.row + 1,
                line_end=node.end_point.row + 1,
            )
        )

        # Inheritance edges from base classes
        superclasses_node = node.child_by_field_name("superclasses")
        if superclasses_node:
            for arg in superclasses_node.children:
                if arg.type == "identifier":
                    base_name = _text(arg)
                    edges.append(
                        CodeEdge(
                            source=class_id,
                            target=base_name,  # symbolic — resolved later
                            kind="inheritance",
                            properties={"base_class": base_name},
                        )
                    )
                elif arg.type == "attribute":
                    base_name = _text(arg)
                    edges.append(
                        CodeEdge(
                            source=class_id,
                            target=base_name,
                            kind="inheritance",
                            properties={"base_class": base_name},
                        )
                    )

        # Walk class body for methods
        body = node.child_by_field_name("body")
        if body:
            self._walk_python_node(
                body,
                file_path,
                file_id,
                nodes,
                edges,
                class_id_map,
                source_bytes,
                current_class_id=class_id,
            )

    def _handle_python_function(
        self,
        node: tree_sitter.Node,
        file_path: str,
        file_id: str,
        nodes: list[CodeNode],
        edges: list[CodeEdge],
        source_bytes: bytes,
        current_class_id: str | None,
    ) -> None:
        name_node = node.child_by_field_name("name")
        func_name = _text(name_node) if name_node else "<anonymous>"
        func_id = _node_id(file_path, func_name, node.start_point.row)
        kind: NodeKind = "method" if current_class_id else "function"

        complexity = self._compute_cyclomatic_complexity(node, language="python")
        nodes.append(
            CodeNode(
                id=func_id,
                kind=kind,
                name=func_name,
                file_path=file_path,
                line_start=node.start_point.row + 1,
                line_end=node.end_point.row + 1,
                complexity=complexity,
            )
        )

        # Extract call edges from function body
        body = node.child_by_field_name("body")
        if body:
            caller_id = func_id
            self._extract_python_calls(
                body, file_path, caller_id, edges, current_class_id
            )

    def _handle_python_import(
        self,
        node: tree_sitter.Node,
        file_path: str,
        file_id: str,
        edges: list[CodeEdge],
    ) -> None:
        # import X / from X import Y
        if node.type == "import_statement":
            for child in node.children:
                if child.type == "dotted_name":
                    module_name = _text(child)
                    edges.append(
                        CodeEdge(
                            source=file_id,
                            target=module_name,
                            kind="import",
                            properties={"module": module_name},
                        )
                    )
                elif child.type == "aliased_import":
                    name_child = child.child_by_field_name("name")
                    if name_child:
                        module_name = _text(name_child)
                        edges.append(
                            CodeEdge(
                                source=file_id,
                                target=module_name,
                                kind="import",
                                properties={"module": module_name},
                            )
                        )
        elif node.type == "import_from_statement":
            module_node = node.child_by_field_name("module_name")
            module_name = _text(module_node) if module_node else "<unknown>"
            # Collect imported names
            imported: list[str] = []
            for child in node.children:
                if child.type == "dotted_name" and child != module_node:
                    imported.append(_text(child))
                elif child.type == "import_list":
                    for item in child.children:
                        if item.type in ("dotted_name", "identifier"):
                            imported.append(_text(item))
                        elif item.type == "aliased_import":
                            name_child = item.child_by_field_name("name")
                            if name_child:
                                imported.append(_text(name_child))
            edges.append(
                CodeEdge(
                    source=file_id,
                    target=module_name,
                    kind="import",
                    properties={"module": module_name, "names": imported},
                )
            )

    def _extract_python_calls(
        self,
        node: tree_sitter.Node,
        file_path: str,
        caller_id: str,
        edges: list[CodeEdge],
        current_class_id: str | None,
    ) -> None:
        """Recursively find call expressions and create call edges."""
        if node.type == "call":
            func_node = node.child_by_field_name("function")
            if func_node:
                callee_name = _text(func_node)
                edges.append(
                    CodeEdge(
                        source=caller_id,
                        target=callee_name,
                        kind="call",
                        properties={"line": node.start_point.row + 1},
                    )
                )
        for child in node.children:
            self._extract_python_calls(
                child, file_path, caller_id, edges, current_class_id
            )

    # ------------------------------------------------------------------
    # JavaScript / TypeScript extraction
    # ------------------------------------------------------------------

    def _extract_javascript(
        self, tree: tree_sitter.Tree, file_path: str, source_bytes: bytes
    ) -> tuple[list[CodeNode], list[CodeEdge]]:
        nodes: list[CodeNode] = []
        edges: list[CodeEdge] = []
        root = tree.root_node

        file_id = _node_id(file_path, "<file>", 0)
        nodes.append(
            CodeNode(
                id=file_id,
                kind="file",
                name=Path(file_path).name,
                file_path=file_path,
                line_start=root.start_point.row + 1,
                line_end=root.end_point.row + 1,
            )
        )

        self._walk_js_node(root, file_path, file_id, nodes, edges, source_bytes)
        return nodes, edges

    def _walk_js_node(
        self,
        node: tree_sitter.Node,
        file_path: str,
        file_id: str,
        nodes: list[CodeNode],
        edges: list[CodeEdge],
        source_bytes: bytes,
        current_class_id: str | None = None,
    ) -> None:
        for child in node.children:
            if child.type == "class_declaration":
                self._handle_js_class(
                    child, file_path, file_id, nodes, edges, source_bytes
                )
            elif child.type in ("function_declaration", "generator_function_declaration"):
                self._handle_js_function(
                    child, file_path, file_id, nodes, edges, source_bytes, None
                )
            elif child.type == "lexical_declaration":
                # const foo = () => {} or const foo = function() {}
                self._handle_js_lexical(
                    child,
                    file_path,
                    file_id,
                    nodes,
                    edges,
                    source_bytes,
                    current_class_id,
                )
            elif child.type == "export_statement":
                # Recurse into export to find declarations
                self._walk_js_node(
                    child,
                    file_path,
                    file_id,
                    nodes,
                    edges,
                    source_bytes,
                    current_class_id,
                )
            elif child.type == "import_statement":
                self._handle_js_import(child, file_path, file_id, edges)
            elif child.type == "expression_statement":
                self._extract_js_calls(child, file_path, file_id, edges)
            else:
                self._walk_js_node(
                    child,
                    file_path,
                    file_id,
                    nodes,
                    edges,
                    source_bytes,
                    current_class_id,
                )

    def _handle_js_class(
        self,
        node: tree_sitter.Node,
        file_path: str,
        file_id: str,
        nodes: list[CodeNode],
        edges: list[CodeEdge],
        source_bytes: bytes,
    ) -> None:
        name_node = node.child_by_field_name("name")
        class_name = _text(name_node) if name_node else "<anonymous>"
        class_id = _node_id(file_path, class_name, node.start_point.row)

        nodes.append(
            CodeNode(
                id=class_id,
                kind="class",
                name=class_name,
                file_path=file_path,
                line_start=node.start_point.row + 1,
                line_end=node.end_point.row + 1,
            )
        )

        # Inheritance: class Foo extends Bar
        heritage_node = None
        for child in node.children:
            if child.type == "class_heritage":
                heritage_node = child
                break
        if heritage_node:
            for child in heritage_node.children:
                if child.type == "identifier":
                    base_name = _text(child)
                    edges.append(
                        CodeEdge(
                            source=class_id,
                            target=base_name,
                            kind="inheritance",
                            properties={"base_class": base_name},
                        )
                    )
                elif child.type == "member_expression":
                    base_name = _text(child)
                    edges.append(
                        CodeEdge(
                            source=class_id,
                            target=base_name,
                            kind="inheritance",
                            properties={"base_class": base_name},
                        )
                    )

        # Walk class body for methods
        body = node.child_by_field_name("body")
        if body:
            for child in body.children:
                if child.type == "method_definition":
                    self._handle_js_method(
                        child, file_path, class_id, nodes, edges, source_bytes
                    )

    def _handle_js_function(
        self,
        node: tree_sitter.Node,
        file_path: str,
        file_id: str,
        nodes: list[CodeNode],
        edges: list[CodeEdge],
        source_bytes: bytes,
        current_class_id: str | None,
    ) -> None:
        name_node = node.child_by_field_name("name")
        func_name = _text(name_node) if name_node else "<anonymous>"
        func_id = _node_id(file_path, func_name, node.start_point.row)

        complexity = self._compute_cyclomatic_complexity(node, language="javascript")
        nodes.append(
            CodeNode(
                id=func_id,
                kind="function",
                name=func_name,
                file_path=file_path,
                line_start=node.start_point.row + 1,
                line_end=node.end_point.row + 1,
                complexity=complexity,
            )
        )

        body = node.child_by_field_name("body")
        if body:
            self._extract_js_calls(body, file_path, func_id, edges)

    def _handle_js_method(
        self,
        node: tree_sitter.Node,
        file_path: str,
        class_id: str,
        nodes: list[CodeNode],
        edges: list[CodeEdge],
        source_bytes: bytes,
    ) -> None:
        name_node = node.child_by_field_name("name")
        method_name = _text(name_node) if name_node else "<anonymous>"
        method_id = _node_id(file_path, method_name, node.start_point.row)

        complexity = self._compute_cyclomatic_complexity(node, language="javascript")
        nodes.append(
            CodeNode(
                id=method_id,
                kind="method",
                name=method_name,
                file_path=file_path,
                line_start=node.start_point.row + 1,
                line_end=node.end_point.row + 1,
                complexity=complexity,
            )
        )

        body = node.child_by_field_name("body")
        if body:
            self._extract_js_calls(body, file_path, method_id, edges)

    def _handle_js_lexical(
        self,
        node: tree_sitter.Node,
        file_path: str,
        file_id: str,
        nodes: list[CodeNode],
        edges: list[CodeEdge],
        source_bytes: bytes,
        current_class_id: str | None,
    ) -> None:
        """Handle `const/let foo = () => {}` style function declarations."""
        for child in node.children:
            if child.type == "variable_declarator":
                name_node = child.child_by_field_name("name")
                value_node = child.child_by_field_name("value")
                if name_node and value_node and value_node.type in (
                    "arrow_function",
                    "function_expression",
                    "generator_function",
                ):
                    func_name = _text(name_node)
                    func_id = _node_id(file_path, func_name, child.start_point.row)
                    complexity = self._compute_cyclomatic_complexity(
                        value_node, language="javascript"
                    )
                    nodes.append(
                        CodeNode(
                            id=func_id,
                            kind="function",
                            name=func_name,
                            file_path=file_path,
                            line_start=child.start_point.row + 1,
                            line_end=child.end_point.row + 1,
                            complexity=complexity,
                        )
                    )
                    body = value_node.child_by_field_name("body")
                    if body:
                        self._extract_js_calls(body, file_path, func_id, edges)

    def _handle_js_import(
        self,
        node: tree_sitter.Node,
        file_path: str,
        file_id: str,
        edges: list[CodeEdge],
    ) -> None:
        source_node = node.child_by_field_name("source")
        if not source_node:
            return
        module_name = _text(source_node).strip("'\"")

        imported: list[str] = []
        for child in node.children:
            if child.type == "import_clause":
                for clause_child in child.children:
                    if clause_child.type == "identifier":
                        imported.append(_text(clause_child))
                    elif clause_child.type == "named_imports":
                        for spec in clause_child.children:
                            if spec.type == "import_specifier":
                                name_child = spec.child_by_field_name("name")
                                if name_child:
                                    imported.append(_text(name_child))
                    elif clause_child.type == "namespace_import":
                        imported.append(_text(clause_child))

        edges.append(
            CodeEdge(
                source=file_id,
                target=module_name,
                kind="import",
                properties={"module": module_name, "names": imported},
            )
        )

    def _extract_js_calls(
        self,
        node: tree_sitter.Node,
        file_path: str,
        caller_id: str,
        edges: list[CodeEdge],
    ) -> None:
        """Recursively find call expressions and create call edges."""
        if node.type == "call_expression":
            func_node = node.child_by_field_name("function")
            if func_node:
                callee_name = _text(func_node)
                edges.append(
                    CodeEdge(
                        source=caller_id,
                        target=callee_name,
                        kind="call",
                        properties={"line": node.start_point.row + 1},
                    )
                )
        for child in node.children:
            self._extract_js_calls(child, file_path, caller_id, edges)

    # ------------------------------------------------------------------
    # Cyclomatic complexity
    # ------------------------------------------------------------------

    def _compute_cyclomatic_complexity(
        self, node: tree_sitter.Node, language: str = "python"
    ) -> float:
        """Count decision points in a subtree. Complexity = 1 + decision_count."""
        decision_types = (
            _PYTHON_DECISION_TYPES if language == "python" else _JS_DECISION_TYPES
        )
        count = 1  # baseline

        def _walk(n: tree_sitter.Node) -> None:
            nonlocal count
            if n.type in decision_types:
                # For JS binary_expression, only count logical operators
                if n.type == "binary_expression" and language == "javascript":
                    op_node = n.child_by_field_name("operator")
                    if op_node and _text(op_node) in ("&&", "||", "??"):
                        count += 1
                else:
                    count += 1
            for child in n.children:
                _walk(child)

        _walk(node)
        return float(count)


# ---------------------------------------------------------------------------
# Directory scanner
# ---------------------------------------------------------------------------


def parse_directory(
    root: Path,
    extensions: set[str] | None = None,
) -> tuple[list[CodeNode], list[CodeEdge]]:
    """Walk a directory tree, parse all supported files, return aggregated nodes & edges."""
    exts = extensions or DEFAULT_EXTENSIONS
    all_nodes: list[CodeNode] = []
    all_edges: list[CodeEdge] = []
    parser = LanguageParser()

    root = root.resolve()
    if not root.is_dir():
        logger.error("parse_directory: %s is not a directory", root)
        return all_nodes, all_edges

    for path in sorted(root.rglob("*")):
        # Skip excluded directories
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if not path.is_file():
            continue
        if path.suffix.lower() not in exts:
            continue

        try:
            file_nodes, file_edges = parser.parse_file(path)
            all_nodes.extend(file_nodes)
            all_edges.extend(file_edges)
        except Exception as exc:
            logger.warning("Failed to parse %s: %s", path, exc)
            continue

    logger.info(
        "Parsed %d files -> %d nodes, %d edges from %s",
        sum(1 for n in all_nodes if n.kind == "file"),
        len(all_nodes),
        len(all_edges),
        root,
    )
    return all_nodes, all_edges
