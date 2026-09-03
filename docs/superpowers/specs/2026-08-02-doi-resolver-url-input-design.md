# DOI Resolver URL Input Design

## Goal

Allow the `Update from ADS` identifier input to accept a canonical DOI resolver
URL in the form `https://doi.org/{doi}`.

## Scope

The shared ADS identifier parser will remove an anchored,
case-insensitive `https://doi.org/` prefix before its existing DOI detection and
normalization. The same behavior will therefore apply to the desktop GUI and to
direct CLI uses of `--identifier`.

Existing bare DOI, `DOI:`-prefixed DOI, journal-volume-page, and bibcode input
remain unchanged.

## Deliberate exclusions

This change does not accept `http://`, alternate DOI resolver hosts, URL query
parameters, URL fragments, or URL decoding. Each would expand the accepted
identifier grammar and needs an explicit compatibility decision.

## Implementation and testing

Add a focused parser regression test demonstrating that
`https://doi.org/10.1234/example` resolves as the normalized DOI
`10.1234/example`. Implement the smallest parser-only change needed for that
test to pass.

## Failure behavior

An input that does not match the exact resolver prefix continues through the
existing identifier classification. A resolver URL with no DOI therefore does
not become a DOI lookup.
