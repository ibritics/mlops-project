import pandas as pd
import isodate
from googleapiclient.discovery import build
from dateutil import parser
# from feel_it import EmotionClassifier, SentimentClassifier

class YouTubeAgent:
    def __init__(self, api_key: str):
        self.youtube = build('youtube', 'v3', developerKey=api_key)
        # Initialize classifiers once to save memory/load time
        # self.emotion_clf = EmotionClassifier()
        # self.sentiment_clf = SentimentClassifier()

    def get_channel_stats(self, channel_id: str):
        """Fetches general channel statistics."""
        request = self.youtube.channels().list(
            part="snippet,contentDetails,statistics",
            id=channel_id
        )
        response = request.execute()
        item = response['items'][0]
        
        return {
            'channelName': item['snippet']['title'],
            'subscribers': int(item['statistics']['subscriberCount']),
            'views': int(item['statistics']['viewCount']),
            'totalVideos': int(item['statistics']['videoCount']),
            'playlistId': item['contentDetails']['relatedPlaylists']['uploads']
        }

    def get_all_video_ids(self, playlist_id: str):
        """Efficiently retrieves all video IDs from a playlist using pagination."""
        video_ids = []
        next_page_token = None
        
        while True:
            request = self.youtube.playlistItems().list(
                part='contentDetails',
                playlistId=playlist_id,
                maxResults=50,
                pageToken=next_page_token
            )
            response = request.execute()
            
            video_ids.extend([item['contentDetails']['videoId'] for item in response['items']])
            next_page_token = response.get('nextPageToken')
            
            if not next_page_token:
                break
        return video_ids

    def get_video_details(self, video_ids: list):
        """Retrieves details for videos in batches of 50 (API limit)."""
        all_video_info = []
        
        for i in range(0, len(video_ids), 50):
            chunk = video_ids[i:i+50]
            request = self.youtube.videos().list(
                part="snippet,contentDetails,statistics",
                id=','.join(chunk)
            )
            response = request.execute()

            for video in response.get('items', []):
                snippet = video.get('snippet', {})
                stats = video.get('statistics', {})
                content = video.get('contentDetails', {})

                info = {
                    'video_id': video['id'],
                    'title': snippet.get('title'),
                    'publishedAt': parser.parse(snippet.get('publishedAt')).replace(tzinfo=None),
                    'duration': isodate.parse_duration(content.get('duration')).total_seconds(),
                    'viewCount': int(stats.get('viewCount', 0)),
                    'likeCount': int(stats.get('likeCount', 0)),
                    'commentCount': int(stats.get('commentCount', 0)),
                    'tags': snippet.get('tags', [])
                }
                all_video_info.append(info)
        
        return pd.DataFrame(all_video_info)

    def get_comments(self, video_ids: list, max_comments_per_video=100):
        """Fetches comments and replies for a list of videos."""
        all_comments = []

        for v_id in video_ids:
            try:
                next_page_token = None
                count = 0
                while count < max_comments_per_video:
                    request = self.youtube.commentThreads().list(
                        part="snippet,replies",
                        videoId=v_id,
                        maxResults=100,
                        pageToken=next_page_token,
                        textFormat="plainText"
                    )
                    response = request.execute()

                    for item in response.get('items', []):
                        # Top-level comment
                        top_comment = item['snippet']['topLevelComment']['snippet']
                        all_comments.append({
                            'video_id': v_id,
                            'type': 'original',
                            'text': top_comment['textOriginal'],
                            'author': top_comment['authorDisplayName'],
                            'likes': top_comment['likeCount'],
                            'time': parser.parse(top_comment['publishedAt']).replace(tzinfo=None)
                        })

                        # Replies
                        if 'replies' in item:
                            for reply in item['replies']['comments']:
                                all_comments.append({
                                    'video_id': v_id,
                                    'type': 'reply',
                                    'text': reply['snippet']['textOriginal'],
                                    'author': reply['snippet']['authorDisplayName'],
                                    'likes': reply['snippet']['likeCount'],
                                    'time': parser.parse(reply['snippet']['publishedAt']).replace(tzinfo=None)
                                })
                    
                    count += len(response.get('items', []))
                    next_page_token = response.get('nextPageToken')
                    if not next_page_token: break

            except Exception as e:
                print(f"Skipping video {v_id}: Comments might be disabled.")
        
        return pd.DataFrame(all_comments)

    def analyze_sentiment(self, df: pd.DataFrame, text_column='text'):
        """Performs batch sentiment and emotion analysis."""
        if df.empty: return df
        
        texts = df[text_column].tolist()
        df['emotion'] = self.emotion_clf.predict(texts)
        df['sentiment'] = self.sentiment_clf.predict(texts)
        return df
API_KEY = "AIzaSyBFDxewfxKIzNkBhSBKlSUJzxESenU6L9Q"
CHANNEL_ID = "UCeZ7biz6kkMcEcyma7lUcFA"

agent = YouTubeAgent(API_KEY)

# 1. Channel Stats
print("Fetching Channel Stats...")
channel_info = agent.get_channel_stats(CHANNEL_ID)
pd.DataFrame([channel_info]).to_excel("01_channel_report.xlsx", index=False)

# 2. Get Video IDs and Details
print("Fetching Video Details...")
video_ids = agent.get_all_video_ids(channel_info['playlistId'])
video_df = agent.get_video_details(video_ids)
video_df.to_excel("02_video_insights.xlsx", index=False)

# 3. Get Comments (Limited to first 5 videos for speed/quota testing)
print("Fetching Comments...")
comments_df = agent.get_comments(video_ids[:5])

# 4. Sentiment Analysis
# print("Analyzing Sentiments (Italian)...")
# comments_df = agent.analyze_sentiment(comments_df)
comments_df.to_excel("03_sentiment_report.xlsx", index=False)

print("Done! All reports generated.")