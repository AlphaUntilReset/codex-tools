# Codex Tools maintenance

This is an unofficial Windows desktop companion toolkit for Codex.

- Never commit artwork, user asset packs, generated CSS, preview images, local settings, logs, credentials, binary packages, or personal machine paths.
- Code and documentation are MIT licensed; user-supplied assets are excluded from that license and stay local.
- Keep the custom-asset mode independent of historical character calibration. Missing assets must produce an actionable setup prompt.
- Tests must use temporary data directories and offscreen Qt. Do not manipulate a real Codex installation, running pet, startup entries or user data during tests.
- Run `npm run check:public`, `npm test`, and `npm run test:pet` before release. Changes require a freshly reviewed PUBLIC-FILES.json inventory.
- Never turn a repository containing private artwork/history into this public repository. Publish only reviewed clean exports, preserving the public repository's own history.
- Re-read files before edits; another agent or contributor may be working concurrently.
