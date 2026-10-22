# Qt / Qt for Python source and license information

The Windows application uses Qt, PySide6 and Shiboken6. The pinned dependency
version for this release is 6.11.2. Qt and Qt for Python are developed by The Qt
Company Ltd. and contributors. These libraries have their own licenses and are
not covered by this application's MIT license.

The public PySide6 6.11.2 distribution declares
`LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only`. This distribution uses the LGPLv3
option for the LGPL-covered Qt / Qt for Python libraries. The upstream commercial
license notice describes a separate alternative; it does not replace the
open-source license declaration. See the [versioned PyPI metadata](https://pypi.org/project/PySide6/6.11.2/)
and [Qt's licensing documentation](https://doc.qt.io/qt-6.11/licensing.html).

The complete LGPLv3 and GPLv3 texts are included alongside this file, unchanged
from the Qt 6.11.2 source release. The GPLv3 text accompanies LGPLv3 because the
latter incorporates its terms. Their upstream URLs and exact file hashes are
recorded in `SOURCE-PROVENANCE.json`.

## Corresponding upstream sources

The exact upstream source archives for the pinned library version are available
from Qt's public download servers, without an account:

- [Qt for Python 6.11.2 source ZIP](https://download.qt.io/official_releases/QtForPython/pyside6/PySide6-6.11.2-src/pyside-setup-everywhere-src-6.11.2.zip)
  contains the PySide6 and Shiboken6 sources and build scripts.
- [Qt 6.11.2 complete source ZIP](https://download.qt.io/archive/qt/6.11/6.11.2/single/qt-everywhere-src-6.11.2.zip)
  contains the Qt module sources, build scripts, and upstream third-party notices.
- The [Qt for Python 6.11 build instructions](https://doc.qt.io/qtforpython-6.11/building_from_source/index.html)
  and [Qt 6.11 build instructions](https://doc.qt.io/qt-6.11/build-sources.html)
  describe the required build tools and configuration.

The official archive SHA-256 values and archive sizes are recorded in
`SOURCE-PROVENANCE.json`. These records describe the upstream archives, not a
claim that an archive has been included in this application download. The
application's own source and Windows build recipe are in the public repository
and source archive for the same release tag. A modified dependency build must
also provide its matching source changes and build instructions.

## Replacing the libraries

The Windows build uses PyInstaller's directory distribution with shared Qt
libraries. Keep the whole extracted application directory. The Qt DLLs and
PySide6 / Shiboken6 bindings are outside the main executable under `_internal`.
To test a modified, interface-compatible library, close the application, keep a
backup of the extracted directory, and replace the matching shared libraries
and bindings in that copy. Rebuild the application from the release's source if
a different ABI or dependency configuration requires it. The project places no
additional restriction on modifying those libraries or reverse engineering the
application to debug such modifications. Test a modified copy independently
of any updater that could restore the released binaries.

## Third-party components and release maintenance

Qt also incorporates code under other licenses. The `qt-third-party/` directory
contains 147 text notices: all 146 attribution pages linked by the versioned
[Qt 6.11 third-party index](https://doc.qt.io/qt-6.11/licenses-used-in-qt.html)
for Qt 6.11.2, plus the copyright and license blocks from the
[Qt for Python third-party index](https://doc.qt.io/qtforpython-6.11/licenses.html).
This intentionally includes notices for modules and examples absent from the
application. Including a notice does not mean that its component is shipped or
that its license applies to the application's own source. No third-party art,
fonts, example assets, or component binaries are included in this notice set.
The source URLs, retrieval date, original page hashes, text hashes, and extraction
method are recorded in `qt-third-party/PROVENANCE.json`.

The two GNU license files alone do not replace these component-specific
copyright and license notices. Preserve the complete notice directory when
redistributing the program, and review the inventory again when Qt changes.

Before distributing a binary release, its publisher must check the actual
library and plugin inventory, package the relevant third-party notices, record
the built library versions, and preserve working access to the matching sources.
If an upstream source download is moved or removed, provide an equivalent
accessible copy and update the source instructions accompanying the binary.
These links and notices are technical distribution records, not a declaration
that an arbitrary modified build satisfies every licensing requirement.
