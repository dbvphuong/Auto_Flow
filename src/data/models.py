from sqlalchemy import Column, Integer, String, Boolean, DateTime, Text
from datetime import datetime
from .database import Base

class Account(Base):
    __tablename__ = "accounts"
    
    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True)
    is_active = Column(Boolean, default=True)
    is_image = Column(Boolean, default=True)
    is_video = Column(Boolean, default=False)
    is_gemini = Column(Boolean, default=False)
    account_type = Column(String, default="FREE") # PRO, FREE
    credits = Column(Integer, default=0)
    proxy = Column(String, nullable=True) # host:port:user:pass
    use_proxy = Column(Boolean, default=True)
    chrome_profile = Column(String, default="_tool_profile_")
    cookie_expiry = Column(DateTime, nullable=True)
    cookies_json = Column(String, nullable=True) # Store cookies as JSON string
    position = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
    
class Task(Base):
    __tablename__ = "tasks"
    
    id = Column(Integer, primary_key=True, index=True)
    prompt = Column(String, index=True)
    image_name = Column(String, nullable=True)
    reference_image = Column(String, nullable=True)
    status = Column(String, default="PENDING") # PENDING, RUNNING, COMPLETED, ERROR
    result_path = Column(String, nullable=True)
    account_id = Column(Integer, nullable=True) # Which account executed this
    task_type = Column(String, default="image")
    session_id = Column(Integer, nullable=True) # Link to image_sessions table
    retry_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)

class ImageSession(Base):
    __tablename__ = "image_sessions"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True)
    ref_dir = Column(String, nullable=True)
    save_dir = Column(String, nullable=True)
    prompts_text = Column(String, nullable=True) # Store prompts list, newline-separated
    status = Column(String, default="PENDING") # PENDING, RUNNING, COMPLETED, ERROR
    created_at = Column(DateTime, default=datetime.utcnow)


class VideoSession(Base):
    __tablename__ = "video_sessions"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True)
    ref_dir = Column(String, nullable=True)
    save_dir = Column(String, nullable=True)
    prompts_text = Column(String, nullable=True) # Store prompts list, newline-separated
    status = Column(String, default="PENDING") # PENDING, RUNNING, COMPLETED, ERROR
    created_at = Column(DateTime, default=datetime.utcnow)


class GeminiBatch(Base):
    __tablename__ = "gemini_batches"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True)
    source_path = Column(String, nullable=True)
    story_content = Column(Text, nullable=False)
    master_prompt = Column(Text, nullable=False)
    output_dir = Column(String, nullable=False)
    total_parts = Column(Integer, default=1)
    current_part = Column(Integer, default=0)
    country = Column(String, nullable=True)
    max_continuations = Column(Integer, default=10)
    done_marker = Column(String, default="[[DONE]]")
    status = Column(String, default="PENDING")
    account_id = Column(Integer, nullable=True)
    result_path = Column(String, nullable=True)
    error_message = Column(Text, nullable=True)
    retry_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
