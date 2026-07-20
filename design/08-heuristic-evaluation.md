## Heuristic Evaluation: Live Translator direction flow

**Evaluated**: 2026-07-16  
**Framework**: Nielsen's 10 Usability Heuristics  
**Scope**: Direction selection and automatic audio routing in the shared screenshot

### Summary

- Critical issues: 0
- Major issues: 2
- Minor issues: 1

### Major Issues — Fix Immediately

#### The second product mode is hidden

- **Heuristic violated**: #6 — Recognition rather than recall
- **Location**: `YO‘NALISH` dropdown
- **Problem**: Only the active `English → O‘zbekcha` mode is visible. The required
  `O‘zbekcha → English` workflow has no persistent visual presence.
- **Impact**: The interface looks like it still has only one translation mode.
- **Recommendation**: Replace the dropdown with two always-visible segmented
  buttons and show a strong selected state.
- **Severity**: 3

#### Incoming mode selected the wrong physical output

- **Heuristic violated**: #5 — Error prevention
- **Location**: `OUTPUT` field
- **Problem**: `English → O‘zbekcha` automatically selected the `P2961` display at
  44 kHz instead of the intended MacBook speaker/headphone output.
- **Impact**: Translation can be inaudible or play through the wrong device.
- **Recommendation**: Prefer `MacBook Air Speakers`, headphones, or the current
  known physical output; never choose the first non-virtual device blindly.
- **Severity**: 3

### Minor Issue

| Issue | Heuristic | Recommendation | Severity |
|---|---|---|---|
| `INPUT → GEMINI → OUTPUT` exposes implementation language | #2 — Match with real world | Keep the path but pair it with task wording such as “Meetingni eshitish” / “Zoom’ga gapirish” | 2 |

### Strengths Observed

- Current status and Start/Stop availability are clear.
- Input and output names and sample rates are visible.
- The route explanation under the device fields is contextual and actionable.

### Next Steps

1. Ship the two-button mode selector.
2. Make direction selection apply deterministic MacBook/BlackHole presets.
3. Keep manual input/output overrides available after the preset is applied.
