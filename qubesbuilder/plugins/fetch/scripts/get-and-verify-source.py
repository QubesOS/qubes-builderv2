#!/usr/bin/env python3
#
# The Qubes OS Project, http://www.qubes-os.org
#
# Copyright (C) 2023 Frédéric Pierret (fepitre) <frederic@invisiblethingslab.com>
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along
# with this program. If not, see <https://www.gnu.org/licenses/>.
#
# SPDX-License-Identifier: GPL-3.0-or-later

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List


def verify_git_obj(gpg_client, keyring_dir, repository_dir, obj_type, obj_path):
    try:
        # Run git command and capture the output
        command = [
            "git",
            "-c",
            f"gpg.program={gpg_client}",
            "-c",
            "gpg.minTrustLevel=fully",
            f"verify-{obj_type}",
            "--raw",
            "--",
            obj_path,
        ]

        env = os.environ.copy()
        env["GNUPGHOME"] = str(keyring_dir)
        output = subprocess.run(
            command,
            capture_output=True,
            universal_newlines=True,
            cwd=repository_dir,
            env=env,
            check=True,
        ).stderr

        # Count the occurrences of [GNUPG:] NEWSIG in the output
        newsig_number = output.count("[GNUPG:] NEWSIG")

        # Check if there is exactly one [GNUPG:] NEWSIG and if TRUST_FULLY or TRUST_ULTIMATE is 0
        if newsig_number == 1:
            if any(
                line.startswith("[GNUPG:] TRUST_FULLY 0 pgp")
                or line.startswith("[GNUPG:] TRUST_ULTIMATE 0 pgp")
                for line in output.splitlines()
            ):
                valid_sig_key = None
                valid_sig_re = re.compile(
                    r"^\[GNUPG:\] VALIDSIG ([a-fA-F0-9]{40}) [0-9]{4}-[0-9]{2}-[0-9]{2}.*"
                )
                for line in output.splitlines():
                    parsed_sig = valid_sig_re.match(line)
                    if parsed_sig:
                        valid_sig_key = parsed_sig.group(1)
                if valid_sig_key:
                    return valid_sig_key
    except subprocess.CalledProcessError as e:
        print(f"ERROR: {e!r}; stderr: {e.stderr}")


def main(args):
    # Sanity check on branch and repo
    if not re.match(r"^[A-Za-z0-9][A-Za-z0-9/._-]+$", args.git_branch):
        raise ValueError(f"Invalid branch {args.git_branch}")
    elif not re.match(r"^/[A-Za-z][A-Za-z0-9-/_]*$", args.component_directory):
        raise ValueError(
            f"Invalid repository directory {args.component_directory}"
        )

    git_url = args.component_repository
    repo = Path(args.component_directory).expanduser().resolve()
    keys_dir = Path(args.keys_dir).expanduser().resolve()
    git_keyring_dir = Path(args.git_keyring_dir).expanduser().resolve()

    git_branch = args.git_commit or args.git_branch
    clean = args.clean
    fetch_only = args.fetch_only
    ignore_missing = args.ignore_missing
    insecure_skip_checking = args.insecure_skip_checking
    less_secure_signed_commits_sufficient = (
        args.less_secure_signed_commits_sufficient
    )
    fetch_versions_only = args.fetch_versions_only
    maintainers = args.maintainer or []
    minimum_distinct_maintainers = int(args.minimum_distinct_maintainers)

    gpg_sequoia = "/usr/bin/gpg-sq"
    gpg = "/usr/bin/gpg"

    if Path(gpg_sequoia).exists():
        gpg_client = gpg_sequoia
    elif Path(gpg).exists():
        gpg_client = gpg
    else:
        raise ValueError(
            "Cannot find GnuPG or GnuPG-compatible Sequoia Chameleon."
        )

    # Validity check on provided maintainers
    for maintainer in maintainers:
        if not re.match(r"^[a-fA-F0-9]{40}$", maintainer):
            raise ValueError(f"Invalid maintainer provided: {maintainer}")

    if args.trust_all_keys and maintainers:
        raise ValueError(
            "--maintainer cannot be used together with --trust-all-keys"
        )

    # Define common git options
    git_options: List[str] = []
    git_merge_opts = ["--ff-only"]

    if args.shallow_clone:
        git_options += ["--depth=1"]

    fresh_clone = False
    if clean:
        shutil.rmtree(repo)
    if (repo / ".git").is_dir():
        try:
            subprocess.run(
                ["git", "fetch"]
                + git_options
                + ["-q", "--tags", "--", git_url, git_branch],
                capture_output=True,
                check=True,
                cwd=repo,
            )
        except subprocess.CalledProcessError as e:
            if ignore_missing:
                return
            else:
                raise e

        rev = subprocess.run(
            ["git", "rev-parse", "-q", "--verify", "FETCH_HEAD^{commit}"],
            capture_output=True,
            text=True,
            cwd=repo,
            check=True,
        ).stdout.strip()

        if fetch_versions_only:
            tags = (
                subprocess.run(
                    ["git", "tag", "--points-at", rev],
                    capture_output=True,
                    text=True,
                    cwd=repo,
                    check=True,
                )
                .stdout.strip()
                .splitlines()
            )
            version_tags = [tag for tag in tags if tag.startswith("v")]
            if not version_tags:
                print("No version tag.")
                os.remove(repo / ".git/FETCH_HEAD")
                return
        verify_ref = rev
    else:
        if repo.exists():
            shutil.rmtree(repo)
        try:
            looks_like_commit = re.match(r"^[a-fA-F0-9]{40}$", git_branch)
            if args.git_commit or looks_like_commit:
                # git clone can't handle commit reference, use fetch instead
                repo.mkdir()
                subprocess.run(
                    ["git", "init"],
                    capture_output=True,
                    cwd=repo,
                    check=True,
                )
                subprocess.run(
                    ["git", "fetch"]
                    + (["--tags"] if looks_like_commit else [])
                    + git_options
                    + ["--", git_url, git_branch],
                    capture_output=True,
                    cwd=repo,
                    check=True,
                )
                subprocess.run(
                    ["git", "reset", "-q", "--soft", "FETCH_HEAD"],
                    capture_output=True,
                    cwd=repo,
                    check=True,
                )
            else:
                subprocess.run(
                    ["git", "clone"]
                    + git_options
                    + ["-n", "-q", "-b", git_branch]
                    + ["--", git_url, str(repo)],
                    capture_output=True,
                    check=True,
                )
        except subprocess.CalledProcessError as e:
            if ignore_missing:
                return
            else:
                raise e

        if fetch_versions_only:
            vtag = subprocess.run(
                [
                    "git",
                    "describe",
                    "--match=v*",
                    "--abbrev=0",
                    "HEAD",
                ],
                capture_output=True,
                text=True,
                cwd=repo,
            ).stdout.strip()
            if vtag and vtag.startswith("v"):
                verify_ref = f"{vtag}^{{commit}}"
            else:
                raise ValueError("No version tag.")
        else:
            verify_ref = "HEAD"
        fresh_clone = True

    check = "signed-tag"
    verify = True
    if insecure_skip_checking:
        verify = False
    elif less_secure_signed_commits_sufficient:
        check = "signed-tag-or-commit"
    elif args.git_commit:
        # user specified pre-verified commit-id
        verify = False

    verify_ref = subprocess.run(
        ["git", "rev-parse", "-q", "--verify", verify_ref],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    if not verify_ref:
        raise ValueError("Cannot determine reference to verify!")

    if verify is False:
        print("--> NOT verifying tags")
    else:
        if check == "signed-tag-or-commit":
            print("--> Verifying tags or commits...")
        else:
            print("--> Verifying tags...")

        env = os.environ.copy()
        env["GNUPGHOME"] = str(git_keyring_dir)
        git_keyring_dir.mkdir(parents=True, exist_ok=True)
        git_keyring_dir.chmod(0o700)
        # We request a list to init the keyring. It looks like it does
        # not do it on first import, so we just show available keys.
        subprocess.run(
            [gpg_client, "--list-keys"],
            capture_output=True,
            check=True,
            env=env,
        )
        if args.trust_all_keys:
            for file in keys_dir.glob("*"):
                subprocess.run(
                    [gpg_client, "--import", str(file)],
                    check=True,
                    env=env,
                    capture_output=True,
                )
            list_keys = subprocess.run(
                [gpg_client, "--list-keys", "--with-colons"],
                capture_output=True,
                check=True,
                env=env,
            )
            for line in list_keys.stdout.splitlines():
                if not line.startswith(b"fpr:"):
                    continue
                keyid = line.decode().split(":")[9]
                subprocess.run(
                    [gpg_client, "--import-ownertrust"],
                    input=f"{keyid}:6:\n",
                    capture_output=True,
                    text=True,
                    env=env,
                    check=True,
                )

        for keyid in maintainers:
            key_path = keys_dir / f"{keyid}.asc"
            if not key_path.exists():
                raise ValueError(f"Cannot find {key_path}")
            subprocess.run(
                [gpg_client, "--import", keys_dir / f"{keyid}.asc"],
                check=True,
                env=env,
                capture_output=True,
            )
            subprocess.run(
                [gpg_client, "--import-ownertrust"],
                input=f"{keyid}:6:\n",
                capture_output=True,
                text=True,
                env=env,
                check=True,
            )

        subprocess.run(
            ["gpgconf", "--kill", "gpg-agent"], check=True, capture_output=True
        )
        expected_hash = verify_ref
        hash_len = len(expected_hash)

        if hash_len != 40 and hash_len != 64:
            raise ValueError("---> Bad Git hash value (wrong length); failing")
        elif not all(c in "abcdef0123456789" for c in expected_hash):
            raise ValueError("---> Bad Git hash value (bad character); failing")

        # Git format string, see man:git-for-each-ref(1) for details.
        #
        # The %(if)...%(then)...%(end) skips lightweight tags, which have no object to
        # point to.  The colons allow a SHA-1 hash to be distinguished from a truncated
        # SHA-256 hash, and also allow a truncated line to be detected.
        format_str = (
            "%(if:equals=tag)%(objecttype)%(then)%(objectname):%(object):%(end)"
        )
        tags = subprocess.run(  # type: ignore
            [
                "git",
                "tag",
                f"--points-at={expected_hash}",
                f"--format={format_str}",
            ],
            capture_output=True,
            text=True,
            cwd=repo,
            check=True,
        ).stdout.strip()[:500]

        verified_tags = set()
        for tag in tags.split():  # type: ignore
            if len(tag) != hash_len * 2 + 2:
                raise ValueError(
                    "---> Bad Git hash value (wrong length); failing"
                )
            elif tag[hash_len:] != f":{expected_hash}:":
                raise ValueError(
                    f"---> Tag has wrong hash (found {tag[hash_len + 1:hash_len]}, expected {expected_hash})"
                )
            tag = tag[:hash_len]
            valid_sig_key = verify_git_obj(
                gpg_client=gpg_client,
                keyring_dir=git_keyring_dir,
                repository_dir=repo,
                obj_type="tag",
                obj_path=tag,
            )
            if valid_sig_key:
                verified_tags.add(valid_sig_key)
                print(f"---> Good tag {tag}.")
            else:
                print(f"---> Invalid tag {tag}.")

        if tags:
            if len(verified_tags) < minimum_distinct_maintainers:
                raise ValueError(
                    f"Not enough distinct tag signatures. Found {len(verified_tags)}, mandatory minimum is {minimum_distinct_maintainers}."
                )
            else:
                print(
                    f"Enough distinct tag signatures. Found {len(verified_tags)}, mandatory minimum is {minimum_distinct_maintainers}."
                )

        if not tags:
            print(f"---> No tag pointing at {expected_hash}")
            if verify_git_obj(
                gpg_client=gpg_client,
                keyring_dir=git_keyring_dir,
                repository_dir=repo,
                obj_type="commit",
                obj_path=expected_hash,
            ):
                if check == "signed-tag-or-commit":
                    print(
                        f"---> {expected_hash} does not have a signed tag. However, it is signed by a trusted key, and CHECK is set to {check}. Accepting it anyway."
                    )
                elif check == "signed-tag":
                    raise ValueError(
                        f"---> {expected_hash} is a commit signed by a trusted key. Did the signer forget to add a tag?"
                    )
                else:
                    raise ValueError("---> Internal error (this is a bug).")
            else:
                raise ValueError(f"---> Invalid commit {expected_hash}.")

    if fetch_only:
        return

    current_git_branch = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True,
        text=True,
        cwd=repo,
        check=True,
    ).stdout.strip()
    if current_git_branch != git_branch or fresh_clone:
        if subprocess.run(
            ["git", "name-rev", "--name-only", git_branch],
            capture_output=True,
            text=True,
            cwd=repo,
            check=True,
        ).stdout.strip():
            print(
                f"--> Switching branch from {current_git_branch} branch to {git_branch}"
            )
            if not fresh_clone:
                subprocess.run(
                    [
                        "git",
                        "merge-base",
                        "--is-ancestor",
                        git_branch,
                        verify_ref,
                    ],
                    capture_output=True,
                    text=True,
                    cwd=repo,
                    check=True,
                )
            subprocess.run(
                ["git", "checkout", "-B", git_branch, verify_ref],
                check=True,
                cwd=repo,
                capture_output=True,
            )
        else:
            print(
                f"--> Switching branch from {current_git_branch} branch to new {git_branch}"
            )
            subprocess.run(
                ["git", "checkout", verify_ref, "-b", git_branch],
                check=True,
                cwd=repo,
                capture_output=True,
            )

    if not fresh_clone:
        print("--> Merging...")
        subprocess.run(
            ["git", "-c", "merge.verifySignatures=no", "merge"]
            + git_merge_opts
            + ["--commit", "-q", verify_ref],
            check=True,
            cwd=repo,
            capture_output=True,
        )
        tracking_branch = f"refs/remotes/origin/{git_branch}"
        if os.path.isfile(repo / f".git/{tracking_branch}"):
            subprocess.run(
                ["git", "update-ref", "--", tracking_branch, verify_ref],
                check=True,
                cwd=repo,
                capture_output=True,
            )

    if (repo / ".gitmodules").exists():
        print("--> Updating submodules")
        subprocess.run(
            ["git", "submodule", "init"],
            check=True,
            cwd=repo,
            capture_output=True,
        )
        subprocess.run(
            ["git", "submodule", "update", "--recursive"],
            check=True,
            cwd=repo,
            capture_output=True,
        )


def get_args():
    parser = argparse.ArgumentParser()

    # mandatory args
    parser.add_argument(
        "component_repository", help="The repository to clone from."
    )
    parser.add_argument(
        "component_directory",
        help="The name of a new directory to clone into.",
    )
    parser.add_argument(
        "git_keyring_dir",
        metavar="git-keyring-dir",
        help="Directory to create component Git keyring.",
    )
    parser.add_argument(
        "keys_dir",
        metavar="keys-dir",
        help="Directory containing keys to the armor format.",
    )

    # optional args
    parser.add_argument("--git-branch", help="Git branch.", default="main")
    parser.add_argument(
        "--git-commit",
        help="Specific git commit id - full sha. Takes precedence over branch and does not require signed tag.",
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="Remove previous sources (use git up vs git clone).",
    )
    parser.add_argument(
        "--shallow-clone",
        action="store_true",
        help="Fetch git repo with --depth=1 to reduce amount of data.",
    )
    parser.add_argument(
        "--fetch-only",
        action="store_true",
        help="Fetch sources but do not merge.",
    )
    parser.add_argument(
        "--fetch-versions-only",
        action="store_true",
        help="Fetch only version tags.",
    )
    parser.add_argument(
        "--ignore-missing",
        action="store_true",
        help="Exit with code 0 if remote branch doesn't exist.",
    )
    parser.add_argument(
        "--insecure-skip-checking",
        action="store_true",
        help="Disable signed tag checking.",
    )
    parser.add_argument(
        "--less-secure-signed-commits-sufficient",
        action="store_true",
        help="Allow signed commits instead of requiring signed tags. This is less secure because only commits that have been reviewed are tagged.",
    )
    parser.add_argument(
        "--maintainer",
        action="append",
        help="Allowed maintainer provided as KEYID assumed to be available as KEYID.asc under provided 'keys-dir' directory. Can be used multiple times.",
    )
    parser.add_argument(
        "--trust-all-keys",
        action="store_true",
        help="Import and trust all keys present in *keys-dir*. Conflicts with --maintainer.",
    )
    parser.add_argument(
        "--minimum-distinct-maintainers",
        help="Minimum of mandatory distinct maintainer signatures.",
        default=1,
    )
    return parser.parse_args()


if __name__ == "__main__":
    try:
        main(get_args())
    except Exception as e:
        if isinstance(e, subprocess.CalledProcessError):
            print(f"args: {e.args}")
            print(f"stdout: {e.stdout}")
            print(f"stderr: {e.stderr}")
        else:
            print(str(e))
        sys.exit(1)
