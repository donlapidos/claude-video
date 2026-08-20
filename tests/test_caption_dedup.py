"""Rolling-caption dedup and SessionStart-hook hygiene.

YouTube auto-captions scroll: cues alternate between a settled line and that
same line plus the next one mid-typing, so every cue repeats words already
shown. On a real 7-minute video that inflates the transcript about 3x (4909
words of cue text for 1642 words actually spoken).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "watch" / "scripts"
HOOK = ROOT / "hooks" / "scripts" / "check-setup.sh"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import transcribe  # noqa: E402


def _seg(start, end, text):
    return {"start": start, "end": end, "text": text}


class TestRollingOverlap:
    # Shape taken from a real youtu.be auto-caption track.
    ROLLING = [
        _seg(301.0, 302.0, "basis instead of subscribing to something like Hexfield. But, I'm just"),
        _seg(302.0, 304.0, "something like Hexfield. But, I'm just asking Claude Code here to generate a"),
        _seg(304.0, 306.0, "asking Claude Code here to generate a relevant video for the site and place it"),
    ]

    def test_repeated_words_appear_once(self):
        text = transcribe.format_transcript(transcribe._dedupe(self.ROLLING))
        assert text.count("asking Claude Code here to generate a") == 1
        assert text.count("something like Hexfield") == 1

    def test_each_cue_keeps_its_own_timestamp(self):
        # Merging everything into one segment would lose the ability to cite.
        out = transcribe._dedupe(self.ROLLING)
        assert [int(s["start"]) for s in out] == [301, 302, 304]

    def test_no_content_is_lost(self):
        out = transcribe._dedupe(self.ROLLING)
        joined = " ".join(s["text"] for s in out)
        for phrase in ("basis instead of subscribing", "relevant video for the site"):
            assert phrase in joined

    def test_transcript_is_substantially_shorter(self):
        naive = sum(len(s["text"]) for s in self.ROLLING)
        deduped = sum(len(s["text"]) for s in transcribe._dedupe(self.ROLLING))
        assert deduped < naive * 0.75


class TestNonRollingUnaffected:
    def test_distinct_lines_pass_through(self):
        segs = [_seg(0, 1, "First distinct line."), _seg(1, 2, "Second distinct line.")]
        assert transcribe._dedupe(segs) == segs

    def test_cue_that_grows_in_place_emits_only_the_new_words(self):
        # "A" then "A B C D": the second cue contributes only what is new, so
        # the words appear once across the transcript.
        segs = [_seg(0, 1, "Hello there my friend"), _seg(0, 2, "Hello there my friend indeed")]
        out = transcribe._dedupe(segs)
        joined = " ".join(s["text"] for s in out)
        assert joined == "Hello there my friend indeed"
        assert joined.count("Hello there") == 1

    def test_exact_duplicate_extends_range(self):
        segs = [_seg(0, 1, "same line"), _seg(1, 2, "same line")]
        out = transcribe._dedupe(segs)
        assert len(out) == 1 and out[0]["end"] == 2

    def test_short_common_phrase_is_not_treated_as_overlap(self):
        # Only a 2-word tail matches, below MIN_OVERLAP_WORDS, so a genuine
        # repetition straddling a line break survives intact.
        segs = [_seg(0, 1, "we shipped it and then"), _seg(1, 2, "and then everything broke")]
        out = transcribe._dedupe(segs)
        assert out[1]["text"] == "and then everything broke"


class TestRealCueStreamShape:
    """The shape that actually comes off YouTube.

    Cues alternate: a settled line, then that line plus the next mid-typing,
    then the next line settled, and so on. A dedup that assumes only
    "cue grew in place" collapses this whole stream into one segment and loses
    text, which is why this fixture mirrors the real interleaving.
    """

    VTT = """WEBVTT
Kind: captions
Language: en

00:00:00.000 --> 00:00:01.510
So, on topic just launched an official

00:00:01.510 --> 00:00:03.910
So, on topic just launched an official
{slash} design skill for Cloud Code. And

00:00:03.910 --> 00:00:03.920
{slash} design skill for Cloud Code. And

00:00:03.920 --> 00:00:05.630
{slash} design skill for Cloud Code. And
what it basically does is it brings

00:00:05.630 --> 00:00:05.640
what it basically does is it brings

00:00:05.640 --> 00:00:07.670
what it basically does is it brings
Cloud Design's really popular artboard
"""

    LINES = [
        "So, on topic just launched an official",
        "{slash} design skill for Cloud Code. And",
        "what it basically does is it brings",
        "Cloud Design's really popular artboard",
    ]

    @pytest.fixture()
    def segments(self, tmp_path: Path):
        vtt = tmp_path / "video.en.vtt"
        vtt.write_text(self.VTT, encoding="utf-8")
        return transcribe.parse_vtt(str(vtt))

    def test_every_spoken_line_survives_exactly_once(self, segments):
        flow = " ".join(s["text"] for s in segments)
        for line in self.LINES:
            assert flow.count(line) == 1, f"{line!r} appears {flow.count(line)}x"

    def test_nothing_collapses_into_a_single_segment(self, segments):
        # The regression this guards: one segment holding only the last pair.
        assert len(segments) >= len(self.LINES)

    def test_no_word_is_dropped(self, segments):
        spoken = set(" ".join(self.LINES).split())
        emitted = set(" ".join(s["text"] for s in segments).split())
        assert not (spoken - emitted)

    def test_timestamps_advance_with_the_lines(self, segments):
        starts = [s["start"] for s in segments]
        assert starts == sorted(starts)
        assert starts[0] < starts[-1]


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash not available")
class TestSessionStartHook:
    def _run(self, env_text: str, extra_env: dict | None = None):
        home = Path(tempfile.mkdtemp())
        (home / ".config" / "watch").mkdir(parents=True)
        (home / ".config" / "watch" / ".env").write_text(env_text, encoding="utf-8")
        env = dict(os.environ)
        env["HOME"] = str(home)
        if extra_env:
            env.update(extra_env)
        try:
            return subprocess.run(
                ["bash", str(HOOK)],
                capture_output=True, text=True, env=env, timeout=60,
            )
        finally:
            shutil.rmtree(home, ignore_errors=True)

    SECRET = "gsk_thisisatestsecretvalue000"

    def test_key_value_never_reaches_output(self):
        r = self._run(f"GROQ_API_KEY={self.SECRET}\nSETUP_COMPLETE=true\n")
        assert self.SECRET not in r.stdout
        assert self.SECRET not in r.stderr

    def test_hook_source_does_not_print_key_values(self):
        src = HOOK.read_text(encoding="utf-8")
        # presence-only helper, and no `echo "${!name}"` style value echo
        assert "has_key()" in src
        assert 'echo "${!name}"' not in src
        assert "read_key" not in src

    def test_no_permission_warning_on_windows_style_modes(self):
        # The POSIX mode check is skipped on Git Bash/MSYS, where an ordinary
        # file always reports 0666 and would warn on every session.
        r = self._run("GROQ_API_KEY=\nOPENAI_API_KEY=\n")
        if sys.platform.startswith("win"):
            assert "permissions" not in r.stdout.lower()

    def test_exits_cleanly(self):
        r = self._run("GROQ_API_KEY=\nOPENAI_API_KEY=\n")
        assert r.returncode == 0
