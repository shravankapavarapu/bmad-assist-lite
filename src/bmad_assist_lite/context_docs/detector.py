"""Library detection from project artifacts and dependency files."""

import json
import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

# Known framework patterns to scan for in docs (case-insensitive)
_FRAMEWORK_PATTERNS: list[tuple[str, str]] = [
    (r"\breact\b", "react"),
    (r"\bnext\.?js\b", "next.js"),
    (r"\bvue\b", "vue"),
    (r"\bsvelte\b", "svelte"),
    (r"\bangular\b", "angular"),
    (r"\bexpress\b", "express"),
    (r"\bfastapi\b", "fastapi"),
    (r"\bdjango\b", "django"),
    (r"\bflask\b", "flask"),
    (r"\bspring\b", "spring"),
    (r"\btailwind\b", "tailwindcss"),
    (r"\bprisma\b", "prisma"),
    (r"\bsqlalchemy\b", "sqlalchemy"),
    (r"\btypescript\b", "typescript"),
    (r"\bgraphql\b", "graphql"),
    (r"\bdocker\b", "docker"),
    (r"\bkubernetes\b", "kubernetes"),
    (r"\bterraform\b", "terraform"),
    (r"\bpydantic\b", "pydantic"),
    (r"\bcelery\b", "celery"),
    (r"\bredis\b", "redis"),
    (r"\bmongodb\b", "mongodb"),
    (r"\bpostgres(?:ql)?\b", "postgresql"),
    (r"\bsupabase\b", "supabase"),
    (r"\bfirebase\b", "firebase"),
    (r"\brust\b", "rust"),
    (r"\btokio\b", "tokio"),
    (r"\baxum\b", "axum"),
    (r"\bactix\b", "actix"),
]

# Dependencies to skip (too generic or not useful for API docs)
_SKIP_DEPS: set[str] = {
    "python",
    "pip",
    "setuptools",
    "wheel",
    "pytest",
    "mypy",
    "ruff",
    "black",
    "flake8",
    "isort",
    "coverage",
    "pytest-cov",
    "pytest-asyncio",
    "types-pyyaml",
    "eslint",
    "prettier",
    "typescript",
    "ts-node",
    "@types/node",
    "@types/react",
    "@types/jest",
    "jest",
    "vitest",
    "mocha",
    "chai",
}


def _detect_from_package_json(project_root: Path) -> list[str]:
    """Extract dependencies from package.json."""
    path = project_root / "package.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        deps: list[str] = []
        for section in ("dependencies", "devDependencies"):
            if section in data and isinstance(data[section], dict):
                deps.extend(data[section].keys())
        return [d for d in deps if d.lower() not in _SKIP_DEPS]
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Failed to parse package.json: %s", e)
        return []


def _detect_from_pyproject(project_root: Path) -> list[str]:
    """Extract dependencies from pyproject.toml."""
    path = project_root / "pyproject.toml"
    if not path.exists():
        return []
    try:
        content = path.read_text(encoding="utf-8")
        # Simple regex extraction — avoids tomllib dependency for Python 3.11 compat
        deps: list[str] = []
        in_deps = False
        for line in content.splitlines():
            stripped = line.strip()
            if stripped == "dependencies = [":
                in_deps = True
                continue
            if in_deps:
                if stripped == "]":
                    in_deps = False
                    continue
                # Extract package name from "package>=version" or "package[extra]"
                match = re.match(r'"([a-zA-Z0-9_-]+)', stripped)
                if match:
                    deps.append(match.group(1))
        return [d for d in deps if d.lower() not in _SKIP_DEPS]
    except OSError as e:
        logger.warning("Failed to parse pyproject.toml: %s", e)
        return []


def _detect_from_requirements(project_root: Path) -> list[str]:
    """Extract dependencies from requirements.txt."""
    path = project_root / "requirements.txt"
    if not path.exists():
        return []
    try:
        deps: list[str] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or line.startswith("-"):
                continue
            match = re.match(r"([a-zA-Z0-9_-]+)", line)
            if match:
                deps.append(match.group(1))
        return [d for d in deps if d.lower() not in _SKIP_DEPS]
    except OSError as e:
        logger.warning("Failed to parse requirements.txt: %s", e)
        return []


def _detect_from_cargo(project_root: Path) -> list[str]:
    """Extract dependencies from Cargo.toml."""
    path = project_root / "Cargo.toml"
    if not path.exists():
        return []
    try:
        deps: list[str] = []
        in_deps = False
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if re.match(r"\[(.*dependencies.*)\]", stripped):
                in_deps = True
                continue
            if stripped.startswith("[") and in_deps:
                in_deps = False
                continue
            if in_deps:
                match = re.match(r"([a-zA-Z0-9_-]+)\s*=", stripped)
                if match:
                    deps.append(match.group(1))
        return deps
    except OSError as e:
        logger.warning("Failed to parse Cargo.toml: %s", e)
        return []


def _scan_doc_for_frameworks(text: str) -> list[str]:
    """Scan text for known framework mentions."""
    found: list[str] = []
    text_lower = text.lower()
    for pattern, name in _FRAMEWORK_PATTERNS:
        if re.search(pattern, text_lower):
            found.append(name)
    return found


def detect_libraries(
    project_root: Path,
    epic_file: Path | None = None,
    architecture_files: list[Path] | None = None,
    max_libs: int = 8,
) -> list[str]:
    """Detect libraries used in a project from dependency files and documentation.

    Args:
        project_root: Path to the project root directory.
        epic_file: Optional path to an epic markdown file to scan.
        architecture_files: Optional list of architecture doc paths to scan.
        max_libs: Maximum number of libraries to return.

    Returns:
        Deduplicated list of library names, capped at max_libs.

    """
    all_libs: list[str] = []

    # 1. Parse dependency files (highest priority — concrete deps)
    all_libs.extend(_detect_from_package_json(project_root))
    all_libs.extend(_detect_from_pyproject(project_root))
    all_libs.extend(_detect_from_requirements(project_root))
    all_libs.extend(_detect_from_cargo(project_root))

    # 2. Scan documentation for framework patterns
    doc_paths = list(architecture_files or []) + ([epic_file] if epic_file else [])
    for doc_path in doc_paths:
        if doc_path and doc_path.exists():
            try:
                text = doc_path.read_text(encoding="utf-8")
                all_libs.extend(_scan_doc_for_frameworks(text))
            except OSError as e:
                logger.warning("Failed to read %s: %s", doc_path, e)

    # Deduplicate preserving order, normalize to lowercase
    seen: set[str] = set()
    deduped: list[str] = []
    for lib in all_libs:
        key = lib.lower().strip()
        if key and key not in seen:
            seen.add(key)
            deduped.append(lib.strip())

    return deduped[:max_libs]
