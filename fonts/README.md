# Fonts

PROVISIONAL: Inter is a placeholder chosen to get the layout moving. The font choice
affects the printed result, so it needs your approval before the first release.

- Family: Inter (https://rsms.me/inter/), designed by Rasmus Andersson.
- Licence: SIL Open Font License 1.1.
- Source: Google Fonts, via the npm package `@expo-google-fonts/inter` 0.2.3. That wrapper
  is MIT; the font files themselves are the OFL-licensed Google Fonts builds.
- Files: `Inter-Regular.ttf` (400), `Inter-Medium.ttf` (500), `Inter-SemiBold.ttf` (600).
- Missing: the verbatim `OFL.txt` licence text is not in the source package and is not yet
  vendored. Add it here before the first release. An italic cut is also not vendored, so the
  scientific name is set upright for now.
- Integrity: `SHA256SUMS` lists the hash of each file. Build info records these hashes.
