# vibe — setup

`vibe` is a small Git client that only talks to the **Chronos** repo:
https://lol.tevproject.com/NIX/Chronos

You don't need `git` installed. You don't need Node. You don't need to install
any packages. Just Python 3.8 or newer, which macOS and Linux usually have.

You should have two files: **`vibe.py`** and **`install.sh`**. Keep them in the
same folder.

---

## macOS / Linux

Open Terminal and `cd` to the folder with the two files. If you're not sure how,
type `cd ` (with the space), then drag the folder from Finder onto the Terminal
window and press Enter.

```bash
chmod +x install.sh
./install.sh
```

Then **open a new Terminal window** (this matters — the old one doesn't know
about the new command yet) and check it worked:

```bash
vb --version
```

You should see `vibe 1.3.0` (or newer).

### Log in

Chronos is private, so you need an access token.

1. Go to https://lol.tevproject.com/user/settings/applications
2. Under **Manage Access Tokens**, give it a name like `vibe`
3. Set the repository permission to **Read and Write**
4. Click **Generate Token** and copy the string it shows you —
   it's only displayed once

```bash
vb login
```

It asks for your username (your Gitea login name, not your display name) and
then the token. The token won't appear as you type — that's normal, just paste
and press Enter.

Use the token, not your account password. Gitea rejects passwords on this kind
of connection when two-factor auth is turned on.

### Tell it who you are

```bash
vb config --global user.name  "Your Name"
vb config --global user.email "you@example.com"
```

This is what shows up next to your commits.

### Get the code

```bash
cd ~/Documents        # or wherever you keep projects
vb clone
cd Chronos
```

Done.

---

## Windows

There's no installer for Windows, but the client itself works fine.

1. Install Python from https://www.python.org/downloads/ — **check "Add
   python.exe to PATH"** on the first screen of the installer
2. Put `vibe.py` somewhere permanent, like `C:\Users\you\vibe\vibe.py`
3. Open PowerShell and make a `vb` shortcut:

```powershell
notepad $PROFILE
```

Add this line (fix the path to match where you put the file), save, close, and
open a new PowerShell window:

```powershell
function vb { python "C:\Users\you\vibe\vibe.py" $args }
```

Now `vb login`, `vb clone`, and everything else works the same as above.

---

## Using it

```bash
vb status                        # what branch you're on and what you changed
vb push -m "what you changed"    # commit everything and upload, in one step
vb pull                          # download other people's changes
vb log                           # recent history
```

There's no `git add` step. `vb push -m "..."` takes everything in the folder
(minus whatever `.gitignore` lists) and sends it.

More commands: `vb branch`, `vb checkout -b name`, `vb merge other-branch`,
`vb commit -m "..."`, `vb merge --abort`, `vb remote`, `vb --help`.

### Syncing everything at once

`vb pull` and `vb push` only touch the branch you're standing on. If several
people are working on several branches, `vb mirror` handles all of them in one
go — downloads every branch that moved, uploads every branch of yours that's
ahead:

```bash
vb mirror              # sync everything, both directions
vb mirror --dry-run    # show what it would do, change nothing
vb mirror --down       # only download
vb mirror --up         # only upload
```

It never merges behind your back. A branch where you and someone else both
committed gets reported as `stuck`, and you deal with it yourself:

```bash
vb checkout that-branch
vb pull                # merge it properly, fix any conflicts
vb mirror              # now it goes up with everything else
```

Uncommitted work is safe — if the branch you're on has changes you haven't
committed, mirror skips that one branch and syncs the rest.

### Pull the latest GitHub push into Gitea once

No Git, Node, npm, pip, or package install is used. `vibe` does the network protocol
itself with Python's standard library. From inside your Chronos folder:

```bash
vb github-pull https://github.com/OWNER/REPO
```

That remembers the GitHub source, so future imports can be:

```bash
vb github-pull                # every branch GitHub has
vb github-pull --dry-run      # show what it would do, change nothing
vb github-pull --branch main  # only that branch
```

It checks Gitea first, reads **every** branch from GitHub, and pushes the result to
only:

```text
https://lol.tevproject.com/NIX/Chronos
```

Nothing is sent to GitHub, and it does not turn on continuous mirroring — it's one
copy, each time you run it.

Branches it creates or fast-forwards are straightforward. Where GitHub and Gitea
have both moved, it keeps both histories but **the files come out as GitHub's**, so
anything that only existed on Gitea is gone from that branch (still recoverable with
`vb versions` / `vb recover`). It is a copy, not a merge.

Two things it deliberately won't touch: a branch where you have commits Gitea hasn't
got — push those first — and a branch that exists on Gitea but not GitHub, which is
left alone rather than deleted. Skipped branches make the command exit 1, and the
output names each one.

### Recover a past version

```bash
vb versions
vb recover <version-id>
```

`vb recover` makes a new commit containing the selected old version and pushes that
new commit to Gitea. It does not delete newer or older commits. Use `--no-push` if
you only want to restore the files locally first — that form never touches the
network, so it works offline.

You can also count backwards instead of copying an id: `vb recover HEAD~3`.

### When two people edit the same thing

If you and someone else changed the same lines, `vb pull` stops and marks the
spot in the file:

```
<<<<<<< main
your version
||||||| base
what it looked like before either of you touched it
=======
their version
>>>>>>> origin/main
```

Open the file, delete the marker lines, keep the text you want, then:

```bash
vb commit -m "merge origin/main"
vb push
```

Changed your mind? `vb merge --abort` puts everything back.

---

## If something goes wrong

**`zsh: command not found: vb`**
Open a new Terminal window. If it still fails, run
`echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc` then
`source ~/.zshrc`.

**`chmod: install.sh: No such file or directory`**
You're in the wrong folder. `ls` should list `vibe.py` and `install.sh`. If it
doesn't, `cd` to wherever they actually are.

**Copy-pasting a command with a `#` comment on it does something weird**
Leave the `#` and everything after it out. Terminal doesn't always treat it as a
comment.

**`authentication failed (HTTP 401)`**
Wrong username or an expired/mistyped token. Generate a fresh token and run
`vb login` again. Make sure the token has **Read and Write** repository access —
read-only works for `clone` and `pull` but fails on `push`.

**`certificate verify failed` / `unable to get local issuer certificate`**
The installer normally fixes this. If it didn't, run:

```bash
mkdir -p ~/.local/share/vibe
security find-certificate -a -p /System/Library/Keychains/SystemRootCertificates.keychain > ~/.local/share/vibe/ca-certs.pem
echo 'export SSL_CERT_FILE="$HOME/.local/share/vibe/ca-certs.pem"' >> ~/.zshrc
source ~/.zshrc
```

Check it grabbed the certs — this should print something over 100:

```bash
grep -c "BEGIN CERTIFICATE" ~/.local/share/vibe/ca-certs.pem
```

**`object ... is missing from the local store`**
An earlier download was cut off partway, leaving the repo with holes. `vb pull`
repairs this by itself now — it notices the gap and re-downloads what's missing.
If it instead tells you the remote can't fill the holes, the history you have
locally isn't on the server any more; clone a fresh copy beside it and move your
work across:

```bash
cd ..
vb clone https://lol.tevproject.com/NIX/Chronos Chronos-fresh
```

### If TLS still fails

There's a last-resort switch that skips certificate checking:

```bash
VIBE_INSECURE=1 vb pull
```

Understand the tradeoff before using it: it turns off the check that you're
actually talking to the real server. Don't use it for `vb login`, since that's
the command that sends your token. Fix the certificates instead and keep this
for emergencies.

---

## Where your stuff lives

| What | Where |
| --- | --- |
| the command | `~/.local/bin/vibe` (and `vb`, pointing at it) |
| your token | `~/.config/vibe/credentials.json`, readable only by you |
| your name/email | `~/.config/vibe/config.json` |
| repo data | `.vibe/` inside the cloned folder |

To remove it all: `rm ~/.local/bin/vibe ~/.local/bin/vb` and
`rm -rf ~/.config/vibe ~/.local/share/vibe`. Run `vb logout` first if you just
want to drop the saved token.

Because the repo data sits in `.vibe/` rather than `.git/`, vibe can share a
folder with regular git without the two confusing each other.
