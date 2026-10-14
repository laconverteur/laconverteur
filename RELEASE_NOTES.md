# La Converteur 1.0.0

First public release of the independent La Converteur interoperability tool.

## Highlights

- Converts Soundvision `.xmlp`, `.xmls`, and `.xml` projects.
- Converts ArrayCalc `.dbacv` and `.dbpr` projects.
- Converts EASE Focus 3 `.fc3` projects directly.
- Converts EASE 5 `.eprj` projects when a compatible local EASE 5 installation
  is present.
- Writes native RDShape `.s3d` projects with groups, colors, basemaps,
  receivers, native quadrilaterals, sectors, trapeziums, and freeform venue
  geometry.
- Uses a 15 cm planarity tolerance when deciding whether adjacent geometry can
  be represented as one editable freeform surface.
- Includes a Windows x64 portable build and SHA-256 checksum.

## Installation

Download `La-Converteur-portable.zip`, verify the accompanying SHA-256 file,
extract the complete archive, and start `La Converteur.exe`.

## EASE 5 requirement

EASE 5 `.eprj` conversion requires a lawfully installed, compatible local copy
of EASE 5. No AFMG program libraries are included in this release. EASE Focus 3
does not require EASE 5.

## Independence

La Converteur is not affiliated with, endorsed by, sponsored by, supported by,
or otherwise connected with L-Acoustics, d&b audiotechnik, AFMG Technologies,
or RCF S.p.A.

## License

Original project code and assets are licensed under Apache-2.0. See
`THIRD-PARTY-NOTICES.md` and the release archive's `licenses/` directory for
the licenses of bundled runtime components.
