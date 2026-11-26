FRONT NATIVE MOVIES V3 - PRIVATE

Five pre-rendered native RGBW movies for the 15-member, 4,900-LED Front group.
Each movie has one shared UUID and 15 controller-specific frame files (WRGB).
Built from the corrected V3 2D map and the original cloud scene's 15 source
3D maps; no controller data or credentials are sent anywhere during generation.
The unlit bush has no LED assignment. The 2D map itself is NOT applied here.

INSTALLATION (Windows PowerShell, on the controller LAN)
1. Ideally apply and verify the corrected 2D map first; remapping may clear movies.
2. Stop the Christmas show, any realtime streaming, and switch every controller off.
   Do not use the app or HA lights during the upload.
3. Extract this ZIP. In the extracted Front_movies_V3 folder:
   python .\upload_movies.py --verify-files
   python .\upload_movies.py
   The second command authenticates and reads the live controllers, but does not
   write any movies. It checks MAC, profile, count, exact group UUID, role, offset,
   free slots and frame capacity. All five require 768 free frames on EVERY member.
4. If preflight passes, try the smaller Window Chase first:
   python .\upload_movies.py --movie window-chase --execute
   Type UPLOAD FRONT when asked. This attempts experimental additive local API
   uploads via movies/new and movies/full, slaves before master, checking each
   catalogue. It never deletes movies, changes group, maps, or playback mode.
5. After successful catalogue readback, reload Twinkly Area Manager in HA. Try
   Movie: Window Chase V3 on light.twinkly_front at low brightness.
6. If successful, preflight and install the remaining four together:
   python .\upload_movies.py --movie bush-window-v2 --movie house-garden --movie bush-startup --movie window-startup
   python .\upload_movies.py --movie bush-window-v2 --movie house-garden --movie bush-startup --movie window-startup --execute
   Reload Area Manager again. To install all five from scratch, omit --movie.
   Do NOT do that after installing one: duplicate names/UUIDs intentionally fail.

WARNINGS
The movie upload API is reverse-engineered and has not been validated on this
physical group. Catalogue verification cannot prove stored frame contents or
synchronized playback. Cloud Gallery/app visibility is not guaranteed. Failed
writes may leave partial entries. No automatic retries or deletion/rollback;
inspect all catalogues before any retry. Keep the original Gallery movies.
Current free storage is unknown: older backup capacities are not current capacity.
The full pack is 768 frames per controller; the 600-LED Front Right controller
historically had a maximum of 1691 frames, but downloaded movies consume it.
Startup clips are standalone looping native movies, no audio; their cue timing
is rounded to 0.1 seconds. The existing Christmas show remains its own score.

FILES
manifest.json: member identity, route, movie metadata and SHA256 per raw file.
Movie folders: raw frames, not video/MP4 files or Twinkly app import files.
upload_movies.py: stdlib only; default read-only, --verify-files entirely offline.
build_movies.py: optional offline rebuild from the V3 candidate ZIP (requires
numpy). A rebuild creates new shared movie UUIDs. Do not rebuild merely to retry.
Keep this package outside Git; contains private controller addresses and identities.
