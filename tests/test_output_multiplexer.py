"""Comprehensive tests for the OutputMultiplexer live output streaming."""

import asyncio
from unittest.mock import patch

from bmad_assist_lite.parallel.output import OutputMultiplexer

# ============================================================================
# TestOutputMultiplexerInit
# ============================================================================


class TestOutputMultiplexerInit:
    """Verify OutputMultiplexer.__init__ initializes correctly."""

    def test_reader_tasks_initialized_empty(self) -> None:
        """_reader_tasks dict is initialized empty."""
        mux = OutputMultiplexer()
        assert mux._reader_tasks == {}

    def test_prefix_width_is_14(self) -> None:
        """_prefix_width accommodates [ORCHESTRATOR] (14 chars)."""
        mux = OutputMultiplexer()
        assert mux._prefix_width == 14


# ============================================================================
# TestReadStream
# ============================================================================


class TestReadStream:
    """Test _read_stream async line reading and prefixing."""

    async def test_prefixes_each_line_with_story_id(self) -> None:
        """Each line from the stream is prefixed with [story_id]."""
        mux = OutputMultiplexer()
        reader = asyncio.StreamReader()
        reader.feed_data(b"Building component...\n")
        reader.feed_data(b"Running tests...\n")
        reader.feed_eof()

        written: list[str] = []
        with patch(
            "bmad_assist_lite.parallel.output.write_progress",
            side_effect=lambda line: written.append(line),
        ):
            await mux._read_stream("3.1", reader)

        assert len(written) == 2
        assert written[0].startswith("[3.1]")
        assert "Building component..." in written[0]
        assert written[1].startswith("[3.1]")
        assert "Running tests..." in written[1]

    async def test_prefix_padded_to_alignment_width(self) -> None:
        """Story ID prefix is padded to _prefix_width for alignment."""
        mux = OutputMultiplexer()
        reader = asyncio.StreamReader()
        reader.feed_data(b"test line\n")
        reader.feed_eof()

        written: list[str] = []
        with patch(
            "bmad_assist_lite.parallel.output.write_progress",
            side_effect=lambda line: written.append(line),
        ):
            await mux._read_stream("3.1", reader)

        # The output should start with [3.1] padded to 14 chars + space + content
        # "[3.1]" (5) + 9 spaces = 14 chars, then " test line"
        assert len(written) == 1
        # Extract the prefix portion (before the space + content)
        # Format is: f"{prefix} {decoded_line}" where prefix is ljust(14)
        assert written[0][:14] == "[3.1]".ljust(14)
        assert written[0][14:] == " test line"

    async def test_calls_write_progress_for_each_line(self) -> None:
        """write_progress is called once per line."""
        mux = OutputMultiplexer()
        reader = asyncio.StreamReader()
        reader.feed_data(b"line 1\n")
        reader.feed_data(b"line 2\n")
        reader.feed_data(b"line 3\n")
        reader.feed_eof()

        with patch(
            "bmad_assist_lite.parallel.output.write_progress",
        ) as mock_wp:
            await mux._read_stream("3.2", reader)

        assert mock_wp.call_count == 3

    async def test_handles_eof_cleanly(self) -> None:
        """Reader completes cleanly when stream reaches EOF."""
        mux = OutputMultiplexer()
        reader = asyncio.StreamReader()
        reader.feed_data(b"one line\n")
        reader.feed_eof()

        with patch("bmad_assist_lite.parallel.output.write_progress"):
            await mux._read_stream("3.1", reader)

        # No exception — test passes if we get here

    async def test_passes_through_empty_lines(self) -> None:
        """Empty lines are written with prefix only (not skipped)."""
        mux = OutputMultiplexer()
        reader = asyncio.StreamReader()
        reader.feed_data(b"before\n")
        reader.feed_data(b"\n")
        reader.feed_data(b"after\n")
        reader.feed_eof()

        written: list[str] = []
        with patch(
            "bmad_assist_lite.parallel.output.write_progress",
            side_effect=lambda line: written.append(line),
        ):
            await mux._read_stream("3.1", reader)

        assert len(written) == 3
        # The empty line should have the prefix and an empty string after the space
        assert written[1].startswith("[3.1]")
        # After prefix + space, the content should be empty
        prefix = "[3.1]".ljust(14)
        assert written[1] == f"{prefix} "

    async def test_decodes_non_utf8_with_replacement_chars(self) -> None:
        """Non-UTF-8 bytes are decoded with replacement characters."""
        mux = OutputMultiplexer()
        reader = asyncio.StreamReader()
        # Invalid UTF-8 sequence
        reader.feed_data(b"valid \xff\xfe invalid\n")
        reader.feed_eof()

        written: list[str] = []
        with patch(
            "bmad_assist_lite.parallel.output.write_progress",
            side_effect=lambda line: written.append(line),
        ):
            await mux._read_stream("3.1", reader)

        assert len(written) == 1
        assert "\ufffd" in written[0]  # Unicode replacement character

    async def test_exception_in_line_processing_continues_draining(self) -> None:
        """Exceptions in line processing are caught; reader continues until EOF."""
        mux = OutputMultiplexer()
        reader = asyncio.StreamReader()
        reader.feed_data(b"line 1\n")
        reader.feed_data(b"line 2\n")
        reader.feed_data(b"line 3\n")
        reader.feed_eof()

        call_count = 0

        def exploding_write(line: str) -> None:
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise RuntimeError("write_progress exploded")

        with patch(
            "bmad_assist_lite.parallel.output.write_progress",
            side_effect=exploding_write,
        ):
            # Should NOT raise — exception is caught internally
            await mux._read_stream("3.1", reader)

        # Lines 1 and 3 succeed, line 2 fails but all 3 are attempted
        assert call_count == 3

    async def test_strips_trailing_newlines(self) -> None:
        r"""Trailing \\n and \\r are stripped from decoded lines."""
        mux = OutputMultiplexer()
        reader = asyncio.StreamReader()
        reader.feed_data(b"test line\r\n")
        reader.feed_eof()

        written: list[str] = []
        with patch(
            "bmad_assist_lite.parallel.output.write_progress",
            side_effect=lambda line: written.append(line),
        ):
            await mux._read_stream("3.1", reader)

        assert not written[0].endswith("\n")
        assert not written[0].endswith("\r")

    async def test_eof_only_no_data(self) -> None:
        """Reader handles immediate EOF with no data."""
        mux = OutputMultiplexer()
        reader = asyncio.StreamReader()
        reader.feed_eof()

        with patch("bmad_assist_lite.parallel.output.write_progress") as mock_wp:
            await mux._read_stream("3.1", reader)

        mock_wp.assert_not_called()

    async def test_readline_exception_continues_draining(self) -> None:
        """Exceptions from readline (e.g. LimitOverrunError) don't crash reader."""
        mux = OutputMultiplexer()
        reader = asyncio.StreamReader()

        call_count = 0

        async def patched_readline() -> bytes:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return b"line 1\n"
            if call_count == 2:
                raise ValueError("Simulated LimitOverrunError")
            if call_count == 3:
                return b"line 3\n"
            return b""  # EOF

        reader.readline = patched_readline  # type: ignore[assignment]

        written: list[str] = []
        with patch(
            "bmad_assist_lite.parallel.output.write_progress",
            side_effect=lambda line: written.append(line),
        ):
            await mux._read_stream("3.1", reader)

        # Lines 1 and 3 should be written; line 2 raised but reader continued
        assert len(written) == 2
        assert "line 1" in written[0]
        assert "line 3" in written[1]


# ============================================================================
# TestStartReader
# ============================================================================


class TestStartReader:
    """Test start_reader creates and tracks asyncio tasks."""

    async def test_creates_asyncio_task(self) -> None:
        """start_reader returns an asyncio.Task."""
        mux = OutputMultiplexer()
        reader = asyncio.StreamReader()
        reader.feed_eof()

        with patch("bmad_assist_lite.parallel.output.write_progress"):
            task = mux.start_reader("3.1", reader)

        assert isinstance(task, asyncio.Task)
        await task  # Clean up

    async def test_stores_task_in_reader_tasks(self) -> None:
        """Task is stored in _reader_tasks dict under story_id."""
        mux = OutputMultiplexer()
        reader = asyncio.StreamReader()
        reader.feed_eof()

        with patch("bmad_assist_lite.parallel.output.write_progress"):
            task = mux.start_reader("3.1", reader)

        assert mux._reader_tasks["3.1"] is task
        await task  # Clean up

    async def test_task_has_correct_name(self) -> None:
        """Task name follows output-reader-{story_id} convention."""
        mux = OutputMultiplexer()
        reader = asyncio.StreamReader()
        reader.feed_eof()

        with patch("bmad_assist_lite.parallel.output.write_progress"):
            task = mux.start_reader("3.2", reader)

        assert task.get_name() == "output-reader-3.2"
        await task  # Clean up

    async def test_returned_task_completes_on_eof(self) -> None:
        """Returned task completes when stream reaches EOF."""
        mux = OutputMultiplexer()
        reader = asyncio.StreamReader()
        reader.feed_data(b"data\n")
        reader.feed_eof()

        with patch("bmad_assist_lite.parallel.output.write_progress"):
            task = mux.start_reader("3.1", reader)
            await task

        assert task.done()


# ============================================================================
# TestStopReader
# ============================================================================


class TestStopReader:
    """Test stop_reader cancellation and cleanup."""

    async def test_cancels_active_reader_task(self) -> None:
        """stop_reader cancels a still-running task."""
        mux = OutputMultiplexer()
        reader = asyncio.StreamReader()
        # No EOF — task will block on readline

        with patch("bmad_assist_lite.parallel.output.write_progress"):
            task = mux.start_reader("3.1", reader)
            # Give the task a chance to start
            await asyncio.sleep(0.01)

            await mux.stop_reader("3.1")

        assert task.done()
        assert "3.1" not in mux._reader_tasks

    async def test_handles_already_completed_task(self) -> None:
        """stop_reader handles task that already completed (EOF)."""
        mux = OutputMultiplexer()
        reader = asyncio.StreamReader()
        reader.feed_eof()

        with patch("bmad_assist_lite.parallel.output.write_progress"):
            task = mux.start_reader("3.1", reader)
            await task  # Wait for completion

            # Should not raise
            await mux.stop_reader("3.1")

        assert "3.1" not in mux._reader_tasks

    async def test_handles_unknown_story_id(self) -> None:
        """stop_reader for unknown story_id is a no-op (no KeyError)."""
        mux = OutputMultiplexer()

        # Should not raise
        await mux.stop_reader("nonexistent")

    async def test_removes_from_reader_tasks(self) -> None:
        """stop_reader removes the story from _reader_tasks dict."""
        mux = OutputMultiplexer()
        reader = asyncio.StreamReader()

        with patch("bmad_assist_lite.parallel.output.write_progress"):
            mux.start_reader("3.1", reader)
            await asyncio.sleep(0.01)
            await mux.stop_reader("3.1")

        assert "3.1" not in mux._reader_tasks


# ============================================================================
# TestStopAll
# ============================================================================


class TestStopAll:
    """Test stop_all cancels all reader tasks."""

    async def test_cancels_all_active_readers(self) -> None:
        """stop_all cancels all tracked reader tasks."""
        mux = OutputMultiplexer()

        readers = []
        tasks = []
        with patch("bmad_assist_lite.parallel.output.write_progress"):
            for sid in ["3.1", "3.2", "3.3"]:
                reader = asyncio.StreamReader()
                readers.append(reader)
                task = mux.start_reader(sid, reader)
                tasks.append(task)

            await asyncio.sleep(0.01)
            await mux.stop_all()

        assert mux._reader_tasks == {}
        for task in tasks:
            assert task.done()

    async def test_stop_all_with_no_readers(self) -> None:
        """stop_all with empty _reader_tasks is a no-op."""
        mux = OutputMultiplexer()

        # Should not raise
        await mux.stop_all()

        assert mux._reader_tasks == {}

    async def test_stop_all_with_mixed_done_and_active(self) -> None:
        """stop_all handles mix of completed and still-active tasks."""
        mux = OutputMultiplexer()

        # One that will complete immediately
        reader_done = asyncio.StreamReader()
        reader_done.feed_eof()

        # One that will block
        reader_active = asyncio.StreamReader()

        with patch("bmad_assist_lite.parallel.output.write_progress"):
            task_done = mux.start_reader("3.1", reader_done)
            await task_done  # Let it complete

            task_active = mux.start_reader("3.2", reader_active)
            await asyncio.sleep(0.01)

            await mux.stop_all()

        assert mux._reader_tasks == {}
        assert task_done.done()
        assert task_active.done()


# ============================================================================
# TestWriteOrchestrator
# ============================================================================


class TestWriteOrchestrator:
    """Test write_orchestrator formatting and output."""

    async def test_formats_with_orchestrator_prefix(self) -> None:
        """Message is prefixed with [ORCHESTRATOR]."""
        mux = OutputMultiplexer()

        with patch(
            "bmad_assist_lite.parallel.output.write_progress",
        ) as mock_wp:
            await mux.write_orchestrator("Story 3.1 completed successfully")

        mock_wp.assert_called_once()
        output = mock_wp.call_args[0][0]
        assert output.startswith("[ORCHESTRATOR]")
        assert "Story 3.1 completed successfully" in output

    async def test_calls_write_progress(self) -> None:
        """write_orchestrator delegates to write_progress."""
        mux = OutputMultiplexer()

        with patch(
            "bmad_assist_lite.parallel.output.write_progress",
        ) as mock_wp:
            await mux.write_orchestrator("test message")

        mock_wp.assert_called_once()


# ============================================================================
# TestPrefixAlignment
# ============================================================================


class TestPrefixAlignment:
    """Test that all prefixes align to the same width."""

    def test_orchestrator_prefix_is_14_chars(self) -> None:
        """[ORCHESTRATOR] is exactly 14 characters."""
        assert len("[ORCHESTRATOR]") == 14

    def test_story_prefix_padded_to_same_width(self) -> None:
        """Short story prefixes like [3.1] are padded to 14 chars."""
        mux = OutputMultiplexer()
        short_prefix = "[3.1]".ljust(mux._prefix_width)
        orch_prefix = "[ORCHESTRATOR]".ljust(mux._prefix_width)
        assert len(short_prefix) == len(orch_prefix)

    async def test_story_and_orchestrator_same_alignment(self) -> None:
        """Story output and orchestrator output align at the same column."""
        mux = OutputMultiplexer()

        # Capture story output
        reader = asyncio.StreamReader()
        reader.feed_data(b"story text\n")
        reader.feed_eof()

        story_output: list[str] = []
        with patch(
            "bmad_assist_lite.parallel.output.write_progress",
            side_effect=lambda line: story_output.append(line),
        ):
            await mux._read_stream("3.1", reader)

        # Capture orchestrator output
        orch_output: list[str] = []
        with patch(
            "bmad_assist_lite.parallel.output.write_progress",
            side_effect=lambda line: orch_output.append(line),
        ):
            await mux.write_orchestrator("orch text")

        # Both should have the message starting at the same column
        # Prefix is ljust(14) + " " = 15 chars before message
        assert story_output[0].index("story text") == orch_output[0].index("orch text")

    def test_long_story_id_prefix(self) -> None:
        """Longer story IDs like [10.1] still get padded."""
        mux = OutputMultiplexer()
        prefix = "[10.1]".ljust(mux._prefix_width)
        assert len(prefix) == 14


# ============================================================================
# TestConcurrentReaders
# ============================================================================


class TestConcurrentReaders:
    """Test multiple concurrent readers interleaving output."""

    async def test_two_readers_interleave_without_corruption(self) -> None:
        """Two concurrent readers produce correctly prefixed output."""
        mux = OutputMultiplexer()

        reader1 = asyncio.StreamReader()
        reader2 = asyncio.StreamReader()

        written: list[str] = []
        with patch(
            "bmad_assist_lite.parallel.output.write_progress",
            side_effect=lambda line: written.append(line),
        ):
            task1 = mux.start_reader("3.1", reader1)
            task2 = mux.start_reader("3.2", reader2)

            # Interleave data
            reader1.feed_data(b"from story 3.1 line 1\n")
            await asyncio.sleep(0.01)
            reader2.feed_data(b"from story 3.2 line 1\n")
            await asyncio.sleep(0.01)
            reader1.feed_data(b"from story 3.1 line 2\n")
            await asyncio.sleep(0.01)
            reader2.feed_data(b"from story 3.2 line 2\n")
            await asyncio.sleep(0.01)

            reader1.feed_eof()
            reader2.feed_eof()

            await task1
            await task2

        # All 4 lines should be present
        assert len(written) == 4

        # Verify every line has a valid prefix and correct content
        story_31_lines = [line for line in written if line.startswith("[3.1]")]
        story_32_lines = [line for line in written if line.startswith("[3.2]")]
        assert len(story_31_lines) == 2, f"Expected 2 lines from 3.1, got {story_31_lines}"
        assert len(story_32_lines) == 2, f"Expected 2 lines from 3.2, got {story_32_lines}"

        # Every line must start with a valid prefix (no corruption)
        for line in written:
            assert line.startswith("[3.1]") or line.startswith("[3.2]"), (
                f"Corrupted prefix: {line!r}"
            )

    async def test_many_readers_all_produce_output(self) -> None:
        """Multiple concurrent readers all produce prefixed output."""
        mux = OutputMultiplexer()
        story_ids = ["3.1", "3.2", "3.3", "3.4", "3.5"]

        written: list[str] = []
        with patch(
            "bmad_assist_lite.parallel.output.write_progress",
            side_effect=lambda line: written.append(line),
        ):
            tasks = []
            readers = []
            for sid in story_ids:
                reader = asyncio.StreamReader()
                reader.feed_data(f"output from {sid}\n".encode())
                reader.feed_eof()
                readers.append(reader)
                task = mux.start_reader(sid, reader)
                tasks.append(task)

            await asyncio.gather(*tasks)

        assert len(written) == 5
        # Each story should have exactly one line
        for sid in story_ids:
            prefix = f"[{sid}]"
            matching = [line for line in written if line.startswith(prefix)]
            assert len(matching) == 1
