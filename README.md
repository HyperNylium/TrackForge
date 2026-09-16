<h1>
  TrackForge
  <img src="assets/TrackForge_icon.png" alt="TrackForge icon" align="right" width="100">
</h1>

TrackForge is a standalone command-line tool that processes the audio tracks of a media file.  
It re-encodes, converts, and downmixes audio according to a list of profiles you give it, and writes a new file with everything else (video, subtitles, attachments, chapters) copied through untouched.

## License and credit

TrackForge is licensed under the **GPLv3**. See [LICENSE](LICENSE) for the full text.

The EOS and EOS+ audio processing is copied with [permission](https://github.com/philiptn/mkv-auto/issues/44#issuecomment-5648542458) from philiptn's [mkv-auto](https://github.com/philiptn/mkv-auto).  
See [NOTICE](NOTICE) for the full attribution and grant details.

## Requirements

- **ffmpeg** and **ffprobe** are required. TrackForge uses ffprobe to inspect the input's audio tracks and ffmpeg to encode the audio.
- **mkvmerge** is optional and only used for `.mkv` output. When it is available, TrackForge prefers it for muxing mkv output (see Formats below). When it is not, ffmpeg does the job instead.

TrackForge looks for each tool in `static/<tool>/` first (for example `static/ffmpeg/ffmpeg`), then falls back to your PATH.  
Set the `TRACKFORGE_STATIC_DIR` environment variable to point at a different directory of bundled tools instead of the project's own `static/`.

## Running

TrackForge is managed with [uv](https://docs.astral.sh/uv/) and targets Python 3.13. From a clone of this repo, sync the dependencies once and then run the CLI:

```
uv sync
uv run python -m src.cli <input> <output> "<profiles>"
```

`uv sync` sets up a local `.venv`, and `uv run` runs the CLI inside it. See [Usage](#usage) below for the arguments and flags.

If you have a prebuilt release, run the bundled binary directly instead:

```
./trackforge <input> <output> "<profiles>"      # Linux
trackforge.exe <input> <output> "<profiles>"    # Windows
```

Release binaries bundle ffmpeg and ffprobe in `static/`, so they run without a separate ffmpeg install.

## Usage

```
python -m src.cli <input> <output> "<profiles>" [options]
```

- `input` - path to the source `.mkv` or `.mp4` file, or a folder of them.
- `output` - path to write the result to (also `.mkv` or `.mp4`), or an output folder when the input is a folder. Not needed with `--info`.
- `profiles` - a comma-separated list of profile strings, for example
  `"ORIG, AAC:2.0, EOS:2.0"`. Not needed with `--info`.

When the input is a folder it is walked recursively for `.mkv` and `.mp4` files, and each one is written under the output folder at the same relative path (so `in/a/b.mkv` becomes `out/a/b.mkv`). A file that fails to process does not stop the rest; a summary of how many succeeded and failed is printed at the end, and the exit code is non-zero if any failed.

Flags:

- `--info` - print the input's audio tracks (index, language, codec, channels, default flag, title) and exit. No output path or profiles needed. Not allowed with a folder input.
- `--track INDEX` - only apply the given profiles to the audio track at `INDEX` (the index shown by `--info`). Every other audio track is copied through unchanged. Not allowed with a folder input.
- `--muxer {auto,ffmpeg,mkvmerge}` - choose the muxer explicitly. `auto` (the default) picks mkvmerge for mkv output when it is installed, and ffmpeg otherwise.
- `--overwrite` - allow writing over an existing output file. Without it, TrackForge refuses to touch a file that already exists.
- `--workers N` - encode up to N audio tracks at once within a file (default 1).
- `--file-workers M` - process up to M files at once when the input is a folder (default 1). Combined with `--workers`, up to N times M encodes run at once.
- `--simple` - print plain line logs instead of the live progress display.
- `-v`, `--verbose` - turn on debug logging, including the exact ffmpeg / ffprobe / mkvmerge commands being run. Implies `--simple`, since a live display and scrolling debug lines fight each other.
- `--version` - print the version and exit.

## Profile syntax

A profile string is a comma-separated list of profile items. Each item takes one of these forms:

- `ORIG` - copy the source track unchanged, no re-encoding.
- `CODEC[:CHANNELS]` - re-encode to the given codec, optionally at a given channel layout.
- `EOS[:CHANNELS]` or `EOS+[:CHANNELS]` - apply the Even-Out-Sound (or EOS+) dialogue-forward downmix, then encode with a default codec of AC3.
- `TRANSFORM-CODEC[:CHANNELS]` - apply EOS or EOS+ and then encode with a specific codec instead of the AC3 default. For example, `EOS-EAC3:5.1`.

Supported codecs: `AAC`, `AC3`, `EAC3`, `DTS`, `OPUS`, `FLAC`, `WAV` (`PCM` is accepted as an alias for `WAV`), and `ORIG`.  
Supported channels: `1.0`, `2.0`, `5.1`, and `7.1`. If you omit the channel token, the output keeps the source's channel layout.

## Formats

Input and output must each be `.mkv` or `.mp4`.

For `.mp4` output, only `AAC`, `AC3`, and `EAC3` are allowed as output codecs. Any other codec (including `ORIG` when the source track's codec is not one of those three) is rejected with an error suggesting you use `.mkv` instead.

One limitation to know about: `.mp4` output only carries through video and chapters from the source. Subtitles and attachments are not copied into `.mp4` output. If you need those preserved, use `.mkv` output instead.

Non-audio streams (video, subtitles, attachments, and chapters) are always stream-copied and keep their original flags.  
For audio, disposition flags are carried from the source onto the derived tracks. The default-track flag is exclusive, so it is set only on the first output derived from a default source track.  
The other flags (forced, commentary, hearing impaired, visual impaired, original) are carried onto every output derived from that source.

## Building

TrackForge compiles to a single-file binary with [PyInstaller](https://pyinstaller.org/). From a clone of the repo:

```
uv run --with pyinstaller python build.py            # Build the TrackForge binary
uv run --with pyinstaller python build.py --refresh  # Download dependencies to static folder
uv run --with pyinstaller python build.py --cleanup  # Delete build artifacts and release folder
```

PyInstaller cannot cross-compile, so it builds for the platform it runs on. To get a binary for a given target, run the build on that platform:

- **Windows x64** - build on Windows x64.
- **Linux x64** - build on Linux x64.
- **Linux ARM64** - build on Linux ARM64.

If you dont know how to build for a specific platform, it is recommended download the latest pre-built release from the GitHub releases page.

### mkvmerge

The Windows release packages bundle mkvmerge in `static/mkvmerge/`, because MKVToolNix ships a static Windows build. MKVToolNix does not ship a static Linux build, so the Linux packages do not bundle it.  
TrackForge muxes `.mkv` output with ffmpeg by default and works fine without mkvmerge, but it is preferred for `.mkv` when available, so on Linux you install MKVToolNix yourself (below) or drop the binary in `static/mkvmerge/`.

On Linux you install it manually with your package manager, for example for ubuntu 24.04 (noble):

```
wget -O /etc/apt/keyrings/gpg-pub-moritzbunkus.gpg https://mkvtoolnix.download/gpg-pub-moritzbunkus.gpg

# change these echo commands to match your distro. Look at the downloads page for more information:
# https://mkvtoolnix.download/downloads.html#ubuntu
# https://mkvtoolnix.download/downloads.html#debian
echo "deb [arch=amd64 signed-by=/etc/apt/keyrings/gpg-pub-moritzbunkus.gpg] https://mkvtoolnix.download/ubuntu/ noble main" > /etc/apt/sources.list.d/mkvtoolnix.download.list
echo "deb-src [arch=amd64 signed-by=/etc/apt/keyrings/gpg-pub-moritzbunkus.gpg] https://mkvtoolnix.download/ubuntu/ noble main" >> /etc/apt/sources.list.d/mkvtoolnix.download.list

apt update

apt install mkvtoolnix
```

If you want to setup Windows manaully, download it from [the official MKVToolNix website](https://mkvtoolnix.download/downloads.html#windows) and make sure the mkvtoolnix install folder is on your PATH.
```pwsh
[Environment]::SetEnvironmentVariable("Path", [Environment]::GetEnvironmentVariable("Path", "User") + ";C:\Program Files\MKVToolNix", "User")
```

Or drop the `mkvmerge` binary in `static/mkvmerge/`.
