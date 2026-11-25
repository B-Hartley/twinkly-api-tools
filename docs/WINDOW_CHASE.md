# Christmas Window Chase

From the repository root, using your private devices.json:

    python window_chase.py --output private-window-chase

Read-only: audits six named window controllers and creates local raw movie binaries and a metadata/checksum manifest. Keep this directory outside Git (or explicitly ignore it); it contains LAN identities and existing movie metadata, not a backup of their raw frames.

Default 8 seconds at 20 fps (160 frames). Use --fps 10 --seconds 8 to reduce required capacity to 80 frames. The chase runs across Office Left, Office Right, Master Bedroom, Landing, Lounge, Bar in that provisional order, then reverses. Pane order uses existing strand definitions. This is not a calibrated whole-house spatial route.

First experimental upload: choose ONE controller that is already standalone and in off or color mode. Do not ungroup it just to test without a verified restoration plan. Stop any HA virtual streaming before testing. Example:

    python window_chase.py --names "Office left" --output private-office-trial --execute

Type UPLOAD when prompted. Existing movies are never deliberately removed. The program checks capacity, slots, identity, mode and group, uploads using movies/new then movies/full, and verifies catalogue metadata. It does not start playback or change mode. Firmware could still behave unexpectedly; stop on any error and inspect the app before retrying. No automated retry or rollback.

The API does not expose a verified individual-movie deletion operation. If full, stop; use the official app to free space. The installer does not call DELETE.

Grouped playback and cloud visibility are not verified; grouped writes are blocked. A local catalogue entry is not proof of raw movie integrity, playback or app/cloud synchronization. Once a standalone trial is confirmed, use app traffic captures to establish grouped upload and synchronization protocol.
