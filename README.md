# La Converteur

<p align="center">
  <img src="docs/images/laconverteur-readme.png" width="960" alt="La Converteur converts venue project files into editable geometry">
</p>

La Converteur is an independent, open-source Windows converter for venue
geometry. It converts projects from Soundvision, ArrayCalc, EASE Focus 3 and
EASE 5 into RDShape `.s3d` projects.

> [!IMPORTANT]
> La Converteur is an independent community project. It is not affiliated with,
> endorsed by, sponsored by, supported by, or otherwise connected with
> L-Acoustics, d&b audiotechnik, AFMG Technologies, or RCF S.p.A.
>
> Soundvision, ArrayCalc, EASE, EASE Focus, RDShape, and all other product names
> and trademarks are the property of their respective owners. Those companies
> do not provide support for La Converteur.

## Supported inputs

- Soundvision: `.xmlp`, `.xmls`
- ArrayCalc: `.dbacv`, `.dbpr`
- EASE Focus 3: `.fc3` (handled directly)
- EASE 5: `.eprj` (requires a lawfully installed, compatible local EASE 5
  installation)

La Converteur does not include AFMG program libraries and does not bypass EASE
5 licensing. The EASE 5 adapter loads `S4.EASE.Core.dll` from the user's local
EASE 5 installation. EASE Focus 3 imports do not require EASE 5.

Users are responsible for ensuring that they are entitled to access and convert
their project files and for complying with the applicable vendor agreements.

## Map-based venues

The **Create venue from map** view can generate an RDShape venue for a selected
location. It combines satellite imagery with OpenStreetMap building footprints
and can optionally add terrain elevation data from OpenTopography.

Satellite imagery and OpenStreetMap data do not require an API key. Terrain
downloads require a free personal OpenTopography API key. Enter the key when
prompted by La Converteur; it is stored for the current Windows user as
`LACONVERTEUR_OPENTOPOGRAPHY_API_KEY`.

## Repository layout

- `src/converter/converteur.py`: main Python converter
- `src/gui/`: Windows Forms frontend
- `assets/`: GUI artwork
- `templates/rdshape/`: native RDShape templates used by the writer
- `tests/fixtures/`: local-only input and RDShape reference files (ignored by Git)
- `tests/expected/`: machine-readable expectations
- `scripts/`: build, checksum and regression scripts
- `dist/`: generated portable package (not committed)

## Build

The portable build needs a prepared runtime directory containing `python/` and `occt/`. A known-good unpacked portable package can be supplied as the runtime source:

```powershell
.\scripts\Build-Portable.ps1 -RuntimeSource "..\dependencies\portable-runtime"
```

The resulting archive and its SHA-256 checksum are written to `dist/`. The ZIP
opens directly to `La Converteur.exe` and one support directory named
`converteur/`; no additional wrapper directory is used.

## Tests

Run the conversion regression suite against the freshly staged package:

```powershell
.\scripts\Test-Regression.ps1
```

Use `-IncludeLarge` to include the large Uber Eats, Turm and Auditorium projects.

Venue fixtures are local-only and excluded from Git. They are never included
in the source repository or uploaded through Git LFS.

### Map importer command-line tools

The isolated geodata spike downloads an Esri World Imagery PNG and OpenStreetMap
building footprints for a metric extent. Generated packages and caches belong in
`tests/output/` and are not committed:

```powershell
python .\src\geo\geo_phase0.py --lat 50.941066 --lon 6.957008 --width 200 --height 200 --output .\tests\output\geo-phase0
python -m unittest .\tests\test_geo_phase0.py
```

Add a global bare-earth GEDTM30 elevation grid when an OpenTopography key is available in
`LACONVERTEUR_OPENTOPOGRAPHY_API_KEY`:

```powershell
python .\src\geo\geo_phase0.py --lat 50.941066 --lon 6.957008 --width 200 --height 200 --elevation GEDTM30 --output .\tests\output\geo-phase0
```

Stage 1 can turn a few simple quadrilateral footprints from that package into
an isolated RDShape venue without running the portable build:

```powershell
python .\src\geo\geo_to_s3d.py .\tests\output\geo-phase0 .\tests\output\geo-stage1.s3d --occt ..\dependencies\portable-runtime\occt
```

Use `--limit 0` to include every simple quadrilateral building footprint and
building part returned for the selected extent.

## License

The original La Converteur source code and project assets are licensed under
the [Apache License 2.0](LICENSE). Distributed third-party components remain
under their respective licenses; see [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md).

The names and trademarks of supported products are used only to describe file
format interoperability. No rights in those names or trademarks are granted.

If we ever meet and La Converteur saved your day, you may buy one of the
contributors a beer. This is a friendly, entirely optional gesture and is not
an additional license condition.

<p align="center">
  <img src="docs/images/laconverteur-beer.png" width="820" alt="The La Converteur machine enjoying an optional thank-you beer">
</p>
