"""Translated-audio buffering profiles for stable real-time playback."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PlaybackProfile:
    code: str
    start_buffer_ms: int
    minimum_flush_start_ms: int
    maximum_target_buffer_ms: int
    maximum_backlog_ms: int
    low_water_ms: int
    high_water_ms: int
    normal_speed: float
    catchup_speed: float
    underflow_buffer_step_ms: int
    recovery_interval_seconds: float
    adaptive: bool
    clear_when_backlog_full: bool


LEGACY_PLAYBACK_PROFILE = PlaybackProfile(
    code="legacy",
    start_buffer_ms=240,
    minimum_flush_start_ms=0,
    maximum_target_buffer_ms=240,
    maximum_backlog_ms=2_000,
    low_water_ms=0,
    high_water_ms=0,
    normal_speed=1.08,
    catchup_speed=1.08,
    underflow_buffer_step_ms=0,
    recovery_interval_seconds=0,
    adaptive=False,
    clear_when_backlog_full=True,
)


BALANCED_SMOOTH_PLAYBACK_PROFILE = PlaybackProfile(
    code="balanced-smooth",
    # Gemini delivers translated audio in bursts. Buffer once, then play a
    # continuous stream instead of repeatedly falling into silence.
    start_buffer_ms=1_400,
    minimum_flush_start_ms=480,
    maximum_target_buffer_ms=2_600,
    maximum_backlog_ms=10_000,
    low_water_ms=850,
    high_water_ms=2_600,
    normal_speed=1.08,
    catchup_speed=1.10,
    underflow_buffer_step_ms=250,
    recovery_interval_seconds=30.0,
    adaptive=True,
    clear_when_backlog_full=False,
)


PLAYBACK_PROFILES = {
    LEGACY_PLAYBACK_PROFILE.code: LEGACY_PLAYBACK_PROFILE,
    BALANCED_SMOOTH_PLAYBACK_PROFILE.code: BALANCED_SMOOTH_PLAYBACK_PROFILE,
}

DEFAULT_PLAYBACK_PROFILE = BALANCED_SMOOTH_PLAYBACK_PROFILE.code


def playback_profile(code: str) -> PlaybackProfile:
    try:
        return PLAYBACK_PROFILES[code]
    except KeyError as error:
        raise ValueError(f"Noma’lum playback profili: {code}") from error
