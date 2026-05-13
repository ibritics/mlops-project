# agent.py
from googleapiclient.discovery import build

class YouTubeAgent:
    def __init__(self, api_key: str):
        # REMOVED the {} around api_key
        self.youtube = build("youtube", "v3", developerKey=api_key)

    def analyze_comments(self, video_id: str):
        print(f"Analyzing comments for video: {video_id}")
        
        # Adding a simple API call to verify it works
        request = self.youtube.commentThreads().list(
            part="snippet",
            videoId=video_id,
            maxResults=5
        )
        response = request.execute()
        
        return response

# Use your key as a plain string
agent = YouTubeAgent('AIzaSyBFDxewfxKIzNkBhSBKlSUJzxESenU6L9Q')

# Use ONLY the 11-character video ID
result = agent.analyze_comments('0vdmbI-aTvs')

print(result)