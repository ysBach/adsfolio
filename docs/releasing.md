# Publishing releases

The repository scripts prepare GitHub repositories, native application releases,
a Homebrew tap, and a static website. Remote-changing commands print a preview
unless you supply `--apply`. Nothing is published by installing the package.

## Repository setup

Install Git, [uv](https://docs.astral.sh/uv/), and the
[GitHub CLI](https://cli.github.com/). Authenticate with `gh auth login`.
The default repository is `ysBach/adsfolio`; use `--repository OWNER/NAME` for a fork.

```sh
uv run --no-sync python scripts/release.py repository
uv run --no-sync python scripts/release.py repository --apply
```

The script initializes Git only when needed. Existing history is retained.
An existing `origin` becomes `previous-origin`, then the new repository becomes
`origin`. If `origin` already points to the requested repository, rerunning setup verifies
that repository without changing remotes or creating it again. Otherwise,
creation stops if the remote repository already exists. For an existing
repository, inspect it and configure `origin` manually instead of creating it again.
The script does not stage files, commit changes, or push history. A newly created
GitHub repository therefore remains empty until the first push.

Review the working tree, including untracked files, before the first commit.
Keep credentials, catalogs, personal PDFs, build outputs, and local reports out
of Git. After review, commit the intended files and push `main` to the new remote.
The old Git remote can be removed after the new repository is verified.

## Prepare and publish a version

1. Set the version. This example is the first stable release:

   ```sh
   uv run --no-sync python scripts/release.py version 0.1.0 --apply
   ```

2. Update `CHANGELOG.md` and check the package:

   ```sh
   uv run --no-sync python scripts/release.py check
   ```

3. Run the **Desktop bundles** workflow manually. Inspect all four platform
   results and test the downloads on clean target systems. Complete code signing
   and dependency-license review before claiming production readiness.
4. Commit the reviewed version, lockfile, and documentation. The release script
   requires a clean working tree on `main` with the intended `origin`.
5. Preview and create the release tag:

   ```sh
   uv run --no-sync python scripts/release.py tag
   uv run --no-sync python scripts/release.py tag --apply
   ```

The script repeats local checks, creates an annotated tag, and pushes `main` and
that tag atomically. No force push is used. If the push fails, inspect the local
tag before retrying; the script never deletes a tag automatically.

Tag workflows check exact version equality, run package checks, and build macOS
ARM64, macOS Intel, Windows x86-64, and Linux x86-64 archives. They run frozen
application smoke tests, verify checksums, and create a GitHub Release with
Python distributions and desktop downloads. Development and prerelease versions
are marked as prereleases. Stable releases also include `adsfolio.rb` for Homebrew.
Existing release assets are not overwritten.

GitHub-hosted runners cannot establish every supported desktop configuration.
See [platform requirements and acceptance checks](desktop.md). Signing and
notarization credentials are not included in this repository.

## Optional PyPI publication

Configure a PyPI trusted publisher for the project `adsfolio`, the repository,
workflow `desktop.yml`, and environment `pypi`. Create that GitHub environment
with your chosen reviewer rules. Set the repository variable `PUBLISH_PYPI=true`.
The workflow publishes the already-built wheel and source archive only after
the GitHub Release job succeeds. It uses short-lived OIDC credentials, not a
stored API token. Project-name availability and publisher setup must be checked
on PyPI; repository scripts do not reserve a name.

## Homebrew tap

Run this after a stable GitHub Release exists. Ruby must be available for the
cask syntax check. Git commits use your configured Git identity.

```sh
uv run --no-sync python scripts/publish_tap.py 0.1.0 --create
uv run --no-sync python scripts/publish_tap.py 0.1.0 --create --apply
```

This creates `ysBach/homebrew-adsfolio`, downloads the release checksums, generates
`Casks/adsfolio.rb`, checks its Ruby syntax, and commits and pushes the cask.
For later releases, omit `--create`. Use `--repository` and `--tap` for other
owners. A cask requires checksums for both native macOS archives. No placeholder
checksum or `:no_check` is used. Do not publish a cask before native acceptance.

Users can then run:

```sh
brew install --cask ysBach/adsfolio/adsfolio
brew upgrade --cask adsfolio
```

## Website and documentation

The static landing page lives in `website/`. It has no build-time JavaScript
framework, tracking scripts, or external fonts. The build includes strict
Sphinx documentation under `docs/` and works beneath a repository URL prefix.

```sh
uv sync --locked --extra gui --group docs
uv run --no-sync python scripts/build_site.py
uv run --no-sync python -m http.server 8000 --directory _site
```

Open `http://localhost:8000`. Before public deployment, update the first-release
notice and download status to match the artifacts actually available.

In GitHub **Settings → Pages**, select **GitHub Actions** as the source.
Set `PUBLISH_PAGES=true` in repository variables and run **Documentation website**.
The expected project URL is `https://ysbach.github.io/adsfolio/`. Pull requests
build the site but cannot deploy it. Until Pages and the variable are configured,
website builds remain available as workflow artifacts without publishing.

## Data directories and the full rename

The public package namespace, CLI, application identifiers, and new storage
paths use `adsfolio`. Update Python imports and scripts to the new name. Earlier
command aliases are not installed. `ADS_API_TOKEN` still names the NASA ADS
service credential, not the application.

On first use, the application copies earlier configuration and cache directories
into the ADSfolio directories if the destination does not exist. SQLite uses the
backup API, including committed WAL data. Catalog IDs, file history, user text,
and manual field locks are retained. Reserved metadata keys are translated in a
transaction. Conflicting old and new reserved keys stop migration instead of
choosing a value. An existing destination is never merged or overwritten.

Close the earlier application before migration. Keep the original directories
until you verify the new catalog. Earlier per-library sidecars remain readable.
Their old names exist only in migration readers, migration tests, and ignore
rules that prevent private catalog files from entering Git. Old Git history is
retained; the rename does not rewrite commits or modify bibliographic user text.
