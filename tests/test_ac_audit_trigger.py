"""Tests for the goal-run11 AC-completeness audit auto-trigger.

The auto-trigger lets the tool decide, per story, whether the ~$2 audit lane is
worth running, from worktree-local structural signals. These tests pin:

- the pure decision core (:func:`decide_from_signals`) — every fire path and the
  quiet baseline, plus the fire-when-uncertain bias;
- the pure signal extractors (:func:`_count_acs`, :func:`extract_story_section`,
  :func:`_doc_markers`, :func:`_diff_spread`) that Phase-2 calibration relies on;
- the resolver's three states — force-ON wins, both-off is inert, auto decides.
"""

from bmad_assist_lite.core.config import load_config
from bmad_assist_lite.core.state import State
from bmad_assist_lite.loop.handlers.ac_audit_trigger import (
    FIRE_MIN_AC_COUNT,
    FIRE_MIN_CHANGED_DIRS,
    FIRE_MIN_CHANGED_FILES,
    FIRE_MIN_DOC_MARKERS,
    AuditTriggerSignals,
    _count_acs,
    _diff_spread,
    _doc_markers,
    decide_from_signals,
    extract_story_section,
    gather_audit_signals,
    resolve_ac_audit_enabled,
)


def _sig(**kw) -> AuditTriggerSignals:
    base = {
        "story_ac_count": 1,
        "cross_ac_markers": 0,
        "doc_marker_hits": 0,
        "diff_file_count": 1,
        "diff_dir_count": 1,
        "dev_attempt": 0,
        "review_iteration": 0,
        "signals_available": True,
    }
    base.update(kw)
    return AuditTriggerSignals(**base)


def _config(enabled: bool = False, auto: bool = False):
    return load_config(
        {
            "providers": {
                "master": {"provider": "claude", "model": "opus", "effort": "high"},
                "multi": [{"provider": "claude", "model": "sonnet"}],
            },
            "ac_audit": {"enabled": enabled, "auto": auto},
        }
    )


# ============================================================================
# Decision core
# ============================================================================


class TestDecideFromSignals:
    def test_quiet_baseline(self):
        fire, reason = decide_from_signals(_sig())
        assert fire is False
        assert reason.startswith("quiet(")

    def test_dev_retry_always_fires(self):
        fire, reason = decide_from_signals(_sig(dev_attempt=1))
        assert fire is True
        assert "escalation" in reason

    def test_re_review_always_fires(self):
        fire, reason = decide_from_signals(_sig(review_iteration=1))
        assert fire is True
        assert "escalation" in reason

    def test_cross_boundary_ac_fires(self):
        """The run9 class: a single cross-screen/handoff AC is enough."""
        fire, reason = decide_from_signals(_sig(cross_ac_markers=1))
        assert fire is True
        assert "cross_ac_markers" in reason

    def test_high_ac_load_fires(self):
        fire, reason = decide_from_signals(_sig(story_ac_count=FIRE_MIN_AC_COUNT))
        assert fire is True
        assert "ac_count" in reason

    def test_high_ac_load_below_threshold_quiet(self):
        fire, _ = decide_from_signals(_sig(story_ac_count=FIRE_MIN_AC_COUNT - 1))
        assert fire is False

    def test_doc_markers_fire(self):
        fire, reason = decide_from_signals(_sig(doc_marker_hits=FIRE_MIN_DOC_MARKERS))
        assert fire is True
        assert "doc_markers" in reason

    def test_broad_diff_by_files_fires(self):
        fire, reason = decide_from_signals(_sig(diff_file_count=FIRE_MIN_CHANGED_FILES))
        assert fire is True
        assert "diff_spread" in reason

    def test_broad_diff_by_dirs_fires(self):
        fire, reason = decide_from_signals(_sig(diff_dir_count=FIRE_MIN_CHANGED_DIRS))
        assert fire is True
        assert "diff_spread" in reason

    def test_unavailable_signals_fire_when_uncertain(self):
        """LOAD-BEARING: an unreadable diff/epic must fire, never silently skip."""
        fire, reason = decide_from_signals(_sig(signals_available=False))
        assert fire is True
        assert "uncertain" in reason


# ============================================================================
# Pure extractors (the calibration foundation)
# ============================================================================

_MINI_EPIC = """\
## Epic 5: things

### Story 5.1: Alpha (S-12) on fixtures

**Covers:**
- screens: S-12

**Acceptance Criteria:**
- AC-1: Given X, When Y, Then Z.
- AC-2: Given a, Then b.

**Dependencies:** None

### Story 5.2: Brand hub (S-13) on fixtures

**Covers:**
- screens: S-13

**Acceptance Criteria:**
- AC-1: Given `DATA_MODE=fixtures`, Then all elements render.
- AC-4: Given the generate-from-keyword button, When a keyword is selected,
  Then navigation threads `keyword_id` into the S-03 route (never re-typed).

**Dependencies:** Story 5.1

#### Operator checkpoint
- [ ] 1. do a thing
"""


class TestExtractors:
    def test_extract_story_section_isolates_one_story(self):
        sec = extract_story_section(_MINI_EPIC, "5.1")
        assert "Story 5.1: Alpha" in sec
        assert "Story 5.2" not in sec
        # the operator checkpoint (a #### heading) must not leak into 5.2 either
        sec2 = extract_story_section(_MINI_EPIC, "5.2")
        assert "Operator checkpoint" not in sec2

    def test_extract_missing_story_returns_empty(self):
        assert extract_story_section(_MINI_EPIC, "5.9") == ""
        assert extract_story_section("", "5.1") == ""

    def test_count_acs_plain_bullets(self):
        """The shard writes `- AC-N:`; the parser's checkbox ac_count is 0 here."""
        ac_count, cross = _count_acs(extract_story_section(_MINI_EPIC, "5.1"))
        assert ac_count == 2
        assert cross == 0  # both ACs are same-screen, no handoff verbs

    def test_count_acs_detects_cross_screen_handoff(self):
        """5.2 AC-4 threads keyword_id into S-03 (its own screen is S-13)."""
        ac_count, cross = _count_acs(extract_story_section(_MINI_EPIC, "5.2"))
        assert ac_count == 2
        assert cross >= 1  # AC-4 both names S-03 (!= S-13) and says "threads"

    def test_doc_markers_counts_distinct_terms(self):
        text = "The Dependency Sweep found a consumer; searchParams reads it."
        hits, found = _doc_markers(text)
        assert hits >= 3
        assert "dependency sweep" in found

    def test_doc_markers_empty(self):
        assert _doc_markers("") == (0, ())

    def test_diff_spread_counts_files_and_dirs(self):
        files = [
            "apps/studio/app/(dev)/dev/s13/page.tsx",
            "apps/studio/app/(dev)/dev/s13/form.tsx",
            "apps/studio/components/x.tsx",
            "packages/data-access/src/y.ts",
        ]
        n_files, n_dirs, sample = _diff_spread(files)
        assert n_files == 4
        assert n_dirs == 3  # two files share the s13 dir
        assert len(sample) == 3


# ============================================================================
# Resolver — three states
# ============================================================================


class TestResolver:
    def test_forced_on_short_circuits(self):
        """enabled=true wins and gathers no signals."""
        d = resolve_ac_audit_enabled(_config(enabled=True, auto=True), State())
        assert d.fire is True
        assert d.mode == "forced_on"
        assert d.signals is None

    def test_both_off_is_inert(self):
        d = resolve_ac_audit_enabled(_config(enabled=False, auto=False), State())
        assert d.fire is False
        assert d.mode == "off"
        assert d.signals is None

    def test_auto_fires_when_uncertain(self, tmp_path):
        """auto=true over a non-git tmp dir -> signals unavailable -> fire."""
        state = State(current_epic=5, current_story="5.2")
        d = resolve_ac_audit_enabled(_config(auto=True), state, tmp_path)
        assert d.mode == "auto"
        assert d.fire is True
        assert d.signals is not None
        assert d.signals.signals_available is False

    def test_gather_with_overrides_is_deterministic(self):
        """Overrides bypass git/fs so Phase-2 calibration is reproducible."""
        state = State(current_epic=5, current_story="5.2")
        sig = gather_audit_signals(
            _config(auto=True),
            state,
            project_path=None,  # unused: all three inputs overridden
            epic_text=_MINI_EPIC,
            story_doc_text="a Dependency Sweep and a consumer",
            changed_files=["a/b/c.ts", "a/b/d.ts", "e/f.ts"],
        )
        assert sig.signals_available is True
        assert sig.story_ac_count == 2
        assert sig.cross_ac_markers >= 1
        assert sig.doc_marker_hits >= 2
        assert sig.diff_file_count == 3
        assert sig.diff_dir_count == 2
        fire, _ = decide_from_signals(sig)
        assert fire is True


# ============================================================================
# Config
# ============================================================================


class TestConfig:
    def test_auto_defaults_off(self):
        assert _config().ac_audit.auto is False
        assert _config().ac_audit.enabled is False

    def test_enabled_wins_over_auto(self):
        cfg = _config(enabled=True, auto=True)
        assert cfg.ac_audit.enabled is True
        assert cfg.ac_audit.auto is True
        assert resolve_ac_audit_enabled(cfg, State()).mode == "forced_on"
