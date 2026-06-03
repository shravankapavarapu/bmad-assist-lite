"""File discovery and inclusion for BMAD workflow compiler."""

from __future__ import annotations

import glob
import logging
import re
from enum import StrEnum
from pathlib import Path
from typing import Any

from bmad_assist_lite.compiler.types import CompilerContext
from bmad_assist_lite.core.exceptions import AmbiguousFileError, CompilerError

logger = logging.getLogger(__name__)


class LoadStrategy(StrEnum):
    """File loading strategy."""

    FULL_LOAD = "FULL_LOAD"
    SELECTIVE_LOAD = "SELECTIVE_LOAD"


def discover_files(context: CompilerContext) -> dict[str, list[Path]]:
    """Discover files based on input_file_patterns in workflow config."""
    if context.workflow_ir is None:
        raise CompilerError("Cannot discover files: workflow_ir not set in context")

    raw_config = context.workflow_ir.raw_config
    patterns_config = raw_config.get("input_file_patterns", {})

    if not patterns_config:
        context.discovered_files = {}
        return {}

    discovered: dict[str, list[Path]] = {}

    for pattern_name, pattern_config in patterns_config.items():
        files = _discover_pattern(pattern_name, pattern_config, context)
        discovered[pattern_name] = files

    _validate_required_files(patterns_config, discovered)
    context.discovered_files = discovered
    return discovered


def _resolve_pattern_variables(pattern: str, context: CompilerContext) -> str:
    """Resolve variable placeholders in a file discovery pattern."""
    result = pattern

    # Resolve path placeholders
    if "{project-root}" in result:
        result = result.replace("{project-root}", str(context.project_root))
    if "{output_folder}" in result:
        result = result.replace("{output_folder}", str(context.output_folder))

    # Resolve variables from context.resolved_variables
    for key, value in context.resolved_variables.items():
        placeholder = "{" + key + "}"
        if placeholder in result and isinstance(value, (str, int, float)):
            result = result.replace(placeholder, str(value))

    # Warn about unresolved placeholders
    remaining = re.findall(r"\{([a-zA-Z_][a-zA-Z0-9_-]*)\}", result)
    if remaining:
        logger.warning(
            "Unresolved variables in pattern '%s': %s", pattern, remaining
        )

    return result


def _discover_pattern(
    pattern_name: str,
    pattern_config: dict[str, Any],
    context: CompilerContext,
) -> list[Path]:
    """Discover files for a single pattern configuration."""
    sharded_pattern = pattern_config.get("sharded")
    whole_pattern = pattern_config.get("whole")
    # Support 'pattern' key as fallback for 'whole'
    if not whole_pattern:
        whole_pattern = pattern_config.get("pattern")

    # Support both 'load_strategy' and 'strategy' keys
    strategy_str = pattern_config.get("load_strategy") or pattern_config.get(
        "strategy", "FULL_LOAD"
    )

    try:
        strategy = LoadStrategy(strategy_str)
    except ValueError:
        strategy = LoadStrategy.FULL_LOAD

    files: list[Path] = []

    if sharded_pattern:
        resolved_sharded = _resolve_pattern_variables(sharded_pattern, context)
        sharded_files = _glob_files(resolved_sharded, pattern_name, context.project_root)
        if sharded_files:
            files = sharded_files

    if not files and whole_pattern:
        resolved_whole = _resolve_pattern_variables(whole_pattern, context)
        files = _glob_files(resolved_whole, pattern_name, context.project_root)

    files = _apply_load_strategy(files, strategy, pattern_name)
    return files


def _glob_files(pattern: str, pattern_name: str, project_root: Path) -> list[Path]:
    """Execute glob pattern and filter results."""
    try:
        matches = glob.glob(pattern, recursive=True)
    except re.error as e:
        raise CompilerError(
            f"Invalid glob pattern for '{pattern_name}': {pattern}\n  Error: {e}"
        ) from e

    files: list[Path] = []
    visited: set[Path] = set()

    for match in matches:
        path = Path(match)
        if not path.is_file():
            continue

        try:
            resolved = path.resolve()
        except (OSError, RuntimeError):
            continue

        if resolved in visited:
            continue
        visited.add(resolved)

        try:
            if not resolved.is_relative_to(project_root.resolve()):
                continue
        except (OSError, ValueError):
            continue

        files.append(path)

    return sorted(files, key=lambda f: str(f))


def _apply_load_strategy(
    files: list[Path],
    strategy: LoadStrategy,
    pattern_name: str,
) -> list[Path]:
    """Apply load strategy to discovered files."""
    if not files:
        return []

    if strategy == LoadStrategy.FULL_LOAD:
        return files

    if strategy == LoadStrategy.SELECTIVE_LOAD:
        if len(files) > 1:
            sorted_files = sorted(files, key=lambda f: str(f))
            candidates_text = "\n    - ".join(str(f) for f in sorted_files[:10])
            raise AmbiguousFileError(
                f"Multiple files match pattern '{pattern_name}' with SELECTIVE_LOAD\n"
                f"  Candidates:\n    - {candidates_text}",
                pattern_name=pattern_name,
                candidates=sorted_files,
            )
        return files

    return files


def _validate_required_files(
    patterns_config: dict[str, Any],
    discovered: dict[str, list[Path]],
) -> None:
    """Validate that required files were discovered."""
    for pattern_name, config in patterns_config.items():
        required = config.get("required", False)
        if required and not discovered.get(pattern_name):
            raise CompilerError(f"Required file not found for pattern '{pattern_name}'")


def load_file_contents(
    context: CompilerContext,
    patterns: list[str] | None = None,
) -> dict[str, str]:
    """Load content from discovered files into context."""
    if patterns is None:
        patterns = list(context.discovered_files.keys())

    result: dict[str, str] = {}

    for pattern_name in patterns:
        files = context.discovered_files.get(pattern_name, [])
        if not files:
            result[pattern_name] = ""
            continue

        content_parts: list[str] = []
        for file_path in files:
            try:
                content = file_path.read_text(encoding="utf-8")
                content_parts.append(content)
                context.per_file_contents[file_path.name.lower()] = content
            except UnicodeDecodeError:
                continue
            except PermissionError as e:
                raise CompilerError(f"Permission denied reading file: {file_path}") from e
            except OSError as e:
                logger.warning("Error reading file '%s': %s", file_path, e)

        result[pattern_name] = "\n\n".join(content_parts)

    context.file_contents.update(result)
    return result


def extract_section(file_path: Path, section_id: str) -> str:
    """Extract a section from markdown file by header match."""
    try:
        content = file_path.read_text(encoding="utf-8")
    except OSError as e:
        raise CompilerError(f"Cannot read file: {file_path}\n  Error: {e}") from e

    lines = content.split("\n")
    normalized_id = re.sub(r"[-._]", " ", section_id.lower())

    start_idx: int | None = None
    start_level = 0

    for i, line in enumerate(lines):
        if not line.startswith("#"):
            continue
        match = re.match(r"^(#+)", line)
        if not match:
            continue

        level = len(match.group(1))
        header_text = line.lstrip("#").strip().lower()
        normalized_header = re.sub(r"[-._]", " ", header_text)

        pattern = r"\b" + re.escape(normalized_id) + r"\b"
        if re.search(pattern, normalized_header):
            start_idx = i
            start_level = level
            break

    if start_idx is None:
        raise CompilerError(f"Section not found in {file_path}: '{section_id}'")

    end_idx = len(lines)
    for i in range(start_idx + 1, len(lines)):
        line = lines[i]
        if line.startswith("#"):
            match = re.match(r"^(#+)", line)
            if match and len(match.group(1)) <= start_level:
                end_idx = i
                break

    return "\n".join(lines[start_idx:end_idx])


def find_closest_file(
    base_dir: Path,
    pattern: str,
    exclude_dirs: list[str] | None = None,
) -> Path | None:
    """Find file matching pattern closest to base directory."""
    if exclude_dirs is None:
        exclude_dirs = []

    exclude_lower = [d.lower() for d in exclude_dirs]
    full_pattern = str(base_dir / pattern)

    try:
        matches = glob.glob(full_pattern, recursive=True)
    except Exception:
        return None

    valid_files: list[Path] = []
    base_dir_resolved = base_dir.resolve()

    for match in matches:
        path = Path(match)
        if not path.is_file():
            continue
        # Ensure file is within base directory (prevent path traversal)
        try:
            if not path.resolve().is_relative_to(base_dir_resolved):
                continue
        except (OSError, ValueError):
            continue
        path_parts_lower = [p.lower() for p in path.parts]
        if any(excl in path_parts_lower for excl in exclude_lower):
            continue
        valid_files.append(path)

    if not valid_files:
        return None

    valid_files.sort(key=lambda p: (len(p.parts), str(p)))
    return valid_files[0]
