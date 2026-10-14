# Third-party notices

La Converteur is licensed under Apache-2.0. The portable Windows distribution
also contains the components below. They remain the property of their respective
copyright holders and are provided under their own licenses.

| Component | Version in release 1.0 | License |
| --- | --- | --- |
| Microsoft .NET Windows runtime | 9.x self-contained runtime | Microsoft .NET Library License and included third-party notices |
| CPython | 3.13.0 | Python Software Foundation License |
| Open CASCADE Technology | 7.8.0 | LGPL-2.1-only with the OCCT additional exception |
| FFmpeg libraries | 3.3.4 | LGPL-2.1-or-later; the bundled binaries identify themselves as LGPL builds |
| FreeImage | 3.17.0 | FreeImage Public License 1.0 |
| FreeType | 2.5.5 | FreeType Project License or GPL-2.0-only |
| jemalloc | OCCT binary dependency | 2-clause BSD-style license |
| OpenVR API | 1.14.15 | BSD-3-Clause |
| oneTBB | 2021.5 | Apache-2.0 |
| Tcl/Tk | 8.6 | Tcl/Tk BSD-style license |
| zlib | 1.2.8 | Zlib License |
| Microsoft Visual C++ runtime | VC14-family redistributable | Microsoft Visual C++ Redistributable terms |

The portable archive includes the .NET license and notices, the Python license,
the OCCT LGPL text and exception, and the Tcl/Tk license files distributed with
those runtimes. Copyright and license notices embedded in the component trees
must not be removed.

Corresponding upstream projects and source code:

- .NET: <https://github.com/dotnet/runtime>
- CPython: <https://github.com/python/cpython/tree/v3.13.0>
- OCCT 7.8.0: <https://github.com/Open-Cascade-SAS/OCCT/tree/V7_8_0>
- FFmpeg 3.3.4: <https://ffmpeg.org/releases/ffmpeg-3.3.4.tar.xz>
- FreeImage 3.17.0: <https://sourceforge.net/projects/freeimage/files/Source%20Distribution/3.17.0/>
- FreeType 2.5.5: <https://download.savannah.gnu.org/releases/freetype/>
- jemalloc: <https://github.com/jemalloc/jemalloc>
- OpenVR: <https://github.com/ValveSoftware/openvr>
- oneTBB 2021.5: <https://github.com/oneapi-src/oneTBB/tree/v2021.5.0>
- Tcl/Tk 8.6: <https://www.tcl-lang.org/software/tcltk/8.6.html>
- zlib 1.2.8: <https://zlib.net/fossils/>

The FFmpeg 3.3.4 corresponding-source archive is published alongside the
portable ZIP as a release asset. La Converteur does not call FFmpeg, FreeImage,
or OpenVR directly; these DLLs are load-time dependencies of the distributed
OCCT Windows build.

This notice is informational and does not replace the license texts supplied
with the release.
