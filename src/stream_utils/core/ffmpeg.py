"""FFmpeg helpers for the shorts pipeline.

Two primitives:

- :func:`segments_to_srt` — convert a list of :class:`Segment` (from
  :mod:`stream_utils.core.transcribe`) into SRT text. Pure string logic, no
  FFmpeg involvement.
- :func:`cut_vertical` — call FFmpeg via subprocess to extract ``[start, end]``
  from a source video, center-crop to 9:16, optionally burn in subtitles via
  libass + a force-style override. Raw subprocess instead of ``ffmpeg-python``
  because libass force_style is awkward through that wrapper.

FFmpeg must be on ``PATH`` at runtime. :func:`ffmpeg_available` is a cheap
check consumers can run on startup to fail fast with a useful error.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

from stream_utils.core.errors import StreamUtilsError
from stream_utils.core.transcribe import Segment


class FFmpegError(StreamUtilsError):
    """ffmpeg subprocess returned non-zero or wasn't on PATH."""


@dataclass(frozen=True)
class SubtitleStyle:
    """libass force_style fragments. Defaults match the short-form spec from
    ``shorts-from-stream/CLAUDE.md`` (Inter 18px, white text, 3px black outline,
    bottom-center, 120 px above the bottom edge so the text sits above TikTok UI).

    Colors are libass BBGGRR + ``&H...&`` wrapping. Ascending alignment values:
    1=bot-left, 2=bot-center, 3=bot-right, 5=top-left, etc.

    ``font_file`` is optional: when set, the font is registered with libass
    via the ``fontsdir`` filter parameter (the parent directory of the file).
    Use this to bundle a custom font with a project rather than relying on
    system-installed fonts. ``font_name`` must still match the font's internal
    family name as set by the foundry (e.g. ``"Manrope ExtraBold"``).
    """

    font_name: str = "Inter"
    font_size: int = 18
    primary_color: str = "&HFFFFFF&"
    outline_color: str = "&H000000&"
    outline: int = 3
    shadow: int = 0
    border_style: int = 1
    alignment: int = 2
    margin_v: int = 120
    font_file: Path | str | None = None

    def to_force_style(self) -> str:
        """Return the comma-separated ``force_style=`` value for libass."""
        parts = [
            f"FontName={self.font_name}",
            f"FontSize={self.font_size}",
            f"PrimaryColour={self.primary_color}",
            f"OutlineColour={self.outline_color}",
            f"Outline={self.outline}",
            f"Shadow={self.shadow}",
            f"BorderStyle={self.border_style}",
            f"Alignment={self.alignment}",
            f"MarginV={self.margin_v}",
        ]
        return ",".join(parts)


def ffmpeg_available() -> bool:
    """``True`` if ``ffmpeg`` is on PATH."""
    return shutil.which("ffmpeg") is not None


def _format_srt_time(seconds: float) -> str:
    """SRT timestamp: HH:MM:SS,mmm. Negative seconds clamp to 0."""
    if seconds < 0:
        seconds = 0.0
    total_ms = round(seconds * 1000)
    hours, rem_ms = divmod(total_ms, 3600 * 1000)
    minutes, rem_ms = divmod(rem_ms, 60 * 1000)
    secs, ms = divmod(rem_ms, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"


def segments_to_srt(segments: list[Segment], *, time_offset: float = 0.0) -> str:
    """Render segments as SRT text.

    ``time_offset`` is subtracted from each segment's start/end so that segments
    extracted from the middle of a stream can be re-zeroed for a cut clip.
    Empty segments and zero-length segments are skipped.
    """
    lines: list[str] = []
    index = 1
    for s in segments:
        text = s.text.strip()
        if not text:
            continue
        start = s.start - time_offset
        end = s.end - time_offset
        if end <= start:
            continue
        lines.append(str(index))
        lines.append(f"{_format_srt_time(start)} --> {_format_srt_time(end)}")
        lines.append(text)
        lines.append("")
        index += 1
    return "\n".join(lines)


def write_srt(
    segments: list[Segment],
    output_path: Path | str,
    *,
    time_offset: float = 0.0,
) -> Path:
    """Write segments as a UTF-8 SRT file at ``output_path``."""
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(segments_to_srt(segments, time_offset=time_offset), encoding="utf-8")
    return out


def _build_video_filter(
    target_width: int,
    target_height: int,
    subtitles_path: Path | None,
    style: SubtitleStyle | None,
    crop_focus_x: float = 0.5,
    original_size: tuple[int, int] | None = None,
) -> str:
    """Build the FFmpeg ``-vf`` filter chain for vertical-crop + optional subs.

    Algorithm: pick a 9:16-shaped window of the source's full height, position
    it horizontally according to ``crop_focus_x`` (0.0 = left edge of the
    window aligns with frame's left edge, 1.0 = right edge aligns with
    frame's right edge, 0.5 = centered), then scale to the target resolution.
    Aspect derived from ``target_width / target_height`` so non-9:16 targets
    work too.
    """
    if not 0.0 <= crop_focus_x <= 1.0:
        raise ValueError(
            f"crop_focus_x must be in [0.0, 1.0], got {crop_focus_x}"
        )
    aspect_w = target_width
    aspect_h = target_height
    crop_w_expr = f"floor(ih*{aspect_w}/{aspect_h}/2)*2"
    crop_x_expr = f"(iw-{crop_w_expr})*{crop_focus_x:.4f}"
    crop = f"crop={crop_w_expr}:ih:{crop_x_expr}:0"
    scale = f"scale={target_width}:{target_height}:flags=lanczos"
    chain = [crop, scale]
    if subtitles_path is not None:
        # libass needs forward slashes and no drive-letter colon escaping issues.
        # We pass the full posix-style path, then escape the colon for FFmpeg
        # filter syntax (Windows drive letters → "X\\:/...").
        ass_path = subtitles_path.as_posix().replace(":", r"\:")
        sub_filter = f"subtitles='{ass_path}'"
        # original_size tells libass what canvas the .ass was authored for —
        # critical when the source has been cropped/resized. Without it,
        # libass uses default PlayRes (~288px tall) and FontSize/MarginV
        # render scaled-up: 28px font becomes 187px in 1080x1920 output,
        # 120 MarginV becomes 42% from bottom (i.e. middle of screen).
        if original_size is None:
            original_size = (target_width, target_height)
        sub_filter += f":original_size={original_size[0]}x{original_size[1]}"
        if style is not None and style.font_file is not None:
            fontsdir = Path(style.font_file).parent.as_posix().replace(":", r"\:")
            sub_filter += f":fontsdir='{fontsdir}'"
        if style is not None:
            sub_filter += f":force_style='{style.to_force_style()}'"
        chain.append(sub_filter)
    return ",".join(chain)


def cut_vertical(
    input_path: Path | str,
    output_path: Path | str,
    start: float,
    end: float,
    *,
    subtitles_path: Path | str | None = None,
    style: SubtitleStyle | None = None,
    target_resolution: tuple[int, int] = (1080, 1920),
    crop_focus_x: float = 0.5,
    audio_bitrate: str = "128k",
    video_crf: int = 23,
    overwrite: bool = True,
) -> Path:
    """Cut ``[start, end]`` from ``input_path``, vertical-crop, optionally
    burn subtitles, write to ``output_path``.

    ``crop_focus_x`` controls horizontal positioning of the crop window:
    0.0 = left-aligned, 0.5 = centered (default), 1.0 = right-aligned.
    Use ~0.7-0.8 for a VTube streamer whose model lives in the right portion
    of the frame.

    Re-encodes the segment (no stream copy) — required for the crop and
    subtitle burn-in. Uses libx264 + AAC, which are universally compatible
    with TikTok / Shorts / Reels.
    """
    if start < 0:
        raise ValueError(f"start must be >= 0, got {start}")
    if end <= start:
        raise ValueError(f"end must be > start, got start={start}, end={end}")
    if not ffmpeg_available():
        raise FFmpegError("ffmpeg not found on PATH")
    src = Path(input_path)
    if not src.is_file():
        raise FileNotFoundError(f"Input video not found: {src}")
    dst = Path(output_path)
    dst.parent.mkdir(parents=True, exist_ok=True)

    target_w, target_h = target_resolution
    if target_w <= 0 or target_h <= 0:
        raise ValueError(f"target_resolution must be positive, got {target_resolution}")

    subs = Path(subtitles_path) if subtitles_path is not None else None
    if subs is not None and not subs.is_file():
        raise FileNotFoundError(f"Subtitles file not found: {subs}")

    if style is not None and style.font_file is not None:
        font_path = Path(style.font_file)
        if not font_path.is_file():
            raise FileNotFoundError(f"Font file not found: {font_path}")

    duration = end - start
    vf = _build_video_filter(target_w, target_h, subs, style, crop_focus_x)
    # Input-side -ss (before -i) is accurate by default in modern FFmpeg
    # (4.x+) AND keeps the subtitles filter's PTS clock in sync — output-side
    # -ss after -i was a dead end here: audio/video aligned, but libass
    # ended up with no events visible at any output frame timestamp.
    cmd = [
        "ffmpeg",
        "-y" if overwrite else "-n",
        "-ss",
        f"{start:.3f}",
        "-i",
        str(src),
        "-t",
        f"{duration:.3f}",
        "-vf",
        vf,
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        str(video_crf),
        "-c:a",
        "aac",
        "-b:a",
        audio_bitrate,
        "-movflags",
        "+faststart",
        str(dst),
    ]
    log = logger.bind(module="stream_utils.ffmpeg")
    log.debug(f"running: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise FFmpegError(
            f"ffmpeg exited with code {result.returncode}.\n"
            f"stderr (last 500 chars): {result.stderr[-500:]}"
        )
    return dst


__all__ = [
    "FFmpegError",
    "SubtitleStyle",
    "cut_vertical",
    "ffmpeg_available",
    "segments_to_srt",
    "write_srt",
]
