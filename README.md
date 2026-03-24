# fate

_Pull makes happy!_

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

### fate run (or fate r)

Once a directory has `.faterc` set up, you can use `fate run`.
The actions supported right now, if you have a clean working state:

- `pull`: runs `git pull` if there is a configured remote and clean workdir.
  If the working directory is dirty but there's a remote, runs `git fetch` instead.
- `uv`: runs `uv sync --upgrade` in order to update `uv.lock`
  If the `commit` option is enabled, also git commit's the change.
- `prek`: runs `prek auto-update` in order to update `prek.toml` hooks
  If the `commit` option is enabled, also git commit's the change.
- `push`: runs `git push` if there is a configured remote.
  If the `verify` option is turned off, adds `--no-verify`.

### fate multirun (or fate m)

This recursively runs `fate run` on every directory under the specified one
which has a `.faterc` file.

The following options can be used for multirun mode:

- By default `fate` runs all enabled tasks; but you can also use `-o`/`--only`
  or `-e`/`--exclude` to restrict the list.
  Note that `fate` will **never** run a task not actually enabled in `.faterc`.

- You can add a delay between repositories with `-d`/`--delay`
  (e.g. `1s`, `500ms`, `2m`, or just `5` for 5 seconds),
  to throttle requests.

- Pass `-a`/`--all` to include discovered Git repositories that don't have `.faterc`,
  allowing just `pull` and `push` on them.

- Hidden directories are not searched by default
  (since `~/.cache` often has repositories, for example).
  Pass `-u`/`--unrestricted` to search inside hidden directories too.

- By default, only repositories directly inside the target directory are found.
  Use `-r`/`--recursive` to search to any depth, or `--depth N` to limit to N levels.
  (`-r` and `--depth` are mutually exclusive.)

We recommend installing [fd](https://github.com/sharkdp/fd)
for much faster search;
`fate` otherwise falls back to `os.walk` (slower), and prints a warning.

### fate ls (or fate l, or fate list)

Shows the status of each repository without running any tasks.
(It doesn't make any network queries, so it's the fastest.)

### Shortcuts for fate multirun

`fate ls` is actually just `fate multirun` with an empty `--only` list.
We also have the following:

- **fate pull**: equivalent to `fate multirun --only pull`
- **fate gamble (or fate g)**: equivalent to `fate multirun --exclude push`.
- **fate push**: equivalent to `fate multirun --only push`

[fct]: https://puzzmon.world/rounds/fates_thread_casino
