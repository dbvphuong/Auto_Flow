from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
import os

# Define the database path
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB_PATH = os.path.join(BASE_DIR, 'data', 'app.db')

engine = create_engine(f'sqlite:///{DB_PATH}', echo=False)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

def init_db():
    from .models import Account, Task, ImageSession, VideoSession, GeminiBatch
    Base.metadata.create_all(bind=engine)
    
    # Đảm bảo cột use_proxy tồn tại trong cơ sở dữ liệu SQLite
    from sqlalchemy import text
    with engine.connect() as conn:
        try:
            conn.execute(text("SELECT use_proxy FROM accounts LIMIT 1"))
        except Exception:
            try:
                # Thực hiện thêm cột use_proxy với giá trị mặc định là True (1)
                conn.execute(text("ALTER TABLE accounts ADD COLUMN use_proxy BOOLEAN DEFAULT 1"))
                # Một số phiên bản SQLAlchemy yêu cầu commit thủ công khi dùng engine.connect()
                try:
                    conn.commit()
                except:
                    pass
                import logging
                logging.info("[DB] Đã nâng cấp schema: Thêm cột use_proxy thành công.")
            except Exception as e:
                import logging
                logging.error(f"[DB] Lỗi khi thêm cột use_proxy: {e}")
                
    # Đảm bảo cột chrome_profile tồn tại trong cơ sở dữ liệu SQLite
    with engine.connect() as conn:
        try:
            conn.execute(text("SELECT chrome_profile FROM accounts LIMIT 1"))
        except Exception:
            try:
                conn.execute(text("ALTER TABLE accounts ADD COLUMN chrome_profile VARCHAR DEFAULT '_tool_profile_'"))
                try:
                    conn.commit()
                except:
                    pass
                import logging
                logging.info("[DB] Đã nâng cấp schema: Thêm cột chrome_profile thành công.")
            except Exception as e:
                import logging
                logging.error(f"[DB] Lỗi khi thêm cột chrome_profile: {e}")

    # Đảm bảo cột position tồn tại trong cơ sở dữ liệu SQLite
    with engine.connect() as conn:
        try:
            conn.execute(text("SELECT position FROM accounts LIMIT 1"))
        except Exception:
            try:
                conn.execute(text("ALTER TABLE accounts ADD COLUMN position INTEGER DEFAULT 0"))
                try:
                    conn.commit()
                except:
                    pass
                import logging
                logging.info("[DB] Đã nâng cấp schema: Thêm cột position thành công.")
            except Exception as e:
                import logging
                logging.error(f"[DB] Lỗi khi thêm cột position: {e}")

    # Mỗi account phải được bật riêng thì mới được dùng cho Gemini.
    with engine.connect() as conn:
        try:
            conn.execute(text("SELECT is_gemini FROM accounts LIMIT 1"))
        except Exception:
            try:
                conn.execute(text(
                    "ALTER TABLE accounts ADD COLUMN is_gemini BOOLEAN DEFAULT 0"
                ))
                conn.commit()
                import logging
                logging.info("[DB] Đã nâng cấp schema: Thêm cột is_gemini thành công.")
            except Exception as e:
                import logging
                logging.error(f"[DB] Lỗi khi thêm cột is_gemini: {e}")

    # Đảm bảo cột task_type tồn tại trong cơ sở dữ liệu SQLite
    with engine.connect() as conn:
        try:
            conn.execute(text("SELECT task_type FROM tasks LIMIT 1"))
        except Exception:
            try:
                conn.execute(text("ALTER TABLE tasks ADD COLUMN task_type VARCHAR DEFAULT 'image'"))
                try:
                    conn.commit()
                except:
                    pass
                import logging
                logging.info("[DB] Đã nâng cấp schema: Thêm cột task_type thành công.")
            except Exception as e:
                import logging
                logging.error(f"[DB] Lỗi khi thêm cột task_type: {e}")

    # Đảm bảo cột session_id tồn tại trong bảng tasks
    with engine.connect() as conn:
        try:
            conn.execute(text("SELECT session_id FROM tasks LIMIT 1"))
        except Exception:
            try:
                conn.execute(text("ALTER TABLE tasks ADD COLUMN session_id INTEGER"))
                try:
                    conn.commit()
                except:
                    pass
                import logging
                logging.info("[DB] Đã nâng cấp schema: Thêm cột session_id thành công.")
            except Exception as e:
                import logging
                logging.error(f"[DB] Lỗi khi thêm cột session_id: {e}")

    # Lưu số lần retry của task ảnh/video để chỉ báo ERROR sau khi hết lượt.
    with engine.connect() as conn:
        try:
            conn.execute(text("SELECT retry_count FROM tasks LIMIT 1"))
        except Exception:
            try:
                conn.execute(text(
                    "ALTER TABLE tasks ADD COLUMN retry_count INTEGER DEFAULT 0"
                ))
                conn.commit()
                import logging
                logging.info("[DB] Đã nâng cấp schema: Thêm retry_count cho tasks.")
            except Exception as e:
                import logging
                logging.error(f"[DB] Lỗi khi thêm retry_count cho tasks: {e}")

    # Đảm bảo cột prompts_text tồn tại trong bảng image_sessions
    with engine.connect() as conn:
        try:
            conn.execute(text("SELECT prompts_text FROM image_sessions LIMIT 1"))
        except Exception:
            try:
                conn.execute(text("ALTER TABLE image_sessions ADD COLUMN prompts_text VARCHAR"))
                try:
                    conn.commit()
                except:
                    pass
                import logging
                logging.info("[DB] Đã nâng cấp schema: Thêm cột prompts_text cho image_sessions thành công.")
            except Exception as e:
                import logging
                logging.error(f"[DB] Lỗi khi thêm cột prompts_text cho image_sessions: {e}")

    # Nâng cấp bảng Gemini đã được tạo bởi các phiên bản trước.
    gemini_columns = {
        "country": "VARCHAR",
        "max_continuations": "INTEGER DEFAULT 10",
        "done_marker": "VARCHAR DEFAULT '[[DONE]]'",
        "retry_count": "INTEGER DEFAULT 0",
    }
    with engine.connect() as conn:
        for column_name, column_type in gemini_columns.items():
            try:
                conn.execute(text(f"SELECT {column_name} FROM gemini_batches LIMIT 1"))
            except Exception:
                try:
                    conn.execute(text(
                        f"ALTER TABLE gemini_batches ADD COLUMN {column_name} {column_type}"
                    ))
                    conn.commit()
                except Exception as e:
                    import logging
                    logging.error(f"[DB] Lỗi khi thêm cột Gemini {column_name}: {e}")
    
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
