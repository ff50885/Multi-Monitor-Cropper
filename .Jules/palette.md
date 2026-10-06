## 2025-05-10 - Canvas Interaction affordance and OS Dependency Handling
**Learning:** In desktop canvas GUI tools (e.g. Tkinter Canvas), interactive pan/drag features are often non-discoverable without explicit visual hint cues or feedback. Providing a gentle overlay hint on image load significantly improves interaction affordance.
**Action:** Always add interactive hint cues (e.g. "Click & drag to pan") on canvas components and protect OS-dependent library imports gracefully with fallback notifications.
