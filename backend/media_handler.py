# media_handler.py
import os
import cv2
import tempfile
import aiohttp
import trafilatura
from urllib.parse import urlparse
from config import TEMP_IMAGE_DIR, MEDIA_FRAMES_TO_EXTRACT, MEDIA_MAX_TEXT_LENGTH, MEDIA_EXTENSIONS

os.makedirs(TEMP_IMAGE_DIR, exist_ok=True)

def extract_frames(video_path, num_frames=None):
    """Extracts evenly spaced frames from a Video or GIF."""
    if num_frames is None:
        num_frames = MEDIA_FRAMES_TO_EXTRACT
    
    cap = cv2.VideoCapture(video_path)
    frames_paths = []
    
    if not cap.isOpened():
        return frames_paths

    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    if total_frames <= 0:
        total_frames = 10 # Fallback

    # Calculate intervals to get 'num_frames' evenly spaced
    intervals = [int(i * (total_frames / num_frames)) for i in range(num_frames)]
    
    for idx, frame_idx in enumerate(intervals):
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
        ret, frame = cap.read()
        if ret:
            frame_filename = f"frame_{os.urandom(4).hex()}_{idx}.jpg"
            frame_path = os.path.join(TEMP_IMAGE_DIR, frame_filename)
            cv2.imwrite(frame_path, frame)
            frames_paths.append(frame_filename)
            
    cap.release()
    return frames_paths

async def process_url(url):
    """Determines if a URL is media or an article, and processes it."""
    parsed = urlparse(url)
    ext = os.path.splitext(parsed.path)[1].lower()
    
    if ext in MEDIA_EXTENSIONS:
        # It's a direct media link, download it
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as response:
                    if response.status == 200:
                        content = await response.read()
                        filename = f"dl_{os.urandom(6).hex()}{ext}"
                        filepath = os.path.join(TEMP_IMAGE_DIR, filename)
                        with open(filepath, "wb") as f:
                            f.write(content)
                        return {"type": "media", "filepath": filepath, "ext": ext}
        except Exception as e:
            return {"type": "error", "content": str(e)}
    
    # Otherwise, assume it's a webpage and scrape the text
    downloaded = trafilatura.fetch_url(url)
    if downloaded:
        text = trafilatura.extract(downloaded)
        if text:
            # Truncate to save VRAM context window (configurable)
            truncated_text = " ".join(text.split()[:MEDIA_MAX_TEXT_LENGTH]) 
            return {"type": "text", "content": truncated_text}
            
    return {"type": "unknown"}