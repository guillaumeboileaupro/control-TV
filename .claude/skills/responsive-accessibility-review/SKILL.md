---
name: responsive-accessibility-review
description: Review responsive behavior and accessibility of web or application interfaces. Use for keyboard navigation, semantic HTML, contrast, focus, touch targets, forms, dialogs, status messages, and narrow-screen layouts.
---

# Responsive Accessibility Review

Combine automated checks with keyboard and visual inspection.

- Test representative narrow, medium, and wide viewports with real content and long labels.
- Verify document landmarks, heading order, control names, form labels, error association, tables, dialogs, and live status messages.
- Complete every flow by keyboard and ensure focus is visible, ordered, trapped only in active modals, and restored on close.
- Check WCAG AA contrast and never use color as the only state indicator.
- Target at least 44 by 44 pixels for touch controls when practical.
- Respect reduced motion, zoom, text resizing, dark mode, and high-content states.
- Record evidence by issue, severity, location, reproduction, and expected behavior.

Run `python scripts/static_html_audit.py <html-or-directory>` for heuristic static findings, then verify them manually.