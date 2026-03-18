"""Tests for parallel module configuration and exception hierarchy."""

from pathlib import Path

import pytest
from conftest import MINIMAL_CONFIG_DATA
from pydantic import ValidationError

from bmad_assist_lite.core.config import _reset_config, load_config
from bmad_assist_lite.core.exceptions import BmadAssistError, ConfigError
from bmad_assist_lite.parallel.config import ParallelConfig
from bmad_assist_lite.parallel.exceptions import ParallelError

# ============================================================================
# ParallelConfig — Default Values
# ============================================================================


class TestParallelConfigDefaults:
    """Test ParallelConfig default values (Task 4.2)."""

    def test_default_max_concurrency(self) -> None:
        config = ParallelConfig()
        assert config.max_concurrency == 3

    def test_default_stagger_delay(self) -> None:
        config = ParallelConfig()
        assert config.stagger_delay == 10.0

    def test_default_post_merge_fix_retries(self) -> None:
        config = ParallelConfig()
        assert config.post_merge_fix_retries == 1

    def test_default_worktree_base_dir(self) -> None:
        config = ParallelConfig()
        assert config.worktree_base_dir is None

    def test_all_defaults_applied(self) -> None:
        config = ParallelConfig()
        assert config.max_concurrency == 3
        assert config.stagger_delay == 10.0
        assert config.post_merge_fix_retries == 1
        assert config.worktree_base_dir is None


# ============================================================================
# ParallelConfig — Valid Custom Values
# ============================================================================


class TestParallelConfigCustomValues:
    """Test ParallelConfig with valid custom values (Task 4.3)."""

    def test_custom_max_concurrency(self) -> None:
        config = ParallelConfig(max_concurrency=5)
        assert config.max_concurrency == 5

    def test_custom_stagger_delay(self) -> None:
        config = ParallelConfig(stagger_delay=30)
        assert config.stagger_delay == 30.0

    def test_fractional_stagger_delay(self) -> None:
        config = ParallelConfig(stagger_delay=0.5)
        assert config.stagger_delay == 0.5

    def test_custom_post_merge_fix_retries(self) -> None:
        config = ParallelConfig(post_merge_fix_retries=3)
        assert config.post_merge_fix_retries == 3

    def test_custom_worktree_base_dir(self) -> None:
        config = ParallelConfig(worktree_base_dir=Path("/tmp/worktrees"))
        assert config.worktree_base_dir == Path("/tmp/worktrees")

    def test_all_custom_values(self) -> None:
        config = ParallelConfig(
            max_concurrency=2,
            stagger_delay=5.0,
            post_merge_fix_retries=0,
            worktree_base_dir=Path("/custom/path"),
        )
        assert config.max_concurrency == 2
        assert config.stagger_delay == 5.0
        assert config.post_merge_fix_retries == 0
        assert config.worktree_base_dir == Path("/custom/path")

    def test_zero_stagger_delay(self) -> None:
        config = ParallelConfig(stagger_delay=0)
        assert config.stagger_delay == 0.0

    def test_zero_post_merge_fix_retries(self) -> None:
        config = ParallelConfig(post_merge_fix_retries=0)
        assert config.post_merge_fix_retries == 0


# ============================================================================
# ParallelConfig — Boundary Validation (max_concurrency 1-5)
# ============================================================================


class TestParallelConfigBoundaryValidation:
    """Test ParallelConfig boundary validation (Task 4.4)."""

    def test_max_concurrency_lower_bound_valid(self) -> None:
        config = ParallelConfig(max_concurrency=1)
        assert config.max_concurrency == 1

    def test_max_concurrency_upper_bound_valid(self) -> None:
        config = ParallelConfig(max_concurrency=5)
        assert config.max_concurrency == 5

    def test_max_concurrency_zero_invalid(self) -> None:
        with pytest.raises(ValidationError, match="max_concurrency"):
            ParallelConfig(max_concurrency=0)

    def test_max_concurrency_six_invalid(self) -> None:
        with pytest.raises(ValidationError, match="max_concurrency"):
            ParallelConfig(max_concurrency=6)

    def test_max_concurrency_negative_invalid(self) -> None:
        with pytest.raises(ValidationError, match="max_concurrency"):
            ParallelConfig(max_concurrency=-1)

    def test_max_concurrency_large_value_invalid(self) -> None:
        with pytest.raises(ValidationError, match="max_concurrency"):
            ParallelConfig(max_concurrency=100)


# ============================================================================
# ParallelConfig — Negative Value Rejection (Task 4.4a)
# ============================================================================


class TestParallelConfigNegativeValues:
    """Test ParallelConfig rejects negative stagger_delay and post_merge_fix_retries."""

    def test_negative_stagger_delay_invalid(self) -> None:
        with pytest.raises(ValidationError, match="stagger_delay"):
            ParallelConfig(stagger_delay=-1)

    def test_large_negative_stagger_delay_invalid(self) -> None:
        with pytest.raises(ValidationError, match="stagger_delay"):
            ParallelConfig(stagger_delay=-100)

    def test_negative_post_merge_fix_retries_invalid(self) -> None:
        with pytest.raises(ValidationError, match="post_merge_fix_retries"):
            ParallelConfig(post_merge_fix_retries=-1)

    def test_large_negative_retries_invalid(self) -> None:
        with pytest.raises(ValidationError, match="post_merge_fix_retries"):
            ParallelConfig(post_merge_fix_retries=-50)


# ============================================================================
# ParallelConfig — worktree_base_dir Coercion (Task 4.5)
# ============================================================================


class TestParallelConfigWorktreeBaseDir:
    """Test ParallelConfig worktree_base_dir string coercion and edge cases."""

    def test_string_path_coerced_to_path(self) -> None:
        config = ParallelConfig(worktree_base_dir="/tmp/worktrees")
        assert isinstance(config.worktree_base_dir, Path)
        assert config.worktree_base_dir == Path("/tmp/worktrees")

    def test_empty_string_coerced_to_none(self) -> None:
        config = ParallelConfig(worktree_base_dir="")
        assert config.worktree_base_dir is None

    def test_whitespace_only_string_coerced_to_none(self) -> None:
        config = ParallelConfig(worktree_base_dir="   ")
        assert config.worktree_base_dir is None

    def test_relative_path_accepted(self) -> None:
        config = ParallelConfig(worktree_base_dir="relative/path")
        assert isinstance(config.worktree_base_dir, Path)
        assert config.worktree_base_dir == Path("relative/path")

    def test_absolute_path_accepted(self) -> None:
        config = ParallelConfig(worktree_base_dir="/absolute/path")
        assert isinstance(config.worktree_base_dir, Path)
        assert config.worktree_base_dir == Path("/absolute/path")

    def test_none_accepted(self) -> None:
        config = ParallelConfig(worktree_base_dir=None)
        assert config.worktree_base_dir is None


# ============================================================================
# ParallelConfig — Frozen Model Enforcement
# ============================================================================


class TestParallelConfigFrozen:
    """Test that ParallelConfig is immutable (frozen model enforcement)."""

    def test_cannot_set_max_concurrency(self) -> None:
        config = ParallelConfig()
        with pytest.raises(ValidationError):
            config.max_concurrency = 5  # type: ignore[misc]

    def test_cannot_set_stagger_delay(self) -> None:
        config = ParallelConfig()
        with pytest.raises(ValidationError):
            config.stagger_delay = 20  # type: ignore[misc]

    def test_cannot_set_worktree_base_dir(self) -> None:
        config = ParallelConfig()
        with pytest.raises(ValidationError):
            config.worktree_base_dir = Path("/new")  # type: ignore[misc]


# ============================================================================
# Root Config — Without parallel Section (Task 4.6)
# ============================================================================


class TestRootConfigWithoutParallel:
    """Test root Config loads cleanly without parallel section."""

    @pytest.mark.no_auto_config
    def test_config_without_parallel_defaults_to_none(self) -> None:
        _reset_config()
        config = load_config(MINIMAL_CONFIG_DATA)
        assert config.parallel is None

    @pytest.mark.no_auto_config
    def test_config_without_parallel_other_fields_valid(self) -> None:
        _reset_config()
        config = load_config(MINIMAL_CONFIG_DATA)
        assert config.providers.master.provider == "claude"
        assert config.providers.master.model == "opus"


# ============================================================================
# Root Config — With parallel Section (Task 4.7)
# ============================================================================


class TestRootConfigWithParallel:
    """Test root Config loads with parallel section and validates nested model."""

    @pytest.mark.no_auto_config
    def test_config_with_parallel_section(self) -> None:
        _reset_config()
        data = {
            **MINIMAL_CONFIG_DATA,
            "parallel": {
                "max_concurrency": 4,
                "stagger_delay": 15,
                "post_merge_fix_retries": 2,
            },
        }
        config = load_config(data)
        assert config.parallel is not None
        assert config.parallel.max_concurrency == 4
        assert config.parallel.stagger_delay == 15.0
        assert config.parallel.post_merge_fix_retries == 2
        assert config.parallel.worktree_base_dir is None

    @pytest.mark.no_auto_config
    def test_config_with_parallel_defaults(self) -> None:
        _reset_config()
        data = {
            **MINIMAL_CONFIG_DATA,
            "parallel": {},
        }
        config = load_config(data)
        assert config.parallel is not None
        assert config.parallel.max_concurrency == 3
        assert config.parallel.stagger_delay == 10.0
        assert config.parallel.post_merge_fix_retries == 1
        assert config.parallel.worktree_base_dir is None

    @pytest.mark.no_auto_config
    def test_config_with_parallel_worktree_path(self) -> None:
        _reset_config()
        data = {
            **MINIMAL_CONFIG_DATA,
            "parallel": {
                "worktree_base_dir": "/tmp/worktrees",
            },
        }
        config = load_config(data)
        assert config.parallel is not None
        assert config.parallel.worktree_base_dir == Path("/tmp/worktrees")

    @pytest.mark.no_auto_config
    def test_config_parallel_is_parallel_config_instance(self) -> None:
        _reset_config()
        data = {
            **MINIMAL_CONFIG_DATA,
            "parallel": {"max_concurrency": 2},
        }
        config = load_config(data)
        assert isinstance(config.parallel, ParallelConfig)


# ============================================================================
# Root Config — Invalid parallel Values Raises ConfigError (Task 4.8)
# ============================================================================


class TestRootConfigInvalidParallel:
    """Test root Config with invalid parallel values raises ConfigError."""

    @pytest.mark.no_auto_config
    def test_invalid_max_concurrency_raises_config_error(self) -> None:
        _reset_config()
        data = {
            **MINIMAL_CONFIG_DATA,
            "parallel": {"max_concurrency": 10},
        }
        with pytest.raises(ConfigError, match="Configuration validation failed"):
            load_config(data)

    @pytest.mark.no_auto_config
    def test_invalid_negative_stagger_delay_raises_config_error(self) -> None:
        _reset_config()
        data = {
            **MINIMAL_CONFIG_DATA,
            "parallel": {"stagger_delay": -5},
        }
        with pytest.raises(ConfigError, match="Configuration validation failed"):
            load_config(data)

    @pytest.mark.no_auto_config
    def test_invalid_negative_retries_raises_config_error(self) -> None:
        _reset_config()
        data = {
            **MINIMAL_CONFIG_DATA,
            "parallel": {"post_merge_fix_retries": -1},
        }
        with pytest.raises(ConfigError, match="Configuration validation failed"):
            load_config(data)

    @pytest.mark.no_auto_config
    def test_zero_max_concurrency_raises_config_error(self) -> None:
        _reset_config()
        data = {
            **MINIMAL_CONFIG_DATA,
            "parallel": {"max_concurrency": 0},
        }
        with pytest.raises(ConfigError, match="Configuration validation failed"):
            load_config(data)


# ============================================================================
# ParallelError — Exception Hierarchy (Task 4.9)
# ============================================================================


class TestParallelErrorHierarchy:
    """Test ParallelError inherits from BmadAssistError."""

    def test_parallel_error_inherits_from_bmad_assist_error(self) -> None:
        error = ParallelError("test error")
        assert isinstance(error, BmadAssistError)

    def test_parallel_error_is_exception(self) -> None:
        error = ParallelError("test error")
        assert isinstance(error, Exception)

    def test_parallel_error_can_be_raised_and_caught_as_bmad_error(self) -> None:
        with pytest.raises(BmadAssistError, match="parallel failure"):
            raise ParallelError("parallel failure")

    def test_parallel_error_can_be_raised_and_caught_directly(self) -> None:
        with pytest.raises(ParallelError, match="specific error"):
            raise ParallelError("specific error")

    def test_parallel_error_message(self) -> None:
        error = ParallelError("detailed message")
        assert str(error) == "detailed message"

    def test_parallel_error_isinstance_check(self) -> None:
        assert isinstance(ParallelError("msg"), BmadAssistError) is True
