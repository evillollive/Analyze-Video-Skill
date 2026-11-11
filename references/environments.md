# Environment notes

The skill runs anywhere Python 3, ffmpeg, yt-dlp and Node.js run: macOS, Linux and Windows, under any agent that can run shell commands. Read this when something works in one place and not another: commands time out, setup says "ready" but processing disagrees, a download is blocked only from this machine, or installed tools are not found.

Each section describes a situation, how to recognize it, and what to do. None of them is tied to a particular product.

## Setup and processing must share a machine

`setup.py` and `process.py` must run in the same host environment. Setup state is local to the host it ran on.

- If setup ran in one environment (for example a sandbox) and processing will run in another (for example the user's own machine), run `python3 "${SKILL_DIR}/scripts/setup.py" --check` again where processing will run.
- Never assume a "ready" result from one environment applies to another.

## The shell is not the operating system you expect

Some agents give you a shell inside a container or virtual machine, even when the user's computer runs macOS or Windows. Check with `uname -s` and `uname -m` rather than assuming.

- Setup follows the operating system the shell reports, so a Linux virtual machine on a Mac takes the Linux path.
- `--cookies-from-browser` cannot read a browser that lives on a different operating system from the shell.
- Scratch space in such a shell may be small and may be deleted when the session ends. Write deliverables to a folder the user can see and chose, not to scratch.

## Shells with a time limit per command

Some agent shells stop a command after a fixed time (limits from 45 seconds to about 3 minutes have been seen) and kill background processes when the call returns.

- Do not start a download or `process.py` in the background there. It will be killed.
- Run in the foreground with a timeout a little under the limit, for example `timeout -s INT 165 <command>` under a 180-second limit, then re-run the exact same command to continue. Downloads resume from the bytes already on disk and `process.py` reuses finished chunks.
- Read `status.json` in the out-dir to see how far an interrupted run got.
- If the environment also offers a shell without the limit, run long videos there and continue from the manifests it produces.

## Cloud sandboxes and datacenter networks

YouTube and some other sites bot-check or rate-limit (HTTP 429) requests from datacenter IP addresses, including public videos that play fine in the user's browser.

- If you can run commands on the user's own computer, run downloads there.
- Otherwise ask the user to download the video and give you the local file path, then pass `--source-url "<original-url>"` so the title and captions can still be recovered.
- Do not try to get around the block. See the safety boundary in `SKILL.md`.

## Folders synced by a cloud service

Dropbox, iCloud Drive, OneDrive, Google Drive and similar services upload every file that appears in a synced folder. A large download creates thousands of temporary fragment files.

- Keep temporary files out of synced folders with `--temp-dir`, pointing at a local folder such as `"$HOME/dltmp"`.
- Write only the final files to the destination the user chose.
- Never assume a destination path. Ask.

## Tools installed but not on PATH

When setup installs yt-dlp or Deno into a per-user folder that is not on `PATH`, it prints the exact `export PATH=...` lines. Apply them in the same shell invocation that runs the pipeline, or call the tool by its absolute path. In shells that start fresh on every command, that means prefixing every command.

## Read-only skill folder

Some environments mount the skill folder read-only. Do not try to `npm install` into `${SKILL_DIR}/scripts`. The document builder installs the `docx` module into the per-user cache (`~/.cache/analyze-video/node_modules`) instead. See `docx-spec.md` if the module still cannot be found.

## Older Python

yt-dlp prints a deprecation warning on Python 3.10 and older. Pass the warning on to the user rather than hiding it; a future yt-dlp may stop working there.

## Windows

- Use `python` if `python3` is not a command.
- `setup.py` prints exact install commands for missing tools rather than installing them. Run those, then re-run `setup.py`.
- Paths with spaces need quotes, as on every system.
