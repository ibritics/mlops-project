import time
import pandas as pd
import isodate
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from dateutil import parser
from pathlib import Path
import yaml


# ── API Key Loader ────────────────────────────────────────────────────────────

def load_api_key(
    path: Path | str = Path(__file__).resolve().parents[1] / "secret" / "api_key.yaml"
) -> str:
    """Load the API key from a local secret YAML file."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"API key file not found: {path}")

    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    api_key = data.get("api_key") if isinstance(data, dict) else None
    if not api_key:
        raise ValueError(f"'api_key' field not found in {path}")
    return api_key


# ── Helpers ───────────────────────────────────────────────────────────────────

def _handle_http_error(e: HttpError, context: str = "") -> None:
    """Print a clear diagnosis for common YouTube API errors and re-raise."""
    status = e.resp.status
    reason = ""
    try:
        import json
        details = json.loads(e.content).get("error", {})
        reason = details.get("errors", [{}])[0].get("reason", "")
    except Exception:
        pass

    if status == 403:
        if reason == "forbidden" or "blocked" in str(e.content):
            print("\n" + "=" * 60)
            print("❌  403 FORBIDDEN – YouTube Data API v3 is BLOCKED")
            print("=" * 60)
            print("Most likely causes (fix any one of these):")
            print("  1. YouTube Data API v3 is NOT enabled on your project.")
            print("     → https://console.cloud.google.com/apis/library")
            print("        Search 'YouTube Data API v3' → Enable")
            print()
            print("  2. Your API key has API restrictions that exclude YouTube.")
            print("     → APIs & Services → Credentials → your key")
            print("        API restrictions → add 'YouTube Data API v3'")
            print()
            print("  3. Billing is not enabled on the Cloud project.")
            print("     → https://console.cloud.google.com/billing")
            print("=" * 60 + "\n")
        elif reason == "quotaExceeded":
            print("❌  403 Quota exceeded. Daily limit reached (10,000 units).")
    elif status == 404:
        print(f"❌  404 Not found ({context}). Check the ID.")
    raise e


# ── YouTubeAgent ──────────────────────────────────────────────────────────────

class YouTubeAgent:
    def __init__(self, api_key: str):
        self.youtube = build("youtube", "v3", developerKey=api_key)

    # ── Channel ───────────────────────────────────────────────────────────────

    def resolve_channel_id(self, input_str: str) -> str:
        """
        Accept a UC… channel ID, @handle, or legacy username and always
        return the canonical UC… channel ID.
        """
        # Already a proper channel ID
        if input_str.startswith("UC") and len(input_str) == 24:
            return input_str

        handle = input_str.lstrip("@")

        # Try forHandle (modern)
        try:
            resp = self.youtube.channels().list(
                part="id", forHandle=handle
            ).execute()
            if resp.get("items"):
                return resp["items"][0]["id"]
        except HttpError as e:
            _handle_http_error(e, "resolve_channel_id/forHandle")

        # Try forUsername (legacy)
        try:
            resp = self.youtube.channels().list(
                part="id", forUsername=handle
            ).execute()
            if resp.get("items"):
                return resp["items"][0]["id"]
        except HttpError as e:
            _handle_http_error(e, "resolve_channel_id/forUsername")

        raise ValueError(
            f"Could not resolve '{input_str}' to a YouTube channel ID. "
            "Make sure it is a valid @handle, username, or UC… ID."
        )

    def get_channel_stats(self, channel_id: str) -> dict:
        """Fetch general channel statistics."""
        # Auto-resolve handles / usernames
        channel_id = self.resolve_channel_id(channel_id)

        try:
            request = self.youtube.channels().list(
                part="snippet,contentDetails,statistics",
                id=channel_id
            )
            response = request.execute()
        except HttpError as e:
            _handle_http_error(e, "get_channel_stats")

        items = response.get("items", [])
        if not items:
            raise ValueError(
                f"No channel found for ID '{channel_id}'.\n"
                "Possible reasons:\n"
                "  • The channel ID is wrong or the channel was deleted.\n"
                "  • The channel is private.\n"
                "  • The API key / YouTube Data API v3 is not properly set up.\n"
                "Verify in browser: "
                f"https://www.googleapis.com/youtube/v3/channels"
                f"?part=snippet&id={channel_id}&key=YOUR_KEY"
            )

        item = items[0]
        stats = item.get("statistics", {})
        return {
            "channelName":  item["snippet"]["title"],
            "subscribers":  int(stats.get("subscriberCount", 0)),
            "views":        int(stats.get("viewCount", 0)),
            "totalVideos":  int(stats.get("videoCount", 0)),
            "playlistId":   item["contentDetails"]["relatedPlaylists"]["uploads"],
        }

    # ── Playlist / Video IDs ──────────────────────────────────────────────────

    def get_all_video_ids(self, playlist_id: str, max_retries: int = 3) -> list[str]:
        """Retrieve all video IDs from a playlist using pagination."""
        video_ids: list[str] = []
        next_page_token = None

        while True:
            for attempt in range(1, max_retries + 1):
                try:
                    request = self.youtube.playlistItems().list(
                        part="contentDetails",
                        playlistId=playlist_id,
                        maxResults=50,
                        pageToken=next_page_token,
                    )
                    response = request.execute()
                    break  # success
                except HttpError as e:
                    if e.resp.status == 429 and attempt < max_retries:
                        wait = 60 * attempt
                        print(f"Rate limited. Retrying in {wait}s… (attempt {attempt})")
                        time.sleep(wait)
                    else:
                        _handle_http_error(e, "get_all_video_ids")

            items = response.get("items", [])
            if not items:
                break

            video_ids.extend(
                item["contentDetails"]["videoId"] for item in items
            )
            next_page_token = response.get("nextPageToken")
            if not next_page_token:
                break

        return video_ids

    # ── Video Details ─────────────────────────────────────────────────────────

    def get_video_details(self, video_ids: list[str]) -> pd.DataFrame:
        """Retrieve video metadata in batches of 50 (API limit)."""
        all_video_info = []

        for i in range(0, len(video_ids), 50):
            chunk = video_ids[i : i + 50]
            try:
                request = self.youtube.videos().list(
                    part="snippet,contentDetails,statistics",
                    id=",".join(chunk),
                )
                response = request.execute()
            except HttpError as e:
                _handle_http_error(e, "get_video_details")

            for video in response.get("items", []):
                snippet = video.get("snippet", {})
                stats   = video.get("statistics", {})
                content = video.get("contentDetails", {})

                duration_raw = content.get("duration")
                duration_sec = (
                    isodate.parse_duration(duration_raw).total_seconds()
                    if duration_raw else 0
                )

                published_raw = snippet.get("publishedAt")
                published_at  = (
                    parser.parse(published_raw).replace(tzinfo=None)
                    if published_raw else None
                )

                all_video_info.append({
                    "video_id":     video["id"],
                    "title":        snippet.get("title"),
                    "description":  snippet.get("description", ""),
                    "publishedAt":  published_at,
                    "duration":     duration_sec,
                    "viewCount":    int(stats.get("viewCount",    0)),
                    "likeCount":    int(stats.get("likeCount",    0)),
                    "commentCount": int(stats.get("commentCount", 0)),
                    "tags":         snippet.get("tags", []),
                })

        return pd.DataFrame(all_video_info)

    def get_video_list(self, playlist_id: str, sort_desc: bool = True) -> pd.DataFrame:
        """Return a full channel video list with metadata, sorted by publish time."""
        video_ids = self.get_all_video_ids(playlist_id)
        df = self.get_video_details(video_ids)
        if "publishedAt" in df.columns:
            df = df.sort_values("publishedAt", ascending=not sort_desc).reset_index(drop=True)
        return df

    # ── Comments ──────────────────────────────────────────────────────────────

    def get_comments(
        self, video_ids: list[str], max_comments_per_video: int = 100
    ) -> pd.DataFrame:
        """Fetch top-level comments and replies for a list of videos."""
        all_comments: list[dict] = []

        for v_id in video_ids:
            try:
                next_page_token = None
                count = 0

                while count < max_comments_per_video:
                    request = self.youtube.commentThreads().list(
                        part="snippet,replies",
                        videoId=v_id,
                        maxResults=min(100, max_comments_per_video - count),
                        pageToken=next_page_token,
                        textFormat="plainText",
                    )
                    response = request.execute()

                    for item in response.get("items", []):
                        top = item["snippet"]["topLevelComment"]["snippet"]
                        all_comments.append({
                            "video_id": v_id,
                            "type":     "original",
                            "text":     top["textOriginal"],
                            "author":   top["authorDisplayName"],
                            "likes":    top["likeCount"],
                            "time":     parser.parse(top["publishedAt"]).replace(tzinfo=None),
                        })

                        for reply in item.get("replies", {}).get("comments", []):
                            s = reply["snippet"]
                            all_comments.append({
                                "video_id": v_id,
                                "type":     "reply",
                                "text":     s["textOriginal"],
                                "author":   s["authorDisplayName"],
                                "likes":    s["likeCount"],
                                "time":     parser.parse(s["publishedAt"]).replace(tzinfo=None),
                            })

                    count += len(response.get("items", []))
                    next_page_token = response.get("nextPageToken")
                    if not next_page_token:
                        break

            except HttpError as e:
                if e.resp.status in (403, 404):
                    print(f"⚠  Skipping video {v_id}: comments disabled or video unavailable.")
                else:
                    print(f"⚠  Skipping video {v_id}: {e}")

        return pd.DataFrame(all_comments)


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    API_KEY    = load_api_key()
    CHANNEL_ID = "UC7eBNeDW1GQf2NJQ6G6gAxw"   # ← your channel ID

    agent = YouTubeAgent(API_KEY)

    # 1. Channel Stats
    print("Fetching channel stats…")
    channel_info = agent.get_channel_stats(CHANNEL_ID)
    print(channel_info)
    # pd.DataFrame([channel_info]).to_excel("01_channel_stats.xlsx", index=False)

    # 2. All Videos
    print("\nFetching video list…")
    video_df = agent.get_video_list(channel_info["playlistId"])
    print(video_df.head())
    # video_df.to_excel("02_videos.xlsx", index=False)

    # 3. Comments for first 5 videos
    print("\nFetching comments…")
    top5 = video_df["video_id"].head(5).tolist()
    comments_df = agent.get_comments(top5, max_comments_per_video=50)
    print(comments_df.head())
    # comments_df.to_excel("03_comments.xlsx", index=False)