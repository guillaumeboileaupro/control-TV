# Pull request

## Summary

Describe what this pull request changes and why.

## Scope

List the main components affected by this change.

- [ ] Python domain or application logic
- [ ] PyChromecast adapter
- [ ] Python bridge
- [ ] Rust / Tauri
- [ ] TypeScript / UI
- [ ] Tests
- [ ] CI / tooling
- [ ] Documentation
- [ ] Packaging
- [ ] Other

## Changes

Describe the important implementation changes.

- 
- 
- 

## Automated validation

Check only what was actually run successfully.

- [ ] Ruff check
- [ ] Ruff format check
- [ ] mypy strict
- [ ] pytest
- [ ] Python coverage requirement met
- [ ] `uv pip check`
- [ ] Rust formatting
- [ ] Clippy with warnings denied
- [ ] Rust tests
- [ ] TypeScript type checking
- [ ] Frontend formatting
- [ ] Frontend tests
- [ ] GitHub Actions successful on the final commit

Add relevant test counts, coverage, CI run information, or other validation details:

## Physical device validation

Was this pull request tested with a real Google Cast device?

- [ ] Yes
- [ ] No
- [ ] Not applicable

If yes, indicate only the general device type:

- [ ] Chromecast
- [ ] Google TV
- [ ] Android TV with Google Cast
- [ ] Other Google Cast compatible device

Operations physically validated:

- [ ] Discovery
- [ ] Device selection
- [ ] Status retrieval
- [ ] Media loading
- [ ] Play
- [ ] Pause
- [ ] Stop
- [ ] Seek
- [ ] Volume
- [ ] Mute / unmute

Describe the observed result without including private device or network information:

## Privacy checklist

- [ ] No private IP address is included.
- [ ] No real device UUID or other unique device identifier is included.
- [ ] No credential, token, authentication material, or secret is included.
- [ ] Screenshots and logs have been sanitized.
- [ ] No unnecessary local network information is included.

## Architecture

- [ ] The change respects the existing architecture.
- [ ] Cast business logic has not been duplicated in Rust or TypeScript.
- [ ] Existing public contracts were preserved, or changes are documented.
- [ ] No automatic replay was introduced for ambiguous device commands.
- [ ] Confirmation states are not presented as stronger than the available evidence.
- [ ] Not applicable to this pull request.

## UI changes

- [ ] This pull request does not modify the UI.

If the UI is modified:

- [ ] Desktop behavior was checked.
- [ ] Small-window or responsive behavior was checked.
- [ ] Keyboard interaction was checked where relevant.
- [ ] Focus and accessibility states were checked.
- [ ] Loading, unavailable, disconnected, partial, pending and error states were considered where relevant.
- [ ] The existing control-TV visual direction and assets were preserved.

## Documentation and development plan

- [ ] `DEVELOPMENT_PLAN.md` was reviewed and updated if necessary.
- [ ] README or other documentation was updated if behavior changed.
- [ ] Automated validation and physical validation are documented separately.
- [ ] Known limitations and unfinished validation remain explicitly documented.

## Known limitations

List anything intentionally left unresolved or requiring follow-up.

- 

## Review notes

Highlight anything reviewers should examine particularly carefully.

- 

## Final checklist

- [ ] The pull request is focused on one coherent change.
- [ ] Tests were added or updated when behavior changed.
- [ ] Regression fixes include regression coverage where practical.
- [ ] Required CI checks pass on the final commit.
- [ ] Review feedback has been addressed or discussed.
- [ ] No generated build artifacts or temporary files are committed.
- [ ] No hardware validation is claimed unless it was actually performed.
- [ ] Security vulnerabilities are not being disclosed publicly in this pull request.
