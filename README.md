# La Converteur

La Converteur converts venue projects from Soundvision, ArrayCalc, EASE Focus 3 and EASE 5 into RDShape `.s3d` projects.

## Supported inputs

- Soundvision: `.xmlp`, `.xmls`, `.xml`
- ArrayCalc: `.dbacv`, `.dbpr`
- EASE Focus 3: `.fc3`
- EASE 5: `.eprj` (requires a compatible local EASE 5 installation for legacy projects)

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

### Experimental map importer (Phase 0)

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
