# Native Front Window Chase

From repository root with private devices.json, stop the Christmas show and any streaming. Run:

    python window_chase.py --output private-front-chase-preview

Read-only: discovers the complete physical group containing all six windows, checks offsets and capacity, writes raw movies and checksums privately. The 17 panes chase forward and back in 96 frames at 10 fps (9.6 seconds), with every other controller black. All movies share one UUID.

If all members are off, and the preflight is satisfactory, the experimental upload is:

    python window_chase.py --output private-front-chase-upload --execute

The second run repeats preflight and requests explicit confirmation. It appends slaves then master using movies/new and movies/full, never deletes movies, never changes group or mode, verifies catalogues and stops on first error. No automatic rollback: firmware may leave partial entries. Do not retry blindly or delete the complete catalogue. Keep outputs outside Git.

Reload Area Manager after a successful upload; select Movie: Window Chase (stored) on the Front entity. The group API is reverse engineered; grouped writes, playback synchronization and app/cloud visibility still require real hardware validation. Existing native movie playback support matches UUID across controllers.

References: https://github.com/xled/xled-docs/blob/master/docs/rest_api.rst and https://github.com/xled/xled-docs/blob/master/docs/protocol_details.rst
