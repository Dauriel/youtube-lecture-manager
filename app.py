# app.py
from flask import Flask, render_template, request, redirect, url_for, flash
import lecture_manager_core as core 
from load_df import download_last_version
app = Flask(__name__)
app.secret_key = "your_very_secret_key_here" 

# Global variables
lectures_df = None
yt_dlp_ready = False

def initialize_app_data():
    """
    Initializes database, checks yt-dlp, and loads lecture data.
    This function is called once at app startup.
    """
    global lectures_df, yt_dlp_ready # Declare modification intent at the beginning

    core.logging.info("Initializing application data...")
    core.init_db()
    yt_dlp_ready = core.check_yt_dlp()
    
    lectures_df = core.load_and_prepare_data() # Assign to global
    if lectures_df is None:
        core.logging.error("CRITICAL: lectures.csv could not be loaded at startup. App may not function correctly.")
    else:
        core.logging.info("lectures.csv loaded successfully at startup.")
        core._playlist_cache = {}

initialize_app_data()

@app.route('/')
def index():
    # We primarily READ globals here. If we need to re-assign lectures_df,
    # the global declaration must be at the top of this function too.
    # Let's restructure to make this clearer.
    global lectures_df, yt_dlp_ready 

    local_lectures_df = lectures_df # Work with a local copy for reading

    if local_lectures_df is None:
        # Try to load again, maybe the file was fixed
        core.logging.warning("lectures_df was None at start of index route. Attempting reload.")
        reloaded_df = core.load_and_prepare_data()
        if reloaded_df is None:
            flash("CRITICAL Error: Could not load or parse lectures.csv. Please check the file and server logs.", "danger")
        else: 
            lectures_df = reloaded_df # Assign to the global lectures_df
            core._playlist_cache = {}
            local_lectures_df = lectures_df # Update local copy
            flash("Notice: lectures.csv was reloaded successfully after an initial problem.", "info")

    if not yt_dlp_ready:
         flash("CRITICAL WARNING: yt-dlp is not found or not working. Subtitle features will fail. Please check installation and server logs.", "danger")

    num_series = len(local_lectures_df) if local_lectures_df is not None else 0
    num_yt_series = 0
    if local_lectures_df is not None and 'is_youtube_playlist' in local_lectures_df.columns:
        yt_series_df = local_lectures_df[local_lectures_df['is_youtube_playlist']]
        if not yt_series_df.empty:
             num_yt_series = len(yt_series_df)
    return render_template('index.html', num_series=num_series, num_yt_series=num_yt_series)

@app.route('/select_to_watch', methods=['POST'])
def select_to_watch():
    # This function only reads global lectures_df and yt_dlp_ready
    # So, `global` keyword is not strictly needed here unless we were assigning to them.
    # However, for consistency and to avoid future errors if we modify it,
    # it's good practice to declare it if the global is central to the function.
    global lectures_df, yt_dlp_ready

    if lectures_df is None:
        flash("Lecture data not loaded. Please ensure lectures.csv is correct and try refreshing CSV data.", "warning")
        return redirect(url_for('index'))
    if not yt_dlp_ready:
        flash("Warning: yt-dlp is not working. Playlist video fetching might fail.", "warning")
        # Allow to proceed, but with a warning

    result = core.select_random_lecture_to_watch(lectures_df)
    if result and "error" not in result:
        flash(result.get("message", "Lecture selected!"), "success")
        return render_template('lecture_display.html', lecture=result, action_type="watch")
    else:
        flash(result.get("error", "Could not select a lecture."), "danger")
    return redirect(url_for('index'))

@app.route('/select_for_review', methods=['POST'])
def select_for_review():
    global lectures_df, yt_dlp_ready

    if lectures_df is None:
        flash("Lecture data not loaded. Please ensure lectures.csv is correct and try refreshing CSV data.", "warning")
        return redirect(url_for('index'))
    if not yt_dlp_ready:
        flash("Warning: yt-dlp is not working. Subtitle features might fail.", "warning")
        
    result = core.select_random_watched_lecture_for_review(lectures_df)
    
    if result and "error" not in result and lectures_df is not None:
        series_data = lectures_df[lectures_df['lecture_series'] == result.get('series_title')]
        if not series_data.empty and 'playlist_url' in series_data.columns:
            if 'playlist_url' not in result or not result.get('playlist_url'): # Check if key exists and is non-empty
                 result['playlist_url'] = series_data.iloc[0].get('playlist_url')

    if result and "error" not in result:
        flash(result.get("message", "Lecture for review selected!"), "info")
        return render_template('lecture_display.html', lecture=result, action_type="review")
    else:
        flash(result.get("error", "Could not select a lecture for review."), "danger")
    return redirect(url_for('index'))

@app.route('/download_review_subtitle', methods=['POST'])
def download_review_subtitle():
    global lectures_df, yt_dlp_ready # Reading globals

    if lectures_df is None: 
        flash("Critical Error: Lecture data not available. Cannot download subtitle.", "danger")
        return redirect(url_for('index'))
    if not yt_dlp_ready:
        flash("Error: yt-dlp is not working. Cannot download subtitles.", "danger")
        # Attempt to re-render the page with original data, but it's tricky without full state.
        # For simplicity, redirect or show a generic error page.
        # A more robust solution would involve passing enough state back or storing it in session.
        # Let's try to pass back what we can if we have form data
        series_title_form = request.form.get('series_title')
        video_title_form = request.form.get('video_title')
        video_url_form = request.form.get('video_url')
        playlist_index_form = request.form.get('video_playlist_index', type=int)
        playlist_url_form = request.form.get('playlist_url')
        if all([series_title_form, video_title_form, video_url_form, playlist_index_form is not None]):
            lecture_data_for_display = {"series_title": series_title_form, "video_title": video_title_form, "video_url": video_url_form, "playlist_index": playlist_index_form, "playlist_url": playlist_url_form, "subtitles": None, "subtitles_exist_in_db": False, "error": "yt-dlp not working"}
            return render_template('lecture_display.html', lecture=lecture_data_for_display, action_type="review")
        return redirect(url_for('index'))


    series_title = request.form.get('series_title')
    playlist_url_from_form = request.form.get('playlist_url') 
    video_playlist_index = request.form.get('video_playlist_index', type=int)
    video_url_for_page = request.form.get('video_url') 
    video_title_for_page = request.form.get('video_title')

    if not all([series_title, video_playlist_index is not None, video_url_for_page, video_title_for_page]):
        flash("Missing required data to download subtitle. Please try again.", "danger")
        return redirect(url_for('index')) 

    final_playlist_url = playlist_url_from_form
    if not final_playlist_url or "youtube.com/playlist?list=" not in str(final_playlist_url):
        core.logging.warning(f"Playlist URL from form ('{playlist_url_from_form}') is invalid or missing for {series_title}. Attempting lookup from CSV data.")
        series_row = lectures_df[lectures_df['lecture_series'] == series_title]
        if not series_row.empty and 'playlist_url' in series_row.columns:
            final_playlist_url = series_row.iloc[0]['playlist_url']
            core.logging.info(f"Looked up playlist URL from CSV: {final_playlist_url} for series {series_title}")
        else:
            core.logging.error(f"Could not determine a valid playlist URL for series '{series_title}'. Form value: '{playlist_url_from_form}'.")
            flash(f"Could not determine playlist URL for series '{series_title}'. Cannot download subtitle.", "danger")
            lecture_data_for_display = {"series_title": series_title, "video_title": video_title_for_page, "video_url": video_url_for_page, "playlist_index": video_playlist_index, "playlist_url": playlist_url_from_form, "subtitles": None, "subtitles_exist_in_db": False, "error": "Could not find playlist URL"}
            return render_template('lecture_display.html', lecture=lecture_data_for_display, action_type="review")

    if not final_playlist_url or "youtube.com/playlist?list=" not in str(final_playlist_url):
        flash(f"Still unable to get a valid playlist URL for '{series_title}'. Cannot download subtitle.", "danger")
        lecture_data_for_display = {"series_title": series_title, "video_title": video_title_for_page, "video_url": video_url_for_page, "playlist_index": video_playlist_index, "playlist_url": final_playlist_url, "subtitles": None, "subtitles_exist_in_db": False, "error": "Invalid playlist URL"}
        return render_template('lecture_display.html', lecture=lecture_data_for_display, action_type="review")

    download_result = core.download_single_subtitle_if_needed(series_title, final_playlist_url, video_playlist_index)
    
    lecture_data_for_display = {
        "series_title": series_title,
        "video_title": video_title_for_page,
        "video_url": video_url_for_page,
        "playlist_index": video_playlist_index,
        "playlist_url": final_playlist_url,
        "subtitles": download_result.get("subtitles"),
        "subtitles_exist_in_db": bool(download_result.get("subtitles"))
    }

    if "error" in download_result:
        flash(f"Subtitle download error: {download_result['error']}", "danger")
    else:
        flash(download_result.get("message", "Subtitle action completed."), "success")
        lecture_data_for_display["subtitles_exist_in_db"] = True 

    return render_template('lecture_display.html', lecture=lecture_data_for_display, action_type="review")


@app.route('/bulk_download_subtitles', methods=['POST'])
def bulk_download_subtitles():
    global lectures_df, yt_dlp_ready # Reading globals

    if lectures_df is None:
        flash("Lecture data not loaded. Please ensure lectures.csv is correct and try refreshing CSV data.", "warning")
        return redirect(url_for('index'))
    if not yt_dlp_ready:
        flash("Error: yt-dlp is not working. Cannot bulk download subtitles.", "danger")
        return redirect(url_for('index'))
        
    flash("Starting bulk subtitle download. This may take a while... Check server logs for detailed progress.", "info")
    results = core.bulk_download_watched_subtitles(lectures_df)
    
    summary = (f"Bulk Download Complete. Downloaded: {results['downloaded']}, "
               f"Skipped: {results['skipped']}, Failed: {results['failed']}.")
    flash(summary, "success" if results['downloaded'] > 0 or results['skipped'] > 0 else "warning")
    
    return render_template('bulk_status.html', results=results)

@app.route('/refresh_csv', methods=['POST'])
def refresh_csv():
    global lectures_df # Modifying global

    core.logging.info("Attempting to refresh CSV data via web UI...")
    temp_df = core.load_and_prepare_data()
    if temp_df is not None:
        lectures_df = temp_df # Assign to global
        core._playlist_cache = {} 
        flash("CSV data reloaded successfully.", "success")
        core.logging.info("CSV data reloaded successfully via web UI.")
    else:
        flash("Error: Failed to reload CSV data. Check server logs. The application might be using stale or no data.", "danger")
        core.logging.error("Failed to reload CSV data via web UI.")
    return redirect(url_for('index'))


if __name__ == '__main__':
    app.run(debug=True)
