# Releasing Apache Caldera (incubating)

This is the checklist for a release manager (RM). An ASF release is the
signed source archive. Docker images and other builds are convenience
binaries made from an approved release.

Policies this follows:

- Incubator release policy: <https://incubator.apache.org/policy/incubation.html#releases>
- Podling release guide: <https://incubator.apache.org/guides/releasemanagement.html>
- ASF release policy: <https://www.apache.org/legal/release-policy.html>
- Release distribution: <https://infra.apache.org/release-distribution.html>
- Third-party licenses: <https://www.apache.org/legal/resolved.html>

## One-time setup

- **Distribution directories.** Release candidates are staged in
  `https://dist.apache.org/repos/dist/dev/incubator/caldera/`, and approved
  releases go to `https://dist.apache.org/repos/dist/release/incubator/caldera/`
  (served from `https://downloads.apache.org/incubator/caldera/`). If either
  directory is missing, ask the mentors or open an INFRA ticket.
- **Signing key.** Use an RSA key of 4096 bits (at least 2048), created and
  kept on your own machine. Never create or store it on ASF hardware. Add its
  fingerprint to your profile at <https://id.apache.org> and publish it to a
  public keyserver.
- **KEYS.** Append your public key to the `KEYS` file in the release
  directory before you sign anything:

  ```sh
  svn checkout --depth files https://dist.apache.org/repos/dist/release/incubator/caldera caldera-release
  cd caldera-release
  (gpg --list-sigs <KEY_ID> && gpg --armor --export <KEY_ID>) >> KEYS
  svn commit -m "Add <your name>'s key to KEYS" KEYS
  ```

  Never remove a key that signed a release that is still available.

## 1. Prepare the release

- Set `__version__` in `app/version.py` to the release version.
- Check that `LICENSE`, `NOTICE` and `DISCLAIMER-WIP` (or `DISCLAIMER`) are
  current. `LICENSE` must list every bundled third-party file, with license
  texts under `licenses/`, and the release must not contain Category X
  material or compiled code.
- Run the test suite.
- Write the release notes.

## 2. Tag and build the release candidate

```sh
git tag -s 5.4.0-rc1 -m "Apache Caldera (incubating) 5.4.0 RC1"
git push origin 5.4.0-rc1
tools/release/make-source-release.sh 5.4.0 5.4.0-rc1
```

The script writes these files to `dist/5.4.0/`:

- `apache-caldera-5.4.0-incubating-src.tar.gz`
- `apache-caldera-5.4.0-incubating-src.tar.gz.asc`
- `apache-caldera-5.4.0-incubating-src.tar.gz.sha512`

The archive holds the tracked files of the core repository at the tag, plus
one generated file, `plugin-revisions.txt`. Plugin submodules are not part of
the source release, so their directories are empty in the archive.
`plugin-revisions.txt` lists the path, pinned commit and repository URL of
each one, read from `.gitmodules` and the submodule entries of the tag, so
that the same plugins can be fetched for an unpacked archive. The
installation steps in `README.md` show how. Set `GPG_KEY` to choose the
signing key. `--no-sign` gives a dry run that cannot be released.

## 3. Stage the release candidate

```sh
svn checkout --depth empty https://dist.apache.org/repos/dist/dev/incubator/caldera caldera-dev
cd caldera-dev
mkdir 5.4.0-rc1
cp /path/to/caldera/dist/5.4.0/apache-caldera-5.4.0-incubating-src.tar.gz* 5.4.0-rc1/
svn add 5.4.0-rc1
svn commit -m "Stage Apache Caldera (incubating) 5.4.0 RC1"
```

The Apache Trusted Releases platform (<https://releases.apache.org>, in beta)
can stage and publish the files instead.

## 4. Vote

A podling release needs two votes, which can run one after the other or at
the same time. Each vote stays open for at least 72 hours.

1. **PPMC vote** on dev@caldera.apache.org. It passes with at least three +1
   votes from PPMC members and more +1 than -1 votes from PPMC members.
2. **IPMC vote** on general@incubator.apache.org. It passes with at least
   three binding +1 votes from Incubator PMC members and more binding +1 than
   binding -1 votes.

Each `[VOTE]` email gives the tag and its commit id, the staging URL, the
KEYS URL, the SHA-512 checksum, the release notes, and the voting period.
Reviewers check:

```sh
gpg --import KEYS
gpg --verify apache-caldera-5.4.0-incubating-src.tar.gz.asc apache-caldera-5.4.0-incubating-src.tar.gz
sha512sum --check apache-caldera-5.4.0-incubating-src.tar.gz.sha512
```

They also check that the file name includes "incubating", that LICENSE,
NOTICE and the disclaimer are present and correct, that the archive matches
the tag, and that the tests run. The only file in the archive that is not in
the tag is `plugin-revisions.txt`; its entries match the output of
`git ls-tree 5.4.0-rc1 plugins/`. The server, and the tests that use plugin
files, need the plugins. Fetch them into the unpacked archive as `README.md`
describes, or clone the tag with `--recursive`, which gives the same commits.

Close each vote with a `[RESULT][VOTE]` email that lists the votes. If the
candidate fails, fix the problems, tag `5.4.0-rc2` and start again from step 2.

## 5. Publish

After both votes pass:

```sh
svn mv https://dist.apache.org/repos/dist/dev/incubator/caldera/5.4.0-rc1 \
       https://dist.apache.org/repos/dist/release/incubator/caldera/5.4.0 \
       -m "Release Apache Caldera (incubating) 5.4.0"
git tag -s 5.4.0 5.4.0-rc1^{} -m "Apache Caldera (incubating) 5.4.0"
git push origin 5.4.0
```

- Wait at least an hour for the CDN, then update the download page. Link the
  archive through `https://www.apache.org/dyn/closer.lua/incubator/caldera/5.4.0/...`,
  and link the `.asc`, `.sha512` and `KEYS` files on
  `https://downloads.apache.org/incubator/caldera/`.
- Create the GitHub release for the tag.
- Publish the Docker images by running the "Create and publish a Docker
  image" workflow by hand with the release tag and the link to the IPMC
  `[RESULT][VOTE]` email.
- Announce the release from your @apache.org address to announce@apache.org
  (moderated, so it can take hours to appear), with copies to
  dev@caldera.apache.org and general@incubator.apache.org. Link the download
  page rather than the files, and include the incubation disclaimer.
- Remove releases that are no longer current from the release directory.
  They stay available on <https://archive.apache.org/dist/incubator/caldera/>.
