# vibe

A tiny Git client that only talks to
**https://lol.tevproject.com/NIX/Chronos**.

One file, Python 3.8+ standard library only. No `git` binary, no Node, no pip
installs. It speaks Git's smart-HTTP protocol directly: it builds and parses
real packfiles, resolves deltas, and writes real Git objects, so the server
can't tell the difference between vibe and git.

## Install

```bash
chmod +x install.sh
./install.sh          # drops `vibe` and the `vb` symlink into ~/.local/bin
```

Or by hand:

```bash
install -m 755 vibe.py ~/.local/bin/vibe
ln -sf ~/.local/bin/vibe ~/.local/bin/vb
```

## First run

Chronos is private, so every request needs credentials.

1. Make a token at https://lol.tevproject.com/user/settings/applications
   with **read + write** repository scope.
2. ```bash
   vb login
   vb config --global user.name "Nico"
   vb config --global user.email "you@example.com"
   ```
3. ```bash
   vb clone          # creates ./Chronos
   cd Chronos
   ```

Credentials live in `~/.config/vibe/credentials.json` (mode 0600). `VIBE_USERNAME`
and `VIBE_TOKEN` override the file if you'd rather use env vars in CI.

## Everyday use

```bash
vb status                        # branch state + what changed
vb push -m "fix the thing"       # commit everything and push in one step
vb pull                          # fetch, then fast-forward or three-way merge
vb merge origin/main             # explicit merge
vb log -n 20
vb branch                        # list branches
vb checkout -b feature
vb merge --abort                 # bail out of a conflicted merge
vb remote                        # show the fixed Gitea remote + who you're logged in as
vb mirror                        # sync every Gitea branch, both directions
vb github-pull OWNER/REPO        # ONE-SHOT: import every GitHub branch into Gitea
vb versions                      # list recoverable past versions
vb recover <id>                  # restore that version and push recovery to Gitea
```

### mirror

`pull` and `push` operate on the current branch. `mirror` reconciles all of
them in a single fetch and a single push:

```bash
vb mirror              # both directions
vb mirror --dry-run    # report only
vb mirror --down       # fetch and fast-forward local branches only
vb mirror --up         # push local branches only
```

Per branch, it does one of:

| Situation | Action |
| --- | --- |
| on origin, not local | create the local branch |
| local behind origin | fast-forward the ref (and the working tree, if it's the current branch) |
| local ahead of origin | queue it for push |
| local only | queue it for push as a new branch |
| both moved | report as `stuck`, change nothing, exit 1 |
| current branch is dirty | skip that branch, sync the rest |

Every queued branch ships in **one packfile and one HTTP request**, with the
object set computed as the union of what each new tip reaches minus everything
any remote ref already reaches — so pushing ten branches that share history
doesn't send that history ten times.

Divergence is never resolved automatically. Merging a branch that isn't checked
out would mean resolving conflicts with no working tree to resolve them in, so
mirror hands it back to you instead.

There's no staging area. `vb commit` and `vb push -m` snapshot the whole
working tree, minus anything matched by `.gitignore` / `.vibeignore`.

### One-shot GitHub import

This does **not** install or call `git`, Node, npm, pip, or any other program.
`vibe` uses Python's standard library and Git smart-HTTP directly. The first time,
give it the GitHub repository:

```bash
vb github-pull https://github.com/OWNER/REPO
```

The GitHub URL is remembered inside `.vibe/config.json`, so later you can just run:

```bash
vb github-pull              # every branch
vb github-pull --dry-run    # report only, change nothing
vb github-pull --branch main  # just one, whether or not you're standing on it
```

It reads **every** branch GitHub has and writes the result only to the fixed Gitea
repository at `https://lol.tevproject.com/NIX/Chronos`. Nothing is ever sent to
GitHub. Per branch, it does one of:

| Situation | Action |
| --- | --- |
| on GitHub, not on Gitea | create the branch on Gitea |
| Gitea behind GitHub | fast-forward it to GitHub |
| both moved | import commit: both histories kept, files come out as GitHub's |
| already equal, or Gitea ahead | leave it alone |
| you have commits Gitea lacks | skip that branch, `vb push` first |
| on Gitea, not on GitHub | leave it alone, never deleted |

Every branch it changes ships in **one packfile and one HTTP request**, the same
way `mirror` does. Your working tree is only touched for the branch you're
currently on. If any branch was skipped the command exits 1, so a script notices.

**The import commit takes GitHub's files wholesale.** History is preserved - both
tips become parents, nothing is force-pushed - but the resulting tree is GitHub's,
so a file that exists only on Gitea is gone from that branch afterwards. It stays
recoverable (`vb versions`, `vb recover`), but this is a copy, not a merge: GitHub
wins every file. If you want the two sets of changes combined instead, that's
`vb pull`.

For a private GitHub repository, set `VIBE_GITHUB_TOKEN` in the environment before
running the command. The saved Gitea token is never sent to GitHub.

### Recovering an older version

```bash
vb versions                 # show version IDs
vb recover a1b2c3d4         # restore it and push the recovery commit to Gitea
vb recover a1b2c3d4 --no-push  # restore locally only, no network at all
vb recover HEAD~3           # counting back from where you are works too
```

Recovery is non-destructive. It does **not** rewind or force-push history. Instead,
it creates a new commit whose files match the selected old version, so every older
version remains recoverable afterward.

### Conflicts

`vb pull` on diverged history runs a real diff3 merge. Clean merges commit
automatically. Conflicts get standard markers:

```
<<<<<<< main
your version
||||||| base
what it was before
=======
their version
>>>>>>> origin/main
```

Fix the files, then `vb commit -m "merge origin/main"`. vibe refuses to commit
while `<<<<<<<` markers are still in the file (use `-f` to override), and
blocks `push` / `checkout` / another `merge` until the current one is settled.

## Repo layout

vibe stores its data in `.vibe/`, not `.git/`, so it can live next to a normal
git checkout without either one confusing the other:

```
.vibe/
  objects/ab/cdef...      loose objects, zlib-compressed, identical to git's
  refs/heads/main
  refs/remotes/origin/main
  HEAD
  index.json              path -> (mode, blob sha) snapshot
  config.json
  MERGE_HEAD              only during a merge
  MERGE_CONFLICTS
```

## What's implemented

- Smart-HTTP v1 over TLS: `info/refs`, `git-upload-pack`, `git-receive-pack`
- `want` / `have` negotiation, so pulls only download new objects
- Packfile reader: type/size varints, `OBJ_OFS_DELTA`, `OBJ_REF_DELTA`
  (including thin packs resolved against local objects), SHA-1 trailer check
- Packfile writer for push, sending only objects the remote lacks
- `side-band-64k` demultiplexing; remote progress goes to stderr
- diff3 three-way merge with conflict markers, plus binary and
  delete-vs-edit conflict handling
- `.gitignore` subset: globs, negation, directory-only, anchored patterns,
  nested per-directory files
- Fast-forward protection on push (`-f` to override)
- `HEAD~2`, `<id>^` style revisions wherever a version/commit id is accepted
- Checking out a bare commit id detaches HEAD and says so. Committing from
  there leaves your branch alone instead of quietly rewriting its tip - keep
  the commit with `vb branch <name>`

## Known limits

- Fetches `refs/heads/*` only (Gitea and one-shot GitHub imports) — no tags, no shallow clones, no submodules
- Every fetch walks the commits and trees its refs reach to confirm nothing is
  missing. Blobs are only checked for existence, so this costs about a tenth of
  a second on a few hundred commits, but it is proportional to history size
- No rebase, cherry-pick, stash, reset, or diff
- Push always sends whole objects (no delta compression), so pushes are
  bigger on the wire than git's — fine for source trees, slow for large binaries
- Loose objects only; nothing repacks `.vibe/objects`
- File modes: `100644`, `100755`, `120000` symlinks

## Environment variables

| Variable | Effect |
| --- | --- |
| `VIBE_USERNAME`, `VIBE_TOKEN` | credentials, overrides the stored file |
| `VIBE_AUTHOR_NAME`, `VIBE_AUTHOR_EMAIL` | commit identity |
| `VIBE_INSECURE=1` | skip TLS verification (self-signed certs) |
| `NO_COLOR=1` | plain output |

## Versions

**1.3.0** — `vb github-pull` now imports **every** GitHub branch, not just the one
you're standing on, sending them all in a single packfile like `mirror` does. Adds
`--dry-run`; `--branch` now works for a branch you have not checked out. Branches
carrying commits Gitea lacks are skipped rather than quietly swept up, and
Gitea-only branches are never deleted.

**1.2.2** — a fetch no longer trusts a half-populated store. vibe checks that it
really holds every object its refs reach before claiming `have` for them, so an
interrupted download repairs itself on the next `pull` instead of failing forever
with `object ... is missing from the local store`. Gitea and GitHub fetches now
run through one code path.

**1.2.1** — bug fixes: committing or merging on a detached HEAD no longer
rewrites `main` and drops its commits; `vb recover --no-push` is fully offline;
a refused `vb checkout -b` no longer leaves the branch behind; conflicted paths
containing spaces are reported whole; packfiles whose deltas arrive before their
bases resolve instead of failing; the installer puts the directory it actually
installed into on your `PATH`.

**1.2.0** — fixed the only writable remote to `https://lol.tevproject.com/NIX/Chronos`;
added native `vb github-pull` for one-shot GitHub imports; added `vb versions` and
non-destructive `vb recover`. Still Python 3.8+ standard library only: no Git, Node,
pip, or package downloads.

**1.1.0** — added `vb mirror` for syncing every branch in both directions.
Push sends multiple refs in a single request and packfile, so `mirror` costs one
round trip regardless of how many branches moved.

**1.0.0** — initial release: `clone`, `pull`, `push`, `merge`, plus `commit`,
`status`, `log`, `branch`, `checkout`, `config`, `login`, `remote`.
