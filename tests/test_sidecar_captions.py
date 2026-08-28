"""Caption files sitting next to a local video.

Teams, Zoom and Meet all export a .vtt or .srt alongside the recording, so for
an uploaded video there is usually an accurate, speaker-attributed transcript
already on disk. Using it means no Whisper key is needed and no audio is sent
to a third-party API.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "skills" / "watch" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import download  # noqa: E402
import transcribe  # noqa: E402

WATCH = SCRIPTS / "watch.py"

TEAMS_VTT = """WEBVTT

00:00:00.000 --> 00:00:03.000
<v Alex Chen>Welcome to the quarterly review.</v>

00:00:03.000 --> 00:00:06.000
<v Dana Whitfield>Pipeline forecast is first on the agenda.</v>
"""

ZOOM_VTT = """WEBVTT

1
00:00:00.000 --> 00:00:03.000
Dana Whitfield: Starting the recording now.

2
00:00:03.000 --> 00:00:06.000
Alex Chen: Thanks, go ahead.
"""

SRT = """1
00:00:00,000 --> 00:00:02,500
First subtitle line.

2
00:00:02,500 --> 00:00:05,000
Second subtitle line.
"""


def _video(dir_: Path, name: str) -> Path:
    """A stand-in video file; discovery only looks at names."""
    path = dir_ / name
    path.write_bytes(b"\x00")
    return path


class TestPairing:
    def test_exact_stem_vtt(self, tmp_path: Path):
        video = _video(tmp_path, "Recording.mp4")
        (tmp_path / "Recording.vtt").write_text(TEAMS_VTT, encoding="utf-8")
        assert download.find_sidecar_subtitle(video).name == "Recording.vtt"

    def test_dotted_variant(self, tmp_path: Path):
        # Zoom: <stem>.transcript.vtt
        video = _video(tmp_path, "GMT20260828-Quarterly.mp4")
        (tmp_path / "GMT20260828-Quarterly.transcript.vtt").write_text(ZOOM_VTT, encoding="utf-8")
        assert download.find_sidecar_subtitle(video).name.endswith(".transcript.vtt")

    def test_srt_is_accepted(self, tmp_path: Path):
        video = _video(tmp_path, "meeting.mp4")
        (tmp_path / "meeting.srt").write_text(SRT, encoding="utf-8")
        assert download.find_sidecar_subtitle(video).name == "meeting.srt"

    def test_vtt_preferred_over_srt(self, tmp_path: Path):
        video = _video(tmp_path, "call.mp4")
        (tmp_path / "call.srt").write_text(SRT, encoding="utf-8")
        (tmp_path / "call.vtt").write_text(TEAMS_VTT, encoding="utf-8")
        assert download.find_sidecar_subtitle(video).suffix == ".vtt"

    def test_exact_preferred_over_tagged(self, tmp_path: Path):
        video = _video(tmp_path, "call.mp4")
        (tmp_path / "call.en.vtt").write_text(TEAMS_VTT, encoding="utf-8")
        (tmp_path / "call.vtt").write_text(TEAMS_VTT, encoding="utf-8")
        assert download.find_sidecar_subtitle(video).name == "call.vtt"

    def test_english_preferred_among_tagged(self, tmp_path: Path):
        video = _video(tmp_path, "call.mp4")
        (tmp_path / "call.de.vtt").write_text(TEAMS_VTT, encoding="utf-8")
        (tmp_path / "call.en.vtt").write_text(TEAMS_VTT, encoding="utf-8")
        assert download.find_sidecar_subtitle(video).name == "call.en.vtt"

    def test_similarly_named_file_is_not_paired(self, tmp_path: Path):
        # `meeting-2.vtt` belongs to a different recording; requiring the dot
        # separator keeps it from being picked up.
        video = _video(tmp_path, "meeting.mp4")
        (tmp_path / "meeting-2.vtt").write_text(TEAMS_VTT, encoding="utf-8")
        assert download.find_sidecar_subtitle(video) is None

    def test_no_sidecar_returns_none(self, tmp_path: Path):
        assert download.find_sidecar_subtitle(_video(tmp_path, "alone.mp4")) is None

    def test_unrelated_subtitle_ignored(self, tmp_path: Path):
        video = _video(tmp_path, "alone.mp4")
        (tmp_path / "somethingelse.vtt").write_text(TEAMS_VTT, encoding="utf-8")
        assert download.find_sidecar_subtitle(video) is None

    def test_resolve_local_exposes_the_sidecar(self, tmp_path: Path):
        video = _video(tmp_path, "Recording.mp4")
        (tmp_path / "Recording.vtt").write_text(TEAMS_VTT, encoding="utf-8")
        assert download.resolve_local(str(video))["subtitle_path"].endswith("Recording.vtt")

    def test_resolve_local_without_sidecar_is_unchanged(self, tmp_path: Path):
        video = _video(tmp_path, "Recording.mp4")
        assert download.resolve_local(str(video))["subtitle_path"] is None


class TestSpeakerNames:
    def test_voice_span_becomes_a_speaker_prefix(self, tmp_path: Path):
        vtt = tmp_path / "t.vtt"
        vtt.write_text(TEAMS_VTT, encoding="utf-8")
        text = transcribe.format_transcript(transcribe.parse_vtt(str(vtt)))
        assert "Alex Chen: Welcome to the quarterly review." in text
        assert "Dana Whitfield: Pipeline forecast" in text

    def test_voice_span_with_classes(self, tmp_path: Path):
        vtt = tmp_path / "t.vtt"
        vtt.write_text(
            "WEBVTT\n\n00:00:00.000 --> 00:00:02.000\n"
            "<v.first.loud Sam Ortiz>Morning all.</v>\n",
            encoding="utf-8",
        )
        assert "Sam Ortiz: Morning all." in transcribe.format_transcript(
            transcribe.parse_vtt(str(vtt)))

    def test_no_stray_tag_remnants(self, tmp_path: Path):
        vtt = tmp_path / "t.vtt"
        vtt.write_text(TEAMS_VTT, encoding="utf-8")
        text = transcribe.format_transcript(transcribe.parse_vtt(str(vtt)))
        assert "<" not in text and ">" not in text

    def test_youtube_timing_tags_still_stripped(self, tmp_path: Path):
        # The generic tag strip must keep working for auto-caption timing spans.
        vtt = tmp_path / "t.vtt"
        vtt.write_text(
            "WEBVTT\n\n00:00:00.000 --> 00:00:02.000\n"
            "hello<00:00:00.280><c> there</c>\n",
            encoding="utf-8",
        )
        text = transcribe.format_transcript(transcribe.parse_vtt(str(vtt)))
        assert text.endswith("hello there")


class TestSrtParsing:
    def test_comma_separators_and_index_lines(self, tmp_path: Path):
        srt = tmp_path / "t.srt"
        srt.write_text(SRT, encoding="utf-8")
        segs = transcribe.parse_vtt(str(srt))
        assert [s["text"] for s in segs] == ["First subtitle line.", "Second subtitle line."]
        assert segs[1]["start"] == pytest.approx(2.5)

    def test_byte_order_mark_is_tolerated(self, tmp_path: Path):
        srt = tmp_path / "bom.srt"
        srt.write_text(SRT, encoding="utf-8-sig")
        assert len(transcribe.parse_vtt(str(srt))) == 2


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg required")
class TestEndToEnd:
    def test_local_video_uses_sidecar_without_whisper(self, static_clip: Path, tmp_path: Path):
        video = tmp_path / "Recording.mp4"
        shutil.copy(static_clip, video)
        (tmp_path / "Recording.vtt").write_text(TEAMS_VTT, encoding="utf-8")

        proc = subprocess.run(
            [sys.executable, str(WATCH), str(video), "--detail", "balanced",
             "--max-frames", "2", "--no-whisper",
             "--out-dir", str(tmp_path / "work")],
            capture_output=True, text=True, timeout=600,
        )
        assert proc.returncode == 0, proc.stderr
        # a real transcript, with no API key and no audio upload
        assert "via captions" in proc.stdout
        assert "Alex Chen: Welcome to the quarterly review." in proc.stdout
        assert "No transcript available" not in proc.stdout
        # and it is still wrapped as untrusted content
        assert "UNTRUSTED-TRANSCRIPT-" in proc.stdout

    def test_local_video_without_sidecar_still_reports_cleanly(self, static_clip: Path, tmp_path: Path):
        video = tmp_path / "Solo.mp4"
        shutil.copy(static_clip, video)
        proc = subprocess.run(
            [sys.executable, str(WATCH), str(video), "--detail", "efficient",
             "--max-frames", "2", "--no-whisper",
             "--out-dir", str(tmp_path / "work")],
            capture_output=True, text=True, timeout=600,
        )
        assert proc.returncode == 0, proc.stderr
        assert "Transcript:** none available" in proc.stdout
