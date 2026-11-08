"""Translate temporal state into expiring actuator recommendations."""

from __future__ import annotations

from ad_detect.domain import (
    Classification,
    Decision,
    OperatingMode,
    RecommendedAction,
    TemporalState,
)


class ConservativeDecisionPolicy:
    """Stateful policy whose failure direction is always toward restoration."""

    def __init__(
        self,
        *,
        maximum_mute_seconds: int,
        reentry_cooldown_seconds: int = 10,
        decision_ttl_ms: int = 1500,
    ) -> None:
        if maximum_mute_seconds < 1 or reentry_cooldown_seconds < 0 or decision_ttl_ms < 1:
            raise ValueError("policy durations must be positive")
        self._maximum_mute_ns = maximum_mute_seconds * 1_000_000_000
        self._reentry_cooldown_ns = reentry_cooldown_seconds * 1_000_000_000
        self._ttl_ns = decision_ttl_ms * 1_000_000
        self._break_active = False
        self._muted_since_ns: int | None = None
        self._inhibited_until_ns = 0

    @property
    def break_active(self) -> bool:
        return self._break_active

    def decide(
        self,
        temporal: TemporalState,
        *,
        mode: OperatingMode,
        automatic_muting_enabled: bool,
        manual_program_override: bool = False,
    ) -> Decision:
        reasons = list(temporal.reasons)
        action = RecommendedAction.NONE
        can_act = mode is OperatingMode.AUTOMATIC and automatic_muting_enabled

        unsafe = temporal.classification is Classification.UNKNOWN or manual_program_override
        timed_out = (
            self._muted_since_ns is not None
            and temporal.at_ns - self._muted_since_ns >= self._maximum_mute_ns
        )

        if unsafe or timed_out:
            had_active_break = self._break_active or self._muted_since_ns is not None
            if manual_program_override:
                reasons.append("manual program override")
            if temporal.classification is Classification.UNKNOWN:
                reasons.append("unknown classification inhibits mute")
            if timed_out:
                reasons.append("maximum mute duration reached")
            if self._muted_since_ns is not None and can_act:
                action = RecommendedAction.RESTORE
            self._break_active = False
            self._muted_since_ns = None
            if had_active_break:
                self._inhibited_until_ns = temporal.at_ns + self._reentry_cooldown_ns
        elif temporal.classification is Classification.COMMERCIAL:
            if temporal.at_ns < self._inhibited_until_ns:
                reasons.append("re-entry cooldown inhibits mute")
                self._break_active = False
            elif not self._break_active:
                self._break_active = True
                reasons.append("commercial break entered")
                if can_act:
                    action = RecommendedAction.MUTE
                    self._muted_since_ns = temporal.at_ns
                elif mode is OperatingMode.SHADOW:
                    reasons.append("shadow mode suppressed mute")
        elif temporal.classification is Classification.PROGRAM and self._break_active:
            self._break_active = False
            reasons.append("program recovery ended break")
            if self._muted_since_ns is not None and can_act:
                action = RecommendedAction.RESTORE
            self._muted_since_ns = None

        return Decision(
            at_ns=temporal.at_ns,
            classification=temporal.classification,
            confidence=temporal.confidence,
            break_active=self._break_active,
            action=action,
            reasons=tuple(dict.fromkeys(reasons)),
            expires_ns=temporal.at_ns + self._ttl_ns,
            evidence_versions=temporal.evidence_versions,
        )
