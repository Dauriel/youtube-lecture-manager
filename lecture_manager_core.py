# lecture_manager_core.py
import pandas as pd
import random
import subprocess
import json
import sqlite3
import os
import logging # Use logging instead of print for many things
from typing import List, Dict, Optional, Tuple
from load_df import download_last_version
import re
import tempfile # For creating a temporary directory for subtitles


# --- Configuration ---
download_last_version() #comment this line if you want to just have local copy of your lectures.csv. I use this paired with google sheets
CSV_FILE = "lectures.csv"
DB_FILE = "lecture_subtitles.db"
YT_DLP_PATH = "yt-dlp"  # Or full path if not in PATH

_playlist_cache = {} # Cache for playlist video details

# --- Logging Setup ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- Database Functions ---
def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS subtitles (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        series_title TEXT NOT NULL,
        video_playlist_index INTEGER NOT NULL,
        video_title TEXT,
        video_url TEXT UNIQUE NOT NULL,
        subtitles_text TEXT,
        downloaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(series_title, video_playlist_index)
    )
    """)
    conn.commit()
    conn.close()
    logging.info(f"Database {DB_FILE} initialized/checked.")

def store_subtitle(series_title: str, video_playlist_index: int, video_title: str, video_url: str, subtitles_text: str) -> bool:
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    try:
        cleaned_subtitles = clean_vtt_content(subtitles_text) # Clean before storing
        cursor.execute("""
        INSERT OR REPLACE INTO subtitles (series_title, video_playlist_index, video_title, video_url, subtitles_text)
        VALUES (?, ?, ?, ?, ?)
        """, (series_title, video_playlist_index, video_title, video_url, cleaned_subtitles))
        conn.commit()
        logging.info(f"Stored cleaned subtitles for: {series_title} - Video {video_playlist_index + 1}: {video_title}")
        return True 
    except sqlite3.IntegrityError:
        logging.warning(f"Subtitle for {video_url} (or series/index combo) might already exist.")
        return False # Or update, depending on desired behavior
    except Exception as e:
        logging.error(f"Error storing subtitle for {video_url}: {e}")
        return False
    finally:
        conn.close()

def check_subtitle_exists(video_url: str) -> bool:
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM subtitles WHERE video_url = ?", (video_url,))
    exists = cursor.fetchone() is not None
    conn.close()
    return exists

def get_subtitle_for_review(video_url: str) -> Optional[str]:
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT subtitles_text FROM subtitles WHERE video_url = ?", (video_url,))
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else None

# --- yt-dlp Helper Functions ---
def get_playlist_videos_yt_dlp(playlist_url: str) -> Optional[List[Dict[str, str]]]:
    global _playlist_cache
    if playlist_url in _playlist_cache:
        return _playlist_cache[playlist_url]

    if not playlist_url or "youtube.com/playlist?list=" not in playlist_url:
        logging.warning(f"Invalid or non-YouTube playlist URL: {playlist_url}")
        return None
    try:
        command = [YT_DLP_PATH, "--cookies-from-browser","firefox","-j", "--flat-playlist", playlist_url]
        process = subprocess.run(command, capture_output=True, text=True, check=True, encoding='utf-8')
        videos = []
        for line in process.stdout.strip().split('\n'):
            if line:
                video_info = json.loads(line)
                videos.append({
                    'title': video_info.get('title', 'N/A'),
                    'url': video_info.get('url', video_info.get('webpage_url'))
                })
        _playlist_cache[playlist_url] = videos
        return videos
    except subprocess.CalledProcessError as e:
        logging.error(f"Error fetching playlist '{playlist_url}': {e.stderr}")
        return None
    except json.JSONDecodeError as e:
        logging.error(f"Error decoding JSON for playlist '{playlist_url}': {e}")
        return None
    except Exception as e:
        logging.error(f"An unexpected error occurred with yt-dlp for playlist '{playlist_url}': {e}")
        return None

# In lecture_manager_core.py
def clean_vtt_content(vtt_string: str) -> str:
    """
    Cleans VTT subtitle content to extract only the spoken text.
    Removes WEBVTT header, timestamps, cue settings, notes, and extra blank lines.
    Joins multi-line cues into single lines of text.
    """
    if not vtt_string:
        return ""

    lines = vtt_string.splitlines()
    cleaned_lines = []
    current_cue_text = []

    for line in lines:
        line = line.strip()

        # Skip WEBVTT header, empty lines, NOTE comments, and lines that look like timestamps
        if not line or \
           line.lower() == "webvtt" or \
           line.lower().startswith("note") or \
           re.match(r'^\d{2}:\d{2}:\d{2}\.\d{3}\s*-->\s*\d{2}:\d{2}:\d{2}\.\d{3}', line):
            # If we were accumulating text for a cue and hit a timestamp or empty line,
            # it means the previous cue's text is complete.
            if current_cue_text:
                cleaned_lines.append(" ".join(current_cue_text))
                current_cue_text = []
            continue

        # Skip lines that are purely cue settings (like align:start position:0%)
        # This is a simple check; more complex VTT styling might need more robust parsing.
        if "-->" not in line and (":" in line and "%" in line or line.startswith("<c>") or line.startswith("</c>")):
             # if current_cue_text: # If there was text before styling, add it
             #    cleaned_lines.append(" ".join(current_cue_text))
             #    current_cue_text = []
            continue # Skip styling lines often found after timestamps

        # If it's not a timestamp, header, or comment, assume it's subtitle text.
        # Remove common HTML-like tags often found in subtitles (<b>, <i>, <u>, <c.color>)
        line = re.sub(r'<[^>]+>', '', line)
        current_cue_text.append(line)

    # Add any remaining text from the last cue
    if current_cue_text:
        cleaned_lines.append(" ".join(current_cue_text))
    cleaned_lines_final = []

    for i in range(len(cleaned_lines)-1):
        # Remove any leading/trailing whitespace and filter out empty lines
        line = cleaned_lines[i].strip()
        line = line.strip()
        if line:
            if line in cleaned_lines[i+1]:
                # If the current line is a substring of the next line, skip it
                continue
            cleaned_lines_final.append(line)
        
    # Join all cue texts with a space, and remove any leading/trailing whitespace from the final string.
    # Also filter out any "empty" lines that might have resulted from cues with only tags.
    return "\n".join(filter(None, cleaned_lines_final)).strip()

def download_subtitles_yt_dlp(video_url: str) -> Optional[str]:
    if not video_url or not ("youtube.com/watch?v=" in video_url or "youtu.be/" in video_url):
        logging.warning(f"Invalid video URL for subtitle download: {video_url}")
        return None

    try:
        langs_priority = ['en', 'en-US', 'en-GB']
        sub_content = None

        # Create a temporary directory to download subtitles into.
        # This helps avoid filename clashes and makes cleanup easier.
        with tempfile.TemporaryDirectory(prefix="yt_subs_") as temp_dir:
            logging.info(f"Created temporary directory for subtitles: {temp_dir}")

            for lang in langs_priority:
                # Define a specific output filename template for the subtitle within the temp directory
                # This template ensures a predictable filename based on video ID and language.
                # Example: If video_url is "https://www.youtube.com/watch?v=VIDEO_ID",
                # filename will be "VIDEO_ID.en.vtt"
                video_id = video_url.split('v=')[-1].split('&')[0] # Basic way to get video ID
                output_template = os.path.join(temp_dir, f"{video_id}.%(ext)s")

                # Command to download subtitles
                command = [
                    YT_DLP_PATH,
                    "--cookies-from-browser","firefox",
                    '--write-sub',
                    '--write-auto-sub',
                    '--sub-format', 'vtt',
                    '--sub-lang', lang,
                    '--skip-download',
                    '-o', output_template, # Use -o for output template
                    video_url
                ]

                logging.info(f"Attempting subtitle download for {video_url} (lang: {lang}) "
                             f"into {temp_dir} with command: {' '.join(command)}")

                process = subprocess.run(command, capture_output=True, text=True, check=False, encoding='utf-8')

                # After running the command, check if the VTT file exists based on our template.
                # Since we specified --sub-format vtt, the extension should be .vtt
                
                expected_sub_filepath = os.path.join(temp_dir, f"{video_id}.{lang}.vtt")

                if os.path.exists(expected_sub_filepath):
                    logging.info(f"Subtitle file found: {expected_sub_filepath}")
                    with open(expected_sub_filepath, 'r', encoding='utf-8') as f:
                        sub_content = f.read()
                    # No need to explicitly os.remove, TemporaryDirectory handles cleanup
                    break # Subtitles found, exit loop
                else:
                    logging.warning(f"Subtitle file {expected_sub_filepath} not found after yt-dlp command for lang {lang}. "
                                    f"RC: {process.returncode}, stdout: '{process.stdout.strip()}', stderr: '{process.stderr.strip()}'")
            
            if sub_content:
                logging.info(f"Successfully downloaded and processed subtitles for {video_url}")
                return sub_content
            else:
                logging.warning(f"No subtitles found or downloaded for {video_url} after trying languages: {langs_priority}.")
                return None

    except Exception as e:
        logging.error(f"An unexpected error occurred during subtitle download for {video_url}: {e}", exc_info=True)
        return None

# --- Core Logic Functions ---
def load_and_prepare_data() -> Optional[pd.DataFrame]:
    try:
        # Load CSV file
        df = pd.read_csv(CSV_FILE)
        df.columns = [col.strip().lower().replace(' ', '_').replace('.', '') for col in df.columns]
        for col in ['current', 'total']:
            df[col] = pd.to_numeric(df[col], errors='coerce')
        
        # Standardize playlist URL column name
        if 'current_lecture_link' in df.columns and 'playlist_url' not in df.columns:
            df.rename(columns={'current_lecture_link': 'playlist_url'}, inplace=True)
        elif 'url' in df.columns and 'playlist_url' not in df.columns: # A more generic fallback
             df.rename(columns={'url': 'playlist_url'}, inplace=True)


        if 'playlist_url' not in df.columns:
            logging.error("Could not identify the lecture link/URL column. Expected 'playlist_url' or 'current_lecture_link'.")
            return None

        df['is_youtube_playlist'] = df['playlist_url'].astype(str).apply(
            lambda x: "youtube.com/playlist?list=" in x
        )
        return df
    except FileNotFoundError:
        logging.error(f"CSV file '{CSV_FILE}' not found.")
        return None
    except Exception as e:
        logging.error(f"Error loading or processing CSV: {e}")
        return None

def select_random_lecture_to_watch(df: pd.DataFrame) -> Optional[Dict]:
    eligible_series = df[
        df['is_youtube_playlist'] &
        (df['current'].notna()) &
        (df['total'].notna()) &
        (df['current'] < df['total'])
    ].copy()

    if eligible_series.empty:
        return {"error": "No eligible YouTube series found to pick a new lecture from (or all are completed)."}

    selected_series_row = eligible_series.sample(1).iloc[0]
    series_title = selected_series_row['lecture_series']
    playlist_url = selected_series_row['playlist_url']
    next_video_index = int(selected_series_row['current'])

    playlist_videos = get_playlist_videos_yt_dlp(playlist_url)
    if not playlist_videos:
        return {"error": f"Could not retrieve videos for playlist: {series_title} ({playlist_url})"}

    if next_video_index < len(playlist_videos):
        video_to_watch = playlist_videos[next_video_index]
        return {
            "series_title": series_title,
            "video_title": video_to_watch['title'],
            "video_url": video_to_watch['url'],
            "message": f"Selected lecture: '{video_to_watch['title']}' from '{series_title}'. This is video #{next_video_index + 1}."
        }
    else:
        return {"error": f"CSV 'Current' ({selected_series_row['current']}) for '{series_title}' might be out of sync or playlist fully watched. Playlist length: {len(playlist_videos)}."}

def select_random_watched_lecture_for_review(df: pd.DataFrame) -> Optional[Dict]:
    eligible_series = df[
        df['is_youtube_playlist'] &
        (df['current'].notna()) &
        (df['current'] > 1)
    ].copy()

    if eligible_series.empty:
        return {"error": "No YouTube series with watched lectures found for review."}

    selected_series_row = eligible_series.sample(1).iloc[0]
    series_title = selected_series_row['lecture_series']
    playlist_url = selected_series_row['playlist_url']
    num_watched = int(selected_series_row['current'])

    playlist_videos = get_playlist_videos_yt_dlp(playlist_url)
    if not playlist_videos:
        return {"error": f"Could not retrieve videos for playlist: {series_title} ({playlist_url})"}

    actual_max_watched_index = min(num_watched, len(playlist_videos))
    if actual_max_watched_index == 0:
        return {"error": f"No videos to review in '{series_title}' based on playlist content or 'Current' count."}

    random_watched_video_index = random.randint(0, actual_max_watched_index - 1)
    video_to_review = playlist_videos[random_watched_video_index]
    
    subtitles = get_subtitle_for_review(video_to_review['url'])
    subtitles_exist = bool(subtitles)

    return {
        "series_title": series_title,
        "video_title": video_to_review['title'],
        "video_url": video_to_review['url'],
        "subtitles": subtitles,
        "subtitles_exist_in_db": subtitles_exist,
        "playlist_index": random_watched_video_index, # For potential download
        "message": f"Selected for review: '{video_to_review['title']}' from '{series_title}'. Video #{random_watched_video_index + 1}."
    }

def bulk_download_watched_subtitles(df: pd.DataFrame) -> Dict:
    results = {"downloaded": 0, "skipped": 0, "failed": 0, "messages": []}
    youtube_series = df[df['is_youtube_playlist'] & (df['current'].notna()) & (df['current'] > 0)]

    if youtube_series.empty:
        results["messages"].append("No YouTube series with watched videos found in CSV.")
        return results

    for _, row in youtube_series.iterrows():
        series_title = row['lecture_series']
        playlist_url = row['playlist_url']
        num_watched = int(row['current'])
        
        current_series_msg = f"Processing series: {series_title} ({num_watched} watched videos)"
        logging.info(current_series_msg)
        results["messages"].append(current_series_msg)

        playlist_videos = get_playlist_videos_yt_dlp(playlist_url)
        if not playlist_videos:
            msg = f"  Could not fetch video list for {series_title}. Skipping."
            logging.warning(msg)
            results["messages"].append(msg)
            results["failed"] += num_watched # Count all potential videos as failed for this series
            continue
        
        for video_idx in range(min(num_watched, len(playlist_videos))):
            video_info = playlist_videos[video_idx]
            video_title = video_info['title']
            video_url = video_info['url']

            if not video_url:
                msg = f"    Skipping video with no URL: {video_title}"
                logging.warning(msg)
                results["messages"].append(msg)
                results["failed"] +=1
                continue

            if check_subtitle_exists(video_url):
                msg = f"    Subtitles already in DB for {video_title}. Skipping."
                logging.info(msg)
                results["messages"].append(msg)
                results["skipped"] += 1
                continue

            subtitles_text = download_subtitles_yt_dlp(video_url)
            if subtitles_text:
                if store_subtitle(series_title, video_idx, video_title, video_url, subtitles_text):
                    results["downloaded"] += 1
                else: # Store subtitle failed
                    results["failed"] +=1
                    results["messages"].append(f"    Failed to store subtitles for {video_title} after download.")
            else:
                msg = f"    Failed to download or no subtitles found for {video_title}."
                logging.warning(msg)
                results["messages"].append(msg)
                results["failed"] += 1
    
    summary_msg = (f"Bulk Download Summary -- Downloaded: {results['downloaded']}, "
                   f"Skipped: {results['skipped']}, Failed: {results['failed']}")
    logging.info(summary_msg)
    results["messages"].append(summary_msg)
    return results

def download_single_subtitle_if_needed(series_title: str, playlist_url: str, video_playlist_index: int) -> Optional[Dict]:
    """Downloads subtitles for a specific video if not already present."""
    playlist_videos = get_playlist_videos_yt_dlp(playlist_url)
    if not playlist_videos or video_playlist_index >= len(playlist_videos):
        return {"error": "Could not find video in playlist or playlist error."}

    video_info = playlist_videos[video_playlist_index]
    video_title = video_info['title']
    video_url = video_info['url']

    if not video_url:
        return {"error": f"Video {video_title} has no URL."}

    if check_subtitle_exists(video_url):
        # Subtitles from DB are already cleaned if store_subtitle was used
        return {"message": f"Subtitles for '{video_title}' already exist.", "subtitles": get_subtitle_for_review(video_url)}

    raw_subtitles_text = download_subtitles_yt_dlp(video_url) # This gets the raw VTT
    if raw_subtitles_text:
        # store_subtitle will clean it before saving
        if store_subtitle(series_title, video_playlist_index, video_title, video_url, raw_subtitles_text):
            # Retrieve the cleaned version from the DB for consistency,
            # or you could return clean_vtt_content(raw_subtitles_text) directly
            # if you don't want an extra DB read here.
            # For now, let's assume get_subtitle_for_review fetches the (now cleaned) stored version.
            cleaned_subtitles_for_display = get_subtitle_for_review(video_url)
            return {"message": f"Successfully downloaded and stored subtitles for '{video_title}'.", "subtitles": cleaned_subtitles_for_display}
        else:
            return {"error": f"Failed to store downloaded subtitles for '{video_title}'."}
    else:
        return {"error": f"Failed to download subtitles for '{video_title}'. yt-dlp reported no subtitles available or an error."}

def check_yt_dlp():
    """Checks if yt-dlp is accessible."""
    try:
        process = subprocess.run([YT_DLP_PATH, '--version'], capture_output=True, check=True, text=True)
        logging.info(f"yt-dlp found: {process.stdout.strip()}")
        return True
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        logging.error(f"Error: '{YT_DLP_PATH}' command not found or not working. {e}")
        logging.error("Please ensure yt-dlp is installed and in your PATH, or update YT_DLP_PATH in lecture_manager_core.py.")
        return False
