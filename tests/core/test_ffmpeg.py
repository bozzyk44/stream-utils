"""Tests for FFmpeg helpers.

Pure-logic tests (SRT formatting, filter-string building, force_style
serialization) run unconditionally. End-to-end ``cut_vertical`` integration
is gated by ``ffmpeg_available()`` — skipped if ffmpeg isn't on PATH.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from stream_utils import (
    FFmpegError,
    Segment,
    SubtitleStyle,
    cut_vertical,
    ffmpeg_available,
    segments_to_srt,
    write_srt,
)
from stream_utils.core.ffmpeg import _build_video_filter, _format_srt_time


def test_format_srt_time_zero() -> None:
    assert _format_srt_time(0) == "00:00:00,000"


def test_format_srt_time_basic() -> None:
    assert _format_srt_time(3.5) == "00:00:03,500"


def test_format_srt_time_minutes() -> None:
    assert _format_srt_time(125.123) == "00:02:05,123"


def test_format_srt_time_hours() -> None:
    assert _format_srt_time(3661.001) == "01:01:01,001"


def test_format_srt_time_negative_clamps_to_zero() -> None:
    assert _format_srt_time(-1.5) == "00:00:00,000"


def test_segments_to_srt_basic() -> None:
    segs = [
        Segment(start=0.0, end=2.5, text="Привет"),
        Segment(start=2.5, end=5.0, text="мир"),
    ]
    out = segments_to_srt(segs)
    assert "1\n00:00:00,000 --> 00:00:02,500\nПривет\n" in out  # noqa: RUF001
    assert "2\n00:00:02,500 --> 00:00:05,000\nмир\n" in out  # noqa: RUF001


def test_segments_to_srt_skips_empty_text() -> None:
    segs = [
        Segment(start=0.0, end=1.0, text=""),
        Segment(start=1.0, end=2.0, text="real"),
        Segment(start=2.0, end=3.0, text="   "),
    ]
    out = segments_to_srt(segs)
    assert "real" in out
    # The remaining segment should be index 1 (others skipped).
    assert out.startswith("1\n")
    assert "2\n" not in out


def test_segments_to_srt_skips_zero_length() -> None:
    segs = [
        Segment(start=1.0, end=1.0, text="zero-length"),
        Segment(start=1.0, end=2.0, text="real"),
    ]
    out = segments_to_srt(segs)
    assert "zero-length" not in out
    assert "real" in out


def test_segments_to_srt_empty_list() -> None:
    assert segments_to_srt([]) == ""


def test_segments_to_srt_time_offset() -> None:
    """Re-zero a clip extracted from a longer source."""
    segs = [
        Segment(start=100.0, end=102.5, text="line1"),
        Segment(start=102.5, end=104.0, text="line2"),
    ]
    out = segments_to_srt(segs, time_offset=100.0)
    assert "00:00:00,000 --> 00:00:02,500" in out
    assert "00:00:02,500 --> 00:00:04,000" in out


def test_segments_to_srt_time_offset_clamps_negative() -> None:
    segs = [Segment(start=0.5, end=2.0, text="line")]
    out = segments_to_srt(segs, time_offset=1.0)
    # Start clamps to 0; end is 1.0 → 0 < 1 so segment kept.
    assert "00:00:00,000 --> 00:00:01,000" in out


def test_write_srt_creates_file_and_dirs(tmp_path: Path) -> None:
    segs = [Segment(start=0.0, end=1.0, text="hello")]
    out = write_srt(segs, tmp_path / "subdir/out.srt")
    assert out.is_file()
    content = out.read_text(encoding="utf-8")
    assert "hello" in content


def test_subtitle_style_default_force_style() -> None:
    style = SubtitleStyle()
    s = style.to_force_style()
    assert "FontName=Inter" in s
    assert "FontSize=18" in s
    assert "PrimaryColour=&HFFFFFF&" in s
    assert "Outline=3" in s
    assert "Alignment=2" in s
    assert "MarginV=120" in s


def test_subtitle_style_override() -> None:
    style = SubtitleStyle(font_name="Manrope", font_size=24, margin_v=80)
    s = style.to_force_style()
    assert "FontName=Manrope" in s
    assert "FontSize=24" in s
    assert "MarginV=80" in s


def test_build_video_filter_no_subs() -> None:
    vf = _build_video_filter(1080, 1920, None, None)
    # 9:16 aspect, default centered: x = (iw - crop_w) * 0.5
    assert "crop=floor(ih*1080/1920/2)*2:ih:(iw-floor(ih*1080/1920/2)*2)*0.5000:0" in vf
    assert "scale=1080:1920:flags=lanczos" in vf
    assert "subtitles" not in vf


def test_build_video_filter_with_subs(tmp_path: Path) -> None:
    srt = tmp_path / "x.srt"
    srt.write_text("dummy", encoding="utf-8")
    vf = _build_video_filter(1080, 1920, srt, SubtitleStyle())
    assert "subtitles=" in vf
    assert "force_style=" in vf
    assert "FontName=Inter" in vf


def test_build_video_filter_subs_no_style(tmp_path: Path) -> None:
    srt = tmp_path / "x.srt"
    srt.write_text("dummy", encoding="utf-8")
    vf = _build_video_filter(1080, 1920, srt, None)
    assert "subtitles=" in vf
    assert "force_style=" not in vf


def test_build_video_filter_crop_focus_left() -> None:
    vf = _build_video_filter(1080, 1920, None, None, crop_focus_x=0.0)
    assert "*0.0000:" in vf  # window's left edge at frame's left


def test_build_video_filter_crop_focus_right() -> None:
    vf = _build_video_filter(1080, 1920, None, None, crop_focus_x=1.0)
    assert "*1.0000:" in vf  # window's right edge at frame's right


def test_build_video_filter_crop_focus_vtube() -> None:
    """0.7 puts the crop window 70% of the way to the right — typical VTube setup."""
    vf = _build_video_filter(1080, 1920, None, None, crop_focus_x=0.7)
    assert "*0.7000:" in vf


def test_build_video_filter_crop_focus_out_of_range() -> None:
    with pytest.raises(ValueError, match="crop_focus_x"):
        _build_video_filter(1080, 1920, None, None, crop_focus_x=-0.1)
    with pytest.raises(ValueError, match="crop_focus_x"):
        _build_video_filter(1080, 1920, None, None, crop_focus_x=1.5)


def test_build_video_filter_with_font_file(tmp_path: Path) -> None:
    """When SubtitleStyle.font_file is set, the filter includes :fontsdir=."""
    srt = tmp_path / "x.srt"
    srt.write_text("dummy", encoding="utf-8")
    fonts_dir = tmp_path / "fonts"
    fonts_dir.mkdir()
    font_file = fonts_dir / "MyFont.ttf"
    font_file.write_bytes(b"")  # placeholder
    style = SubtitleStyle(font_name="MyFont", font_file=font_file)
    vf = _build_video_filter(1080, 1920, srt, style)
    assert "fontsdir=" in vf
    assert fonts_dir.as_posix().replace(":", r"\:") in vf
    assert "FontName=MyFont" in vf


def test_cut_vertical_rejects_missing_font(tmp_path: Path) -> None:
    if not ffmpeg_available():
        pytest.skip("ffmpeg not on PATH")
    src = tmp_path / "fake.mp4"
    src.write_bytes(b"fake")
    style = SubtitleStyle(font_file=tmp_path / "nonexistent.ttf")
    srt = tmp_path / "x.srt"
    srt.write_text("dummy", encoding="utf-8")
    with pytest.raises(FileNotFoundError, match="Font"):
        cut_vertical(src, tmp_path / "out.mp4", 0, 1, subtitles_path=srt, style=style)


def test_cut_vertical_rejects_negative_start(tmp_path: Path) -> None:
    src = tmp_path / "fake.mp4"
    src.write_bytes(b"fake")
    with pytest.raises(ValueError, match="start"):
        cut_vertical(src, tmp_path / "out.mp4", start=-1.0, end=2.0)


def test_cut_vertical_rejects_end_le_start(tmp_path: Path) -> None:
    src = tmp_path / "fake.mp4"
    src.write_bytes(b"fake")
    with pytest.raises(ValueError, match="end"):
        cut_vertical(src, tmp_path / "out.mp4", start=2.0, end=2.0)
    with pytest.raises(ValueError, match="end"):
        cut_vertical(src, tmp_path / "out.mp4", start=2.0, end=1.0)


def test_cut_vertical_missing_input(tmp_path: Path) -> None:
    if not ffmpeg_available():
        pytest.skip("ffmpeg not on PATH")
    with pytest.raises(FileNotFoundError):
        cut_vertical(tmp_path / "missing.mp4", tmp_path / "out.mp4", 0, 1)


def test_cut_vertical_missing_subs(tmp_path: Path) -> None:
    if not ffmpeg_available():
        pytest.skip("ffmpeg not on PATH")
    src = tmp_path / "fake.mp4"
    src.write_bytes(b"fake")
    with pytest.raises(FileNotFoundError, match="Subtitles"):
        cut_vertical(
            src,
            tmp_path / "out.mp4",
            start=0,
            end=1,
            subtitles_path=tmp_path / "missing.srt",
        )


def test_ffmpeg_error_when_ffmpeg_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """If ffmpeg isn't on PATH, raising FFmpegError is the contract."""
    monkeypatch.setattr(shutil, "which", lambda _: None)
    src = tmp_path / "fake.mp4"
    src.write_bytes(b"fake")
    with pytest.raises(FFmpegError, match="not found on PATH"):
        cut_vertical(src, tmp_path / "out.mp4", 0, 1)


# ---- Integration: real ffmpeg invocation ------------------------------------


@pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg not on PATH")
def test_cut_vertical_roundtrip(tmp_path: Path) -> None:
    """Generate a 3-sec test source, cut [0.5, 2.5], verify output is 9:16."""
    src = tmp_path / "src.mp4"
    # ffmpeg lavfi: testsrc2 (color bars) + sine tone audio, 1280x720, 3 sec
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30:duration=3",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            str(src),
        ],
        check=True,
        capture_output=True,
    )
    dst = tmp_path / "clip.mp4"
    cut_vertical(src, dst, start=0.5, end=2.5)
    assert dst.is_file()
    assert dst.stat().st_size > 1000

    # Probe the output resolution.
    probe = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height",
            "-of", "csv=p=0",
            str(dst),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    width, height = (int(x) for x in probe.stdout.strip().split(","))
    assert (width, height) == (1080, 1920)
