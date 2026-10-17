# Wealthy design assets

`references/` contains the user-supplied implementation pack copied from
`/home/ww/Pictures/Wealthy_UI_Implementation_Pack.zip` after archive safety
inspection.  The archive SHA-256 is
`48f1d2a5241654d579521e254600fbfaf44931cf4c379d152dc9f5ccbb291a08`.

The screens contain **mock names, holdings, amounts, tickers, and returns**.
They are visual references only and must never be presented as a real user's
financial data.  `00_전체_화면_구성도_시안.png` includes C2PA metadata stating
that it was produced by OpenAI Media Service/ChatGPT; the other supplied PNGs
have no textual EXIF metadata detected by the 2026-10-11 audit.

Directories:

- `references/`: immutable supplied references and their original guide.
- `approved/`: reviewed assets approved for product use (currently empty).
- `screenshots/`: implementation captures using sample-only data (not shipped
  in a source release unless explicitly allowlisted).
- `comparison/`: documented visual comparisons (currently empty).

The web implementation follows the newer approved light/dark tokens in
`UI_DESIGN_SPEC.md` where they differ from the reference pack.  Real financial
information, account identifiers, credentials, and user-specific captures are
never allowed in these directories.

