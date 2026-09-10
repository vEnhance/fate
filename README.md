# fate

*Pull makes happy!*

`fate` is a Python program that helps `git pull`
a bunch of Git repositories in your home directory or similar.
(The name comes from [Fate's Thread Casino in Mystery Hunt 2026][fct],
in which the protagonists become addicted to the Keeper's gacha machines.)

## Installation

It's `fate-casino` on PyPI, so e.g.

```bash
uv tool install fate-casino
fate --help
```

## Usage

See the argparse help for full options.

### fate init (or fate i)

For every directory you want to use with `fate`,
you need to create a `.faterc` or `faterc` (the latter takes precedence)
in the root of that Git repository.
You can do this by running `fate init`.

This is a TOML 1.1 file that specifies which actions `fate` performs
when run on that repository, and looks something like this:

```toml
[config]
branch = "main"
venv = ".venv" # path to the virtual environment, if using uv

[actions]
pull = {enabled = true}
uv = {enabled = true, commit = true}
prek = {enabled = true, commit = true}
push = {enabled = true, verify = true}
```

An action written out with `enabled = false` gets a red note when `fate`
skips it, so a deliberately switched-off action stays visible.
Omit the action entirely to silence that.

### fate seek (or fate s)

Search for Git repositories that could benefit from `fate init`
(meaning there is either a `uv.lock` or `prek.toml`).

```bash
fate seek
```

### fate run (or fate r)

Once a directory has `.faterc` set up, you can use `fate run`.
The actions supported right now, if you have a clean working state,
run in the order listed:

- `pull`: runs `git pull` if there is a configured remote and clean workdir.
  If the working directory is dirty but there's a remote, runs `git fetch` instead.
- `uv`: runs `uv sync --upgrade` in order to update `uv.lock`
  If `prek.toml` declares a `uv-export` hook, also runs that.
- `prek`: runs `prek update` in order to update `prek.toml` hooks.
  If updated, run `prek run --all-files` to catch new errors.
  Attempts autofixes, but this can fail.
- `push`: runs `git push` if there is a configured remote.
  If the `verify` option is turned off, adds `--no-verify`.
  Only `fate push` ever runs this task; `fate run` and `fate gamble` skip it.

Every subprocess runs with `PREK_QUIET=1` so that `prek`, whether invoked
directly or from a git hook, only reports what actually needs attention.
Set `PREK_QUIET` yourself to override it.

### fate gamble (or fate g)

This recursively runs `fate run` on every directory under the specified one
which has a `.faterc` file.
Like `fate run`, it never pushes; only `fate push` does that.

The following options can be used:

- You can add a delay between repositories with `-d`/`--delay`
  (e.g. `1s`, `500ms`, `2m`, or just `5` for 5 seconds),
  to throttle requests.

- Pass `-a`/`--all` to include discovered Git repositories that don't have `.faterc`,
  allowing just `pull` on them (and `push`, for `fate push`).

- Hidden directories are not searched by default
  (since `~/.cache` often has repositories, for example).
  Pass `-u`/`--unrestricted` to search inside hidden directories too.

- By default, only repositories directly inside the target directory are found.
  Use `-r`/`--recursive` to search to any depth, or `--depth N` to limit to N levels.
  (`-r` and `--depth` are mutually exclusive.)

The same options work for `fate ls`, `fate pull`, and `fate push`.

We recommend installing [fd](https://github.com/sharkdp/fd)
for much faster search;
`fate` otherwise falls back to `os.walk` (slower), and prints a warning.

### fate ls (or fate l, or fate list)

Shows the status of each repository without running any tasks.
(It doesn't make any network queries, so it's the fastest.)

### Other multi-repository commands

- **fate pull**: runs only the `pull` task on every repository.
- **fate push**: runs only the `push` task on every repository.
  This is the *only* command that pushes:
  neither `fate run` nor `fate gamble` can, no matter what `.faterc` says.

[fct]: https://puzzmon.world/rounds/fates_thread_casino
