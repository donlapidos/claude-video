#!/usr/bin/env python3
"""Parse a WebVTT subtitle file into a clean, timestamped transcript.

YouTube auto-subs emit rolling-duplicate cues (each line appears 2-3 times as it
scrolls). We dedupe consecutive identical cues and merge their time ranges.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path


TS_RE = re.compile(
    r"(\d{2}):(\d{2}):(\d{2})[.,](\d{3})\s+-->\s+(\d{2}):(\d{2}):(\d{2})[.,](\d{3})"
)
TAG_RE = re.compile(r"<[^>]+>")
# WebVTT voice span: `<v Alex Chen>text</v>`. Teams and Meet use these, and the
# speaker name is worth keeping - so lift it out before the generic tag strip
# below removes the whole span.
VOICE_RE = re.compile(r"<v(?:\.[^\s>]+)*\s+([^>]+?)\s*>", re.IGNORECASE)


def _to_seconds(h: str, m: str, s: str, ms: str) -> float:
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0


def parse_vtt(path: str) -> list[dict]:
    """Parse WebVTT, or SRT - the cue-timing regex accepts either separator and
    SRT's index lines simply do not match, so they are skipped."""
    # utf-8-sig also copes with the BOM some tools write.
    text = Path(path).read_text(encoding="utf-8-sig", errors="ignore")
    lines = text.splitlines()

    segments: list[dict] = []
    i = 0
    while i < len(lines):
        match = TS_RE.match(lines[i])
        if not match:
            i += 1
            continue

        start = _to_seconds(*match.groups()[:4])
        end = _to_seconds(*match.groups()[4:])
        i += 1

        cue_lines: list[str] = []
        while i < len(lines) and lines[i].strip():
            raw_line = VOICE_RE.sub(lambda m: m.group(1).strip() + ": ", lines[i])
            cleaned = TAG_RE.sub("", raw_line).strip()
            if cleaned:
                cue_lines.append(cleaned)
            i += 1

        cue_text = " ".join(cue_lines).strip()
        if cue_text:
            segments.append({"start": round(start, 2), "end": round(end, 2), "text": cue_text})
        i += 1

    return _dedupe(segments)


# YouTube's auto-captions arrive as an overlapping stream: a settled line, then
# that line plus the next one mid-typing, then the next line on its own, and so
# on. Every cue therefore repeats words already shown. We keep a short window of
# words already emitted and drop any leading run of a cue that simply repeats
# it, so each line survives exactly once with its own timestamp.
DEDUP_TAIL_WORDS = 40
# Genuine rolling overlap is a whole caption line (5-10 words). Requiring three
# keeps a real repetition that happens to straddle a line break ("and then" /
# "and then everything broke") from being silently trimmed.
MIN_OVERLAP_WORDS = 3


def _leading_overlap(tail: list[str], words: list[str]) -> int:
    """Length of the longest run of `words` that repeats the end of `tail`."""
    # A cue wholly contained in what we already emitted is duplication by
    # definition, however short - this is the "settled line" cue in a rolling
    # stream, and the plain repeated cue. Checked before the word threshold.
    if words and len(words) <= len(tail) and tail[-len(words):] == words:
        return len(words)
    limit = min(len(tail), len(words), DEDUP_TAIL_WORDS)
    for size in range(limit, MIN_OVERLAP_WORDS - 1, -1):
        if tail[-size:] == words[:size]:
            return size
    return 0


def _dedupe(segments: list[dict]) -> list[dict]:
    """Collapse the overlap in rolling/scrolling caption streams.

    Emits only words not already emitted. A cue that is wholly a repeat extends
    the previous segment's time range instead of adding a duplicate line.
    """
    out: list[dict] = []
    tail: list[str] = []
    for seg in segments:
        words = seg["text"].split()
        if not words:
            continue
        fresh = words[_leading_overlap(tail, words):]
        if not fresh:
            if out:
                out[-1]["end"] = seg["end"]
            continue
        merged = dict(seg)
        merged["text"] = " ".join(fresh)
        out.append(merged)
        tail = (tail + fresh)[-DEDUP_TAIL_WORDS:]
    return out


def filter_range(
    segments: list[dict],
    start_seconds: float | None,
    end_seconds: float | None,
) -> list[dict]:
    """Return segments whose time range overlaps [start, end]."""
    if start_seconds is None and end_seconds is None:
        return segments
    lo = start_seconds if start_seconds is not None else float("-inf")
    hi = end_seconds if end_seconds is not None else float("inf")
    return [seg for seg in segments if seg["end"] >= lo and seg["start"] <= hi]


def format_transcript(segments: list[dict]) -> str:
    lines = []
    for seg in segments:
        start = int(seg["start"])
        stamp = f"[{start // 60:02d}:{start % 60:02d}]"
        lines.append(f"{stamp} {seg['text']}")
    return "\n".join(lines)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: transcribe.py <vtt-path>", file=sys.stderr)
        raise SystemExit(2)
    print(format_transcript(parse_vtt(sys.argv[1])))
