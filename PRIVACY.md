# Local data and privacy

The repository contains source code and documentation only. No character artwork, screenshots, personal configuration, conversation archives or original private Git history is distributed.

The pet's optional `codex-local` connection reads the current user's local Codex session/state files to derive activity, task titles and usage status. Task titles can be sensitive: do not share screenshots or local diagnostic logs without reviewing them. Select the disabled connection mode to use the pet without local session inspection. Demo mode is explicitly labelled.

Plan-balance lookup is separate from the session connection mode and runs by default. It reads the local Codex sign-in file and sends the existing access token only to the official `https://chatgpt.com/backend-api/codex/usage` endpoint over HTTPS. Tokens are used in memory, are not written to project logs or copied into the repository, and are not refreshed by this tool. This is an undocumented endpoint: failures display an unavailable/expired state, not invented balances. For a completely offline pet, start it with `CODEX_HOME` pointing to an empty local directory so neither sign-in nor session files are available.

The wallpaper process connects to a loopback-only debugging port (default `127.0.0.1:9228`) to apply CSS to the Codex window. A local debugging endpoint can be used by other processes running on the same computer. Do not expose it to a network interface or proxy it publicly. Codex itself and package managers retain their own networking behavior; this project does not replace them.

Pet settings, derived image caches and bounded diagnostic records are stored in the user's local application-data directory. Wallpaper settings, its copies of supplied images, and startup backups also remain local. Optional notice hooks record event metadata and do not grant approval to actions. They are not installed automatically.

Source setup downloads Python dependencies from the configured package index. GitHub CI downloads public build dependencies. Asset import and theme building work with local files; no artwork upload service is used.

Before submitting a bug report, remove usernames, machine paths, task titles, images, configuration secrets and private log content. `.gitignore` prevents ordinary accidental adds; it does not remove data from an existing commit. The public release checker additionally verifies a precise text-file inventory.
