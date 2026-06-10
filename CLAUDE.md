# Decima Workshop (macOS fork)

Fork of ShadelessFox/decima-workshop ported to macOS (Apple Silicon) for
Death Stranding: Director's Cut from the Mac App Store.

## Build & run

- JDK 24 + Maven (`./mvnw`). Build: `./mvnw clean package -DskipTests`
  → self-contained app at `decima-app/target/dist/decima.app`.
- Game lives at `/Applications/DeathStranding.app` (executable
  `Contents/MacOS/DeathStranding`, data `Contents/Resources/data`).
- macOS-specific bits to preserve: `PlatformMacOSXGLCanvas` (GL is capped at 4.1
  — never assume GL 4.3/KHR_debug), `OffscreenModelSurface` (lightweight offscreen
  model preview, macOS only), the mount-all branch in `DSPackfileProvider`.

## Audio extraction — use audio-extraction-scripts/ as your tools

Don't re-derive the pipeline; these scripts in `audio-extraction-scripts/` already encode it
(details in `audio-extraction-scripts/README.md`). All generated files live OUTSIDE the repo in
`~/decima-explorer-workspace/` (it's copyrighted game audio — never commit it).

| Task | Command |
|---|---|
| **Start here:** search by words, then extract | `python3 audio-extraction-scripts/find_audio.py "<words>"` then `--take N` |
| Dialogue/voice lines for a whole group | `python3 audio-extraction-scripts/extract_lines.py <group>` (groups: `audio-extraction-scripts/line_groups.txt`) |
| A sound effect / music cue by exact name | `python3 audio-extraction-scripts/extract_sfx.py <name-or-substring>` |
| Browse candidate SFX names | `python3 audio-extraction-scripts/extract_sfx.py <substring> --list` |
| One-time SFX catalog build | `python3 audio-extraction-scripts/build_sfx_workspace.py` |

The scripts drive this repo's own build (`decima-app/target/dist/decima.app` — the fork
ships the `sentences`/`extract`/`export-wem` CLI subcommands they need), and read the
project id + ww2ogg/revorb paths from
`~/Library/Application Support/DecimaWorkshop/config.json`.

### Translating user requests to game names (the part that needs judgment)

- Per-character codec/call ringtone: `sd_sfx_com_radio_call_recieve_<char>`
  ("recieve" is misspelled in the game data; chars: fragile, heartman, deadman,
  diehardman, mama, lockne, bridges, announcer, …).
- Mail/notification chime: `sd_sfx_hud_mail_ringtone`. HUD/UI sounds are `sd_sfx_hud_*`.
- Music stings/jingles: `sd_music_*` (e.g. `sd_music_jingle_sam_sleep`).
- Sam's open-world shouts + strand greeting ("Hey, my name's Sam too!"):
  group `ds_lines_sam/lines_sam`. Tutorial/build-hint/codec VO: `ds_lines_common/lines_global`.
  NPCs: `ds_lines_npc/lines_<char>`; missions `ds_lines_mission/lines_m000XX`;
  terminals `ds_lines_terminal/lines_<facility>`.
- To find a spoken line's group: decode a likely group with
  `decima sentences -c localized/sentences/<group>/sentences.core` and grep the TSV.

### Known dead-ends (don't chase these again)

- "Good morning, Sam." and "Keep on keeping on." are subtitle-only: generated at
  runtime by the `vr0001_radio` voicesignal system. No SentenceResource, no WEM,
  nothing to extract.
- Unnamed wwiser txtp (`bank_NNN-…-event.txtp`) are fine: `extract_sfx.py` resolves
  them via FNV-1 hashing of the requested name.
