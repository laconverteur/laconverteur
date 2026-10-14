# Runtime and development dependencies

## Distributed runtime

- Windows x64 .NET 9 self-contained runtime, produced by `dotnet publish`
- Embedded CPython runtime
- Open CASCADE Technology (OCCT) 7.8.0 runtime with `DRAWEXE.exe`, resources
  required by the writer, and the third-party DLLs required by this binary build
- Native RDShape template projects from `templates/rdshape/`

## Host integration

- Windows PowerShell and System.Drawing are used to normalize embedded raster images to PNG.
- EASE Focus 3 projects are decoded without an EASE 5 installation.
- EASE 5 projects require a compatible, lawfully installed local EASE 5 copy.
  The project adapter loads `S4.EASE.Core.dll` from that installation. AFMG
  program libraries are neither committed nor distributed.
- Git for Windows OpenSSL is used only as a fallback for formats that require OpenSSL and when it is available locally.

## Not part of the product repository

Ghidra, GhydraMCP, Procmon logs, decompiler dumps, full OCCT development archives, NuGet caches and .NET SDK caches are research or machine-local dependencies. They belong outside the Git root.

See `THIRD-PARTY-NOTICES.md` for the release license inventory. The portable
archive contains the applicable license and notice files in its root and
`licenses/` directory.

