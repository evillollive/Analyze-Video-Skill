# Pipeline options and outputs

Reference for `process.py`. Read this when a run was interrupted, when you need a field or file the core instructions do not cover, or when tuning speed.

## Per-video outputs

- `manifest_lite.json`: lightweight default manifest, schema v3 minus transcript text and per-frame arrays.
- `manifest.json`: full schema v3 manifest with top-level `transcript_segments`. Segment timestamps are always absolute positions in the source video, including on a focused `--start`/`--end` run.
- `transcript.txt`: human-readable transcript (`[mm:ss] text` per line), written whenever a transcript exists. Its path is also in `manifest_lite.transcript_path`.
- `report.md`: human-readable pipeline report.
- `manifest_partial.json`: a partial manifest written as chunks finish; present only while a run is in flight or after it was interrupted. Removed on success.
- `chunks/chunk_N/contact_sheet.jpg`: one contact sheet per processed chunk.
- `chunks/chunk_N/frames/<sig>/frame_NNNN.jpg`: full-resolution selected-frame candidates. The `<sig>` subfolder is keyed to the extraction settings; always use the `absolute_path` from the manifest rather than building this path yourself.
- `download/video.<ext>`: source video when downloaded with `--no-download-cache` or from a local file. By default a downloaded URL lives in the shared cache (see Resuming after a timeout), not under the out-dir.
- `audio.mp3` or `audio_START_END.mp3`: only if Whisper was used.
- `status.json`: current pipeline stage, updated continuously (downloading, transcript_ready, extracting with `current_chunk`/`chunks_completed`, complete). Read it to see how far an interrupted run got.

## Manifest fields

Per-frame paths live in the full manifest at:

```text
manifest.json -> chunks[].frames[].absolute_path
```

`select_frames.py` reads them for you (it loads the full manifest automatically via the lite file's `manifest_path` pointer), so you rarely need to open `manifest.json` by hand just to pick frames.

Each chunk's `contact_sheet` has `absolute_path`, `cols`, `rows` and `labeled`. When `labeled` is true, every tile shows its tile number and its time in the video; the tile number is that frame's `index`. `labeled` is false when the installed ffmpeg cannot draw text, and the sheet is then plain.

Chunk schema field names (lite and full): `index`, `start_seconds`, `end_seconds`, `start_formatted`, `end_formatted`, `duration_seconds`, `frame_count`, `contact_sheet`, `transcript_slice`. (They are `index`/`start_formatted`/`end_formatted`, not `chunk_index`/`start_time_str`.)

Transcript text lives at:

```text
manifest.transcript_segments[]
```

Each chunk includes `transcript_slice` with `start_index`, `end_index`, and `segment_count` pointers into the top-level transcript list.

## How frames are picked

`select_frames.py` cuts the timeline into as many equal slots as frames were asked for. From each slot it takes the frame that looks least like the frames already picked. A slot with nothing new-looking gives its pick to the most different-looking frame anywhere in the video. Near-blank frames (black, white, mid-fade) are skipped. If the video runs out of new-looking frames, the remaining picks fill the largest gaps in time, so the count asked for is always returned.

Frames are compared as 16 by 16 pixel thumbnails made by ffmpeg, which takes well under a second for 200 frames and needs no extra software. If ffmpeg is missing or fails, the helper says so on stderr and falls back to even spacing. `--even` asks for even spacing directly. `--tiles 1:4,2:59` returns exactly the named frames.

## Resuming after a timeout
`process.py` is resumable. Re-running with the same `--source` and `--out-dir` reuses any chunk whose frames are still valid (matched by an extraction signature), so an interrupted long video continues instead of restarting from zero. Each distinct set of extraction settings writes into its own `frames/<sig>/` subfolder, so a re-run never has to delete a previous run's files (which some sandboxes forbid) and stale frames can't pollute the result. Contact sheets are reused on the same terms: a sheet whose frames and tiling settings are unchanged is not re-tiled, so a resumed run skips a full ffmpeg pass per chunk. If a run is killed, check `status.json` to see where it stopped, then just re-run the same command. Pass `--force` to ignore cached output and re-download + re-extract everything.

Downloaded URLs are cached once per URL under `~/.cache/analyze-video/downloads/<url-hash>/` and reused across runs, so a focused `--start`/`--end` rerun (even in a different `--out-dir`) does not re-download the whole video. The full video is always fetched, so timestamps stay correct. Pass `--force` to refresh a cached download, or `--no-download-cache` to keep the source under the out-dir instead.

Downloads are capped to the source width the pipeline can actually use: `max(854, --resolution)` pixels, so a 16:9 video fetches a 480p stream rather than 720p and transfers roughly half to a third of the bytes without changing the extracted frames (which render at `--resolution`, 512 by default) or the document images. The cap is on width rather than height so portrait video still gets a stream wide enough for the frames. Pass `--download-max-width 0` for the best available stream, or a pixel width to set the budget yourself. A cached download recorded under a narrower budget is not reused for a wider request.

The download cache is self-managing: at the end of each run, `process.py` evicts entries older than 14 days and trims the cache back under a 5 GB total (least-recently-used first), never touching the file the current (or a concurrent) run is using. Tune the limits with `ANALYZE_VIDEO_CACHE_MAX_AGE_DAYS` and `ANALYZE_VIDEO_CACHE_MAX_GB` (set either to `0` to disable that limit). To wipe every cached download by hand, run `python3 "${SKILL_DIR}/scripts/setup.py" --clear-cache` (this leaves the `docx` module cache intact). `setup.py --json` reports the current cache size as `download_cache_bytes`.

## Parallel frame extraction

`process.py` works to a single concurrency budget, auto-picked from the core count and capped at 4. Override it with `--jobs N`; `--jobs 1` forces fully sequential extraction (useful on a heavily loaded or memory-constrained host).

When a video is auto-chunked, the budget is spent running several chunks at once, since frame extraction and contact-sheet tiling are independent per chunk. When there are fewer chunks than the budget allows (including the common single-chunk case for videos under 12 minutes), each chunk spends the spare capacity splitting its own extraction into contiguous sub-ranges that run in parallel. The total number of concurrent ffmpeg processes is the same either way, so the CPU is never oversubscribed.

Output is identical either way. Chunks are always written to the manifest in chronological order, and a split extraction reproduces the single-pass frame set byte for byte: frame `i` sits at `start + i / fps` whether one worker or four produced it. If any sub-range fails or comes up short, the whole range is silently redone in a single pass, so a split can cost time but never correctness.

## Keyframe-only frame sampling

Frame extraction is the pipeline's dominant CPU cost: by default ffmpeg decodes *every* frame of a chunk just so the `fps` filter can keep ~100 of them. For a 600s chunk that is roughly 15,000 decoded frames to produce 100.

`process.py` now measures the source's keyframe interval once per video (an `ffprobe` packet-header read, no decoding, well under a second) and, when it is safe, extracts with `-skip_frame nokey` so only keyframes are decoded. On a 300s 720p test clip this cut frame-extraction CPU from 8.3s to 0.2s while producing the same 20 frames, all visually distinct.

It is only enabled when the gap between sampled frames covers at least two keyframes. That guarantees every output frame lands on its own keyframe rather than the `fps` filter repeating one, and bounds how far a frame can drift from its nominal timestamp at half the sampling interval. The practical effect is that long overview runs (sparse sampling) get the speedup and focused `--start`/`--end` runs (dense sampling) keep a full decode automatically.

Control it with `--keyframe-sampling`:
- `auto` (default): measure and decide per chunk, as described above.
- `never`: always decode every frame, matching the previous behavior exactly.
- `always`: force keyframe-only decoding even where frames may repeat.

If keyframe-only extraction yields nothing (a codec or container that exposes no keyframe flags), the chunk is silently re-extracted with a full decode. The manifest records `keyframe_interval_seconds` for the video and `keyframe_sampled` per chunk.

## Trimming a trailing promo/outro

If a video ends with a repetitive promo or static "watch the full episode" card, `process.py` detects it and records a `trailing_promo` hint in the manifest (plus a note in `report.md`). It does not remove anything by default. To drop that block from frame extraction, re-run with `--trim-static-outro`, or target the real content with `--end`.
