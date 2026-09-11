# Runtime and development dependencies

## Distributed runtime

- Windows x64 .NET 9 self-contained runtime, produced by `dotnet publish`
- Embedded CPython runtime
- Open CASCADE Technology (OCCT) runtime with `DRAWEXE.exe`, headers/resources required by the writer, and its third-party DLLs
- Native RDShape template projects from `templates/rdshape/`

## Host integration

- Windows PowerShell and System.Drawing are used to normalize embedded raster images to PNG.
- Legacy EASE 5 projects may require a compatible locally installed EASE 5/Bond decoder assembly. These vendor files are not intended to be committed to this repository.
- Git for Windows OpenSSL is used only as a fallback for formats that require OpenSSL and when it is available locally.

## Not part of the product repository

Ghidra, GhydraMCP, Procmon logs, decompiler dumps, full OCCT development archives, NuGet caches and .NET SDK caches are research or machine-local dependencies. They belong outside the Git root.

