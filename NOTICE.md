# Notices

The in-game bridge and bundled `worker/modmenu.txt` compatibility menu are
derived from the Thinker mod for Sid Meier's Alpha Centauri:

- Source: https://github.com/induktio/thinker
- Pinned source commit used for this installation: `4aef5be73bda4eb22ffa8db424eb91780c4a51fa`
- License: MIT; see `bridge/License.md`

SMACX Agent's original source is licensed under the repository's top-level
Apache License 2.0. The Thinker-derived bridge retains Thinker's MIT license.

The managed browser stream is built from Selkies at pinned revision
`be53b2c39670ccd1432fe50ebcd6d0ade72ce80a` and installs Pixelflux 2.0.0 and
PCMFlux 2.0.0. Those components are licensed under Mozilla Public License 2.0.
Their license texts are retained in the worker image under
`/opt/smacx/licenses/`; source locations are:

- https://github.com/selkies-project/selkies
- https://github.com/linuxserver/pixelflux
- https://github.com/linuxserver/pcmflux

This application does not include or distribute Sid Meier's Alpha Centauri,
Alien Crossfire, or other proprietary game assets. Users provide their own
installation. Managed copies and optional mechanics extraction remain in the
operator's private volumes.

The isolated Proton prefix contains Microsoft DirectPlay runtime files extracted from the official February 2010 DirectX redistributable. The redistributable is a local installation dependency under its own Microsoft terms and is not covered by the Thinker MIT license.
