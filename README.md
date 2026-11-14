# YT to MP3

A desktop app that converts YouTube videos to MP3. Pick the bitrate, convert single videos, whole playlists, or every video on a channel that matches a search term.

## Requirements

- Python 3.9 or newer, with tkinter (included in the standard Windows and macOS installers from [python.org](https://www.python.org/downloads/))
- An internet connection

ffmpeg is bundled through the `imageio-ffmpeg` package, so you don't need to install it separately.

## Install

```
git clone https://github.com/Sneekit/ytmp3.git
cd ytmp3
pip install -r requirements.txt
```

This installs `yt-dlp` and `imageio-ffmpeg`.

## Run

```
python ytmp3.py
```

## Usage

1. Choose a **Save to** folder and a **Bitrate** (64-320 kbps; 320 is the highest quality). Tick **Embed thumbnail** to add cover art.
2. Add videos with one of the tabs:
   - **Single song**: paste one or more YouTube links, one per line.
   - **Playlist**: paste a playlist link. You can limit the number of videos and name the folder.
   - **Channel**: enter a channel link or `@handle` and a search term. Every video on the channel whose title contains the term is added (separate several terms with commas; case is ignored).
3. For playlists and channels, a dialog shows how many videos were found and asks you to confirm.
4. Everything goes into one queue and converts one video at a time. Use **Stop**, **Remove selected** and **Clear finished** to manage it.

Playlist and channel videos are saved in a subfolder of the Save to folder, named after the playlist (or channel and search term) unless you enter a folder name.

## Troubleshooting

- If downloads start failing, update yt-dlp: `pip install -U yt-dlp`. YouTube changes often and yt-dlp releases fixes quickly.
- A warning about a missing JavaScript runtime can be ignored; audio downloads still work.

## Note

Downloading from YouTube may go against its Terms of Service. Only download content you have the right to use.
