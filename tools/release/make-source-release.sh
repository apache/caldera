#!/usr/bin/env bash
#
# Build the Apache Caldera (incubating) source release from a git ref:
#   apache-caldera-<version>-incubating-src.tar.gz
#   apache-caldera-<version>-incubating-src.tar.gz.asc     (detached signature)
#   apache-caldera-<version>-incubating-src.tar.gz.sha512  (checksum)
#
# The archive is made with `git archive`, so it holds exactly the tracked
# files of the core repository at that ref, plus one generated file,
# plugin-revisions.txt. Plugin submodules are not part of the source release
# and are not included: their directories are empty in the archive, and
# plugin-revisions.txt lists the path, pinned commit and URL of each one so
# that the matching plugins can be fetched (see README.md).
#
# Usage:
#   tools/release/make-source-release.sh [--no-sign] <version> <git-ref>
#
#   version   release version, for example 5.4.0
#   git-ref   tag or commit to package, for example 5.4.0-rc1
#   --no-sign skip the signature (dry run only; the output cannot be released)
#
# Environment:
#   OUT_DIR   output directory (default: dist/<version> under the repository root)
#   GPG_KEY   key id or fingerprint to sign with (default: gpg's default key)
#
# Needs git 2.28 or later (git archive --add-file).
#
# See RELEASING.md for how the files are staged and voted on.

set -euo pipefail

usage() {
    sed -n '/^# Usage:/,/^# See RELEASING/p' "$0" | sed 's/^# \{0,1\}//'
    exit 2
}

sign=1
if [[ "${1:-}" == "--no-sign" ]]; then
    sign=0
    shift
fi
[[ $# -eq 2 ]] || usage
version=$1
ref=$2

if ! [[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo "error: version must look like 5.4.0, got '$version'" >&2
    exit 2
fi

repo_root=$(git -C "$(dirname "$0")" rev-parse --show-toplevel)
cd "$repo_root"

commit=$(git rev-parse --verify --quiet "$ref^{commit}") || {
    echo "error: '$ref' is not a tag or commit in $repo_root" >&2
    exit 1
}

# The packaged tree must declare the version being released. The declaration
# is matched as a fixed string, so the dots in the version are literal.
if ! git show "$commit:app/version.py" | grep -qxF -- "__version__ = '$version'"; then
    echo "error: app/version.py at $ref does not set __version__ = '$version'" >&2
    exit 1
fi

for required in LICENSE NOTICE; do
    git cat-file -e "$commit:$required" 2>/dev/null || {
        echo "error: $required is missing at $ref" >&2
        exit 1
    }
done
if ! git cat-file -e "$commit:DISCLAIMER" 2>/dev/null && ! git cat-file -e "$commit:DISCLAIMER-WIP" 2>/dev/null; then
    echo "error: neither DISCLAIMER nor DISCLAIMER-WIP exists at $ref" >&2
    exit 1
fi

name="apache-caldera-${version}-incubating"
out_dir=${OUT_DIR:-"$repo_root/dist/$version"}
tarball="${name}-src.tar.gz"
mkdir -p "$out_dir"
out_dir=$(cd "$out_dir" && pwd)
rm -f "$out_dir/$tarball" "$out_dir/$tarball.asc" "$out_dir/$tarball.sha512"

# Plugins are separate repositories that the core repository pins as
# submodules. List each submodule of the release commit, with the commit it is
# pinned to and its URL from .gitmodules, in a generated file for the archive.
manifest_name=plugin-revisions.txt
if git cat-file -e "$commit:$manifest_name" 2>/dev/null; then
    echo "error: $manifest_name exists at $ref, but this script generates that file" >&2
    exit 1
fi

# Print the URL that .gitmodules at the release commit gives for a submodule path.
submodule_url() {
    local key value
    while read -r key value; do
        if [[ "$value" == "$1" ]]; then
            key=${key#submodule.}
            git config --blob "$commit:.gitmodules" --get "submodule.${key%.path}.url"
            return
        fi
    done < <(git config --blob "$commit:.gitmodules" --get-regexp '^submodule\..*\.path$')
    return 1
}

work_dir=$(mktemp -d)
trap 'rm -rf "$work_dir"' EXIT
manifest="$work_dir/$manifest_name"
plugin_count=0
{
    echo "# Plugin repositories pinned by Apache Caldera (incubating) $version,"
    echo "# commit $commit."
    echo "# Plugins are not part of this source release. README.md describes how to"
    echo "# fetch them at the commits listed here."
    echo "#"
    echo "# <path> <commit> <url>"
    while read -r mode _ sha path; do
        [[ "$mode" == 160000 ]] || continue
        url=$(submodule_url "$path") || url=
        if [[ -z "$url" || "$path$url" =~ [[:space:]] ]]; then
            echo "error: .gitmodules at $ref has no usable URL for the submodule '$path'" >&2
            exit 1
        fi
        echo "$path $sha $url"
        plugin_count=$((plugin_count + 1))
    done < <(git ls-tree -r "$commit")
} > "$manifest"

git archive --format=tar.gz --prefix="${name}/" --add-file="$manifest" \
    --output="$out_dir/$tarball" "$commit"

# A source release must not contain compiled code.
compiled=$(tar -tzf "$out_dir/$tarball" | grep -E '\.(exe|dll|so|dylib|jar|class|pyc|pyd|o|a)$' || true)
if [[ -n "$compiled" ]]; then
    echo "error: the archive contains compiled files:" >&2
    echo "$compiled" >&2
    exit 1
fi

cd "$out_dir"
if command -v sha512sum >/dev/null 2>&1; then
    sha512sum "$tarball" > "$tarball.sha512"
    sha512sum --check --quiet "$tarball.sha512"
else
    shasum -a 512 "$tarball" > "$tarball.sha512"
    shasum -a 512 --check --quiet "$tarball.sha512"
fi

if [[ $sign -eq 1 ]]; then
    gpg --armor --detach-sign ${GPG_KEY:+--local-user "$GPG_KEY"} --output "$tarball.asc" "$tarball"
    gpg --verify "$tarball.asc" "$tarball"
else
    echo "warning: --no-sign given; $tarball is not signed and cannot be released" >&2
fi

echo
echo "Source release for $version built from $ref ($commit),"
echo "with $plugin_count plugin revisions listed in $manifest_name:"
ls -l "$out_dir/$tarball"*
