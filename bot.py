import logging
import time
import json
import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Any
import pytz
import psycopg2
from psycopg2 import pool
from telegram import (
    Update, InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters
)
from telegram.constants import ParseMode

# تنظیمات لاگ
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# تنظیمات اصلی
TOKEN = "8121929322:AAGlD1LAXROb2DG_34rY94Yl6cFBA4pZsBA"  # توکن ربات خود را اینجا قرار دهید
ADMIN_IDS = [6680287530]  # آیدی عددی ادمین‌ها
MAX_STUDY_TIME = 120  # حداکثر زمان مطالعه به دقیقه (۲ ساعت)
MIN_STUDY_TIME = 10   # حداقل زمان مطالعه به دقیقه

# تنظیمات دیتابیس PostgreSQL
DB_CONFIG = {
    "host": "localhost",
    "database": "focustodo_db",
    "user": "postgres",
    "password": "m13821382",
    "port": "5432"
}

# زمان ایران
IRAN_TZ = pytz.timezone('Asia/Tehran')

# دروس پیش‌فرض
SUBJECTS = [
    "فیزیک", "شیمی", "ریاضی", "زیست",
    "ادبیات", "عربی", "دینی", "زبان",
    "تاریخ", "جغرافیا", "هویت", "سایر"
]

# زمان‌های پیشنهادی
SUGGESTED_TIMES = [
    ("۳۰ دقیقه", 30),
    ("۴۵ دقیقه", 45),
    ("۱ ساعت", 60),
    ("۱.۵ ساعت", 90),
    ("۲ ساعت", 120)
]

# -----------------------------------------------------------
# مدیریت دیتابیس
# -----------------------------------------------------------

class Database:
    """کلاس مدیریت دیتابیس PostgreSQL"""
    
    def __init__(self):
        self.connection_pool = None
        self.init_pool()
        self.create_tables()
    
    def init_pool(self):
        """ایجاد Connection Pool"""
        try:
            self.connection_pool = psycopg2.pool.SimpleConnectionPool(
                1, 20,
                host=DB_CONFIG["host"],
                database=DB_CONFIG["database"],
                user=DB_CONFIG["user"],
                password=DB_CONFIG["password"],
                port=DB_CONFIG["port"]
            )
            logger.info("✅ Connection Pool ایجاد شد")
        except Exception as e:
            logger.error(f"❌ خطا در اتصال به دیتابیس: {e}")
            raise
    
    def get_connection(self):
        """دریافت یک Connection از Pool"""
        return self.connection_pool.getconn()
    
    def return_connection(self, connection):
        """بازگرداندن Connection به Pool"""
        self.connection_pool.putconn(connection)
    
    def execute_query(self, query, params=None, fetch=False, fetchall=False):
        """اجرای کوئری"""
        conn = None
        cursor = None
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            
            cursor.execute(query, params or ())
            
            if fetch:
                result = cursor.fetchone()
            elif fetchall:
                result = cursor.fetchall()
            else:
                conn.commit()
                result = cursor.rowcount
            
            return result
            
        except Exception as e:
            logger.error(f"❌ خطا در اجرای کوئری: {e}")
            if conn:
                conn.rollback()
            raise
            
        finally:
            if cursor:
                cursor.close()
            if conn:
                self.return_connection(conn)
    
    def create_tables(self):
        """ایجاد جداول دیتابیس"""
        queries = [
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY,
                username VARCHAR(255),
                grade VARCHAR(50),
                field VARCHAR(50),
                message TEXT,
                is_active BOOLEAN DEFAULT FALSE,
                registration_date VARCHAR(50),
                total_study_time INTEGER DEFAULT 0,
                total_sessions INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS study_sessions (
                session_id SERIAL PRIMARY KEY,
                user_id BIGINT REFERENCES users(user_id),
                subject VARCHAR(100),
                topic TEXT,
                minutes INTEGER,
                start_time BIGINT,
                end_time BIGINT,
                completed BOOLEAN DEFAULT FALSE,
                date VARCHAR(50),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS files (
                file_id SERIAL PRIMARY KEY,
                grade VARCHAR(50),
                field VARCHAR(50),
                subject VARCHAR(100),
                topic TEXT,
                description TEXT,
                telegram_file_id VARCHAR(500),
                file_name VARCHAR(255),
                file_size INTEGER,
                mime_type VARCHAR(100),
                upload_date VARCHAR(50),
                download_count INTEGER DEFAULT 0,
                uploader_id BIGINT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS daily_rankings (
                id SERIAL PRIMARY KEY,
                user_id BIGINT REFERENCES users(user_id),
                date VARCHAR(50),
                total_minutes INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, date)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS registration_requests (
                request_id SERIAL PRIMARY KEY,
                user_id BIGINT,
                username VARCHAR(255),
                grade VARCHAR(50),
                field VARCHAR(50),
                message TEXT,
                status VARCHAR(20) DEFAULT 'pending',
                admin_note TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        ]
        
        for query in queries:
            try:
                self.execute_query(query)
            except Exception as e:
                logger.warning(f"خطا در ایجاد جدول: {e}")
        
        logger.info("✅ جداول دیتابیس بررسی شدند")

# ایجاد نمونه دیتابیس
db = Database()

# -----------------------------------------------------------
# توابع کمکی
# -----------------------------------------------------------

def get_iran_time() -> Tuple[str, str]:
    """دریافت تاریخ و زمان ایران"""
    now = datetime.now(IRAN_TZ)
    date_str = now.strftime("%Y/%m/%d")
    time_str = now.strftime("%H:%M")
    return date_str, time_str

def format_time(minutes: int) -> str:
    """تبدیل دقیقه به فرمت خوانا"""
    hours = minutes // 60
    mins = minutes % 60
    
    if hours > 0 and mins > 0:
        return f"{hours} ساعت و {mins} دقیقه"
    elif hours > 0:
        return f"{hours} ساعت"
    else:
        return f"{mins} دقیقه"

def calculate_score(minutes: int) -> int:
    """محاسبه امتیاز بر اساس زمان مطالعه"""
    return int(minutes * 1.5)

def is_admin(user_id: int) -> bool:
    """بررسی ادمین بودن کاربر"""
    return user_id in ADMIN_IDS

def validate_file_type(file_name: str) -> bool:
    """بررسی مجاز بودن نوع فایل"""
    allowed_extensions = ['.pdf', '.doc', '.docx', '.ppt', '.pptx', 
                         '.xls', '.xlsx', '.txt', '.mp4', '.mp3',
                         '.jpg', '.jpeg', '.png', '.zip', '.rar']
    
    file_ext = os.path.splitext(file_name.lower())[1]
    return file_ext in allowed_extensions

def get_file_size_limit(file_name: str) -> int:
    """دریافت محدودیت حجم بر اساس نوع فایل"""
    # غیرفعال کردن محدودیت
    return 500 * 1024 * 1024  # 500 MB برای همه فایل‌ها
    
    # یا کاملاً غیرفعال:
    # return float('inf')  # بدون محدودیت

# -----------------------------------------------------------
# مدیریت کاربران
# -----------------------------------------------------------

def register_user(user_id: int, username: str, grade: str, field: str, message: str = "") -> bool:
    """ثبت کاربر جدید در دیتابیس"""
    try:
        date_str, _ = get_iran_time()
        
        # ذخیره درخواست ثبت‌نام
        query = """
        INSERT INTO registration_requests (user_id, username, grade, field, message, status)
        VALUES (%s, %s, %s, %s, %s, 'pending')
        """
        db.execute_query(query, (user_id, username, grade, field, message))
        
        logger.info(f"درخواست ثبت‌نام جدید: {username} ({user_id})")
        return True
        
    except Exception as e:
        logger.error(f"خطا در ثبت کاربر: {e}")
        return False

def get_pending_requests() -> List[Dict]:
    """دریافت درخواست‌های ثبت‌نام در انتظار"""
    query = """
    SELECT request_id, user_id, username, grade, field, message, created_at
    FROM registration_requests
    WHERE status = 'pending'
    ORDER BY created_at DESC
    """
    
    results = db.execute_query(query, fetchall=True)
    
    requests = []
    if results:
        for row in results:
            requests.append({
                "request_id": row[0],
                "user_id": row[1],
                "username": row[2],
                "grade": row[3],
                "field": row[4],
                "message": row[5],
                "created_at": row[6]
            })
    
    return requests

def approve_registration(request_id: int, admin_note: str = "") -> bool:
    """تأیید درخواست ثبت‌نام"""
    try:
        # دریافت اطلاعات درخواست
        query = """
        SELECT user_id, username, grade, field, message
        FROM registration_requests
        WHERE request_id = %s AND status = 'pending'
        """
        result = db.execute_query(query, (request_id,), fetch=True)
        
        if not result:
            return False
        
        user_id, username, grade, field, message = result
        
        # افزودن به جدول کاربران
        date_str, _ = get_iran_time()
        query = """
        INSERT INTO users (user_id, username, grade, field, message, is_active, registration_date)
        VALUES (%s, %s, %s, %s, %s, TRUE, %s)
        ON CONFLICT (user_id) DO UPDATE SET
            is_active = TRUE,
            grade = EXCLUDED.grade,
            field = EXCLUDED.field,
            message = EXCLUDED.message
        """
        db.execute_query(query, (user_id, username, grade, field, message, date_str))
        
        # به‌روزرسانی وضعیت درخواست
        query = """
        UPDATE registration_requests
        SET status = 'approved', admin_note = %s
        WHERE request_id = %s
        """
        db.execute_query(query, (admin_note, request_id))
        
        logger.info(f"کاربر تأیید شد: {username} ({user_id})")
        return True
        
    except Exception as e:
        logger.error(f"خطا در تأیید کاربر: {e}")
        return False

def reject_registration(request_id: int, admin_note: str) -> bool:
    """رد درخواست ثبت‌نام"""
    try:
        query = """
        UPDATE registration_requests
        SET status = 'rejected', admin_note = %s
        WHERE request_id = %s AND status = 'pending'
        """
        db.execute_query(query, (admin_note, request_id))
        
        logger.info(f"درخواست رد شد: {request_id}")
        return True
        
    except Exception as e:
        logger.error(f"خطا در رد درخواست: {e}")
        return False

def activate_user(user_id: int) -> bool:
    """فعال‌سازی کاربر"""
    try:
        query = """
        UPDATE users
        SET is_active = TRUE
        WHERE user_id = %s
        """
        db.execute_query(query, (user_id,))
        
        logger.info(f"کاربر فعال شد: {user_id}")
        return True
        
    except Exception as e:
        logger.error(f"خطا در فعال‌سازی کاربر: {e}")
        return False

def deactivate_user(user_id: int) -> bool:
    """غیرفعال‌سازی کاربر"""
    try:
        query = """
        UPDATE users
        SET is_active = FALSE
        WHERE user_id = %s
        """
        db.execute_query(query, (user_id,))
        
        logger.info(f"کاربر غیرفعال شد: {user_id}")
        return True
        
    except Exception as e:
        logger.error(f"خطا در غیرفعال‌سازی کاربر: {e}")
        return False

def is_user_active(user_id: int) -> bool:
    """بررسی فعال بودن کاربر"""
    try:
        query = """
        SELECT is_active FROM users WHERE user_id = %s
        """
        result = db.execute_query(query, (user_id,), fetch=True)
        
        return result and result[0]
        
    except Exception as e:
        logger.error(f"خطا در بررسی وضعیت کاربر: {e}")
        return False

def get_user_info(user_id: int) -> Optional[Dict]:
    """دریافت اطلاعات کاربر"""
    try:
        query = """
        SELECT username, grade, field, total_study_time, total_sessions
        FROM users
        WHERE user_id = %s
        """
        result = db.execute_query(query, (user_id,), fetch=True)
        
        if result:
            return {
                "username": result[0],
                "grade": result[1],
                "field": result[2],
                "total_study_time": result[3],
                "total_sessions": result[4]
            }
        return None
        
    except Exception as e:
        logger.error(f"خطا در دریافت اطلاعات کاربر: {e}")
        return None

# -----------------------------------------------------------
# مدیریت جلسات مطالعه
# -----------------------------------------------------------

def start_study_session(user_id: int, subject: str, topic: str, minutes: int) -> Optional[int]:
    """شروع جلسه مطالعه جدید"""
    try:
        # 👇 این بررسی را اضافه کنید
        # بررسی وجود کاربر در جدول users
        query_check = "SELECT user_id FROM users WHERE user_id = %s AND is_active = TRUE"
        user_check = db.execute_query(query_check, (user_id,), fetch=True)
        
        if not user_check:
            logger.error(f"کاربر {user_id} فعال نیست یا وجود ندارد")
            return None
        
        start_timestamp = int(time.time())
        date_str, _ = get_iran_time()
        
        query = """
        INSERT INTO study_sessions (user_id, subject, topic, minutes, start_time, date)
        VALUES (%s, %s, %s, %s, %s, %s)
        RETURNING session_id
        """
        
        result = db.execute_query(query, (user_id, subject, topic, minutes, start_timestamp, date_str), fetch=True)
        
        if result:
            session_id = result[0]
            logger.info(f"جلسه مطالعه شروع شد: {session_id} برای کاربر {user_id}")
            return session_id
        
        return None
        
    except Exception as e:
        logger.error(f"خطا در شروع جلسه مطالعه: {e}")
        return None

def complete_study_session(session_id: int) -> Optional[Dict]:
    """اتمام جلسه مطالعه"""
    try:
        end_timestamp = int(time.time())
        
        # تکمیل جلسه
        query = """
        UPDATE study_sessions
        SET end_time = %s, completed = TRUE
        WHERE session_id = %s AND completed = FALSE
        RETURNING user_id, subject, topic, minutes, start_time
        """
        
        result = db.execute_query(query, (end_timestamp, session_id), fetch=True)
        
        if not result:
            return None
        
        user_id, subject, topic, minutes, start_time = result
        
        # به‌روزرسانی آمار کاربر - با کنترل خطا
        try:
            query = """
            UPDATE users
            SET 
                total_study_time = total_study_time + %s,
                total_sessions = total_sessions + 1
            WHERE user_id = %s
            """
            db.execute_query(query, (minutes, user_id))
        except Exception as e:
            logger.warning(f"کاربر {user_id} در جدول users نیست: {e}")
            # کاربر را به جدول اضافه کنیم یا فقط هشدار دهیم
        
        # به‌روزرسانی رتبه‌بندی روزانه - با کنترل خطا
        try:
            date_str, _ = get_iran_time()
            query = """
            INSERT INTO daily_rankings (user_id, date, total_minutes)
            VALUES (%s, %s, %s)
            ON CONFLICT (user_id, date) DO UPDATE SET
                total_minutes = daily_rankings.total_minutes + EXCLUDED.total_minutes
            """
            db.execute_query(query, (user_id, date_str, minutes))
        except Exception as e:
            logger.warning(f"خطا در به‌روزرسانی رتبه‌بندی: {e}")
        
        session_data = {
            "user_id": user_id,
            "subject": subject,
            "topic": topic,
            "minutes": minutes,
            "start_time": start_time,
            "end_time": end_timestamp,
            "session_id": session_id
        }
        
        logger.info(f"جلسه مطالعه تکمیل شد: {session_id}")
        return session_data
        
    except Exception as e:
        logger.error(f"خطا در تکمیل جلسه مطالعه: {e}")
        return None

def get_user_sessions(user_id: int, limit: int = 10) -> List[Dict]:
    """دریافت جلسات اخیر کاربر"""
    try:
        query = """
        SELECT session_id, subject, topic, minutes, date, start_time, completed
        FROM study_sessions
        WHERE user_id = %s
        ORDER BY start_time DESC
        LIMIT %s
        """
        
        results = db.execute_query(query, (user_id, limit), fetchall=True)
        
        sessions = []
        if results:
            for row in results:
                sessions.append({
                    "session_id": row[0],
                    "subject": row[1],
                    "topic": row[2],
                    "minutes": row[3],
                    "date": row[4],
                    "start_time": row[5],
                    "completed": row[6]
                })
        
        return sessions
        
    except Exception as e:
        logger.error(f"خطا در دریافت جلسات کاربر: {e}")
        return []

# -----------------------------------------------------------
# سیستم رتبه‌بندی
# -----------------------------------------------------------

def get_today_rankings() -> List[Dict]:
    """دریافت رتبه‌بندی امروز"""
    try:
        date_str, _ = get_iran_time()
        
        query = """
        SELECT u.user_id, u.username, u.grade, u.field, dr.total_minutes
        FROM daily_rankings dr
        JOIN users u ON dr.user_id = u.user_id
        WHERE dr.date = %s AND u.is_active = TRUE
        ORDER BY dr.total_minutes DESC
        LIMIT 20
        """
        
        results = db.execute_query(query, (date_str,), fetchall=True)
        
        rankings = []
        if results:
            for row in results:
                rankings.append({
                    "user_id": row[0],
                    "username": row[1],
                    "grade": row[2],
                    "field": row[3],
                    "total_minutes": row[4]
                })
        
        return rankings
        
    except Exception as e:
        logger.error(f"خطا در دریافت رتبه‌بندی: {e}")
        return []

def get_user_rank_today(user_id: int) -> Tuple[Optional[int], Optional[int]]:
    """دریافت رتبه و زمان کاربر در امروز"""
    try:
        date_str, _ = get_iran_time()
        
        # دریافت زمان کاربر
        query = """
        SELECT total_minutes FROM daily_rankings
        WHERE user_id = %s AND date = %s
        """
        result = db.execute_query(query, (user_id, date_str), fetch=True)
        
        if not result:
            return None, 0
        
        user_minutes = result[0]
        
        # محاسبه رتبه
        query = """
        SELECT COUNT(*) FROM daily_rankings
        WHERE date = %s AND total_minutes > %s
        """
        result = db.execute_query(query, (date_str, user_minutes), fetch=True)
        
        rank = result[0] + 1 if result else 1
        return rank, user_minutes
        
    except Exception as e:
        logger.error(f"خطا در محاسبه رتبه کاربر: {e}")
        return None, 0

# -----------------------------------------------------------
# مدیریت فایل‌ها
# -----------------------------------------------------------

def add_file(grade: str, field: str, subject: str, topic: str, 
             description: str, telegram_file_id: str, file_name: str,
             file_size: int, mime_type: str, uploader_id: int) -> Optional[Dict]:
    """افزودن فایل جدید به دیتابیس"""
    try:
        upload_date, _ = get_iran_time()
        
        query = """
        INSERT INTO files (grade, field, subject, topic, description, 
                          telegram_file_id, file_name, file_size, mime_type, 
                          upload_date, uploader_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING file_id, upload_date
        """
        
        result = db.execute_query(query, (
            grade, field, subject, topic, description,
            telegram_file_id, file_name, file_size, mime_type,
            upload_date, uploader_id
        ), fetch=True)
        
        if result:
            file_data = {
                "file_id": result[0],
                "grade": grade,
                "field": field,
                "subject": subject,
                "topic": topic,
                "description": description,
                "file_name": file_name,
                "file_size": file_size,
                "upload_date": result[1]
            }
            
            logger.info(f"فایل آپلود شد: {file_name} (ID: {result[0]})")
            return file_data
        
        return None
        
    except Exception as e:
        logger.error(f"خطا در آپلود فایل: {e}")
        return None

def get_user_files(user_id: int) -> List[Dict]:
    """دریافت فایل‌های مرتبط با کاربر"""
    try:
        # دریافت اطلاعات کاربر
        user_info = get_user_info(user_id)
        if not user_info:
            return []
        
        grade = user_info["grade"]
        field = user_info["field"]
        
        query = """
        SELECT file_id, subject, topic, description, file_name, file_size, upload_date, download_count
        FROM files
        WHERE grade = %s AND field = %s
        ORDER BY upload_date DESC
        LIMIT 50
        """
        
        results = db.execute_query(query, (grade, field), fetchall=True)
        
        files = []
        if results:
            for row in results:
                files.append({
                    "file_id": row[0],
                    "subject": row[1],
                    "topic": row[2],
                    "description": row[3],
                    "file_name": row[4],
                    "file_size": row[5],
                    "upload_date": row[6],
                    "download_count": row[7]
                })
        
        return files
        
    except Exception as e:
        logger.error(f"خطا در دریافت فایل‌های کاربر: {e}")
        return []

def get_files_by_subject(user_id: int, subject: str) -> List[Dict]:
    """دریافت فایل‌های یک درس خاص"""
    try:
        user_info = get_user_info(user_id)
        if not user_info:
            return []
        
        grade = user_info["grade"]
        field = user_info["field"]
        
        query = """
        SELECT file_id, topic, description, file_name, file_size, upload_date, download_count
        FROM files
        WHERE grade = %s AND field = %s AND subject = %s
        ORDER BY upload_date DESC
        """
        
        results = db.execute_query(query, (grade, field, subject), fetchall=True)
        
        files = []
        if results:
            for row in results:
                files.append({
                    "file_id": row[0],
                    "topic": row[1],
                    "description": row[2],
                    "file_name": row[3],
                    "file_size": row[4],
                    "upload_date": row[5],
                    "download_count": row[6]
                })
        
        return files
        
    except Exception as e:
        logger.error(f"خطا در دریافت فایل‌های درس: {e}")
        return []

def get_file_by_id(file_id: int) -> Optional[Dict]:
    """دریافت اطلاعات فایل بر اساس ID"""
    try:
        query = """
        SELECT file_id, grade, field, subject, topic, description,
               telegram_file_id, file_name, file_size, mime_type,
               upload_date, download_count, uploader_id
        FROM files
        WHERE file_id = %s
        """
        
        result = db.execute_query(query, (file_id,), fetch=True)
        
        if result:
            return {
                "file_id": result[0],
                "grade": result[1],
                "field": result[2],
                "subject": result[3],
                "topic": result[4],
                "description": result[5],
                "telegram_file_id": result[6],
                "file_name": result[7],
                "file_size": result[8],
                "mime_type": result[9],
                "upload_date": result[10],
                "download_count": result[11],
                "uploader_id": result[12]
            }
        
        return None
        
    except Exception as e:
        logger.error(f"خطا در دریافت فایل: {e}")
        return None

def increment_download_count(file_id: int) -> bool:
    """افزایش شمارنده دانلود فایل"""
    try:
        query = """
        UPDATE files
        SET download_count = download_count + 1
        WHERE file_id = %s
        """
        db.execute_query(query, (file_id,))
        return True
    except Exception as e:
        logger.error(f"خطا در به‌روزرسانی شمارنده دانلود: {e}")
        return False

def get_all_files() -> List[Dict]:
    """دریافت همه فایل‌ها (برای ادمین)"""
    try:
        query = """
        SELECT file_id, grade, field, subject, topic, file_name, 
               file_size, upload_date, download_count
        FROM files
        ORDER BY upload_date DESC
        LIMIT 100
        """
        
        results = db.execute_query(query, fetchall=True)
        
        files = []
        if results:
            for row in results:
                files.append({
                    "file_id": row[0],
                    "grade": row[1],
                    "field": row[2],
                    "subject": row[3],
                    "topic": row[4],
                    "file_name": row[5],
                    "file_size": row[6],
                    "upload_date": row[7],
                    "download_count": row[8]
                })
        
        return files
        
    except Exception as e:
        logger.error(f"خطا در دریافت همه فایل‌ها: {e}")
        return []

def delete_file(file_id: int) -> bool:
    """حذف فایل"""
    try:
        query = "DELETE FROM files WHERE file_id = %s"
        db.execute_query(query, (file_id,))
        logger.info(f"فایل حذف شد: {file_id}")
        return True
    except Exception as e:
        logger.error(f"خطا در حذف فایل: {e}")
        return False

# -----------------------------------------------------------
# کیبوردهای اینلاین
# -----------------------------------------------------------

def get_main_menu() -> InlineKeyboardMarkup:
    """منوی اصلی"""
    keyboard = [
        [
            InlineKeyboardButton("🏆 رتبه‌بندی", callback_data="rankings"),
            InlineKeyboardButton("📚 منابع", callback_data="files"),
            InlineKeyboardButton("➕ ثبت مطالعه", callback_data="start_study")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_subjects_keyboard() -> InlineKeyboardMarkup:
    """کیبورد انتخاب درس"""
    keyboard = []
    row = []
    
    for i, subject in enumerate(SUBJECTS):
        row.append(InlineKeyboardButton(subject, callback_data=f"subject_{subject}"))
        if len(row) == 2:
            keyboard.append(row)
            row = []
    
    if row:
        keyboard.append(row)
    
    keyboard.append([InlineKeyboardButton("🔙 بازگشت", callback_data="main_menu")])
    
    return InlineKeyboardMarkup(keyboard)

def get_time_selection_keyboard() -> InlineKeyboardMarkup:
    """کیبورد انتخاب زمان"""
    keyboard = []
    
    for text, minutes in SUGGESTED_TIMES:
        keyboard.append([InlineKeyboardButton(text, callback_data=f"time_{minutes}")])
    
    keyboard.append([
        InlineKeyboardButton("✏️ زمان دلخواه", callback_data="custom_time"),
        InlineKeyboardButton("🔙 بازگشت", callback_data="choose_subject")
    ])
    
    return InlineKeyboardMarkup(keyboard)

def get_admin_keyboard() -> InlineKeyboardMarkup:
    """منوی ادمین"""
    keyboard = [
        [InlineKeyboardButton("📤 آپلود فایل", callback_data="admin_upload")],
        [InlineKeyboardButton("👥 درخواست‌ها", callback_data="admin_requests")],
        [InlineKeyboardButton("📁 مدیریت فایل‌ها", callback_data="admin_manage_files")],
        [InlineKeyboardButton("📊 آمار ربات", callback_data="admin_stats")],
        [InlineKeyboardButton("🏠 منوی اصلی", callback_data="main_menu")]
    ]
    return InlineKeyboardMarkup(keyboard)

def get_file_subjects_keyboard(user_id: int) -> InlineKeyboardMarkup:
    """کیبورد انتخاب درس برای منابع"""
    user_files = get_user_files(user_id)
    subjects = list(set([f["subject"] for f in user_files]))
    
    keyboard = []
    row = []
    
    for subject in subjects:
        row.append(InlineKeyboardButton(subject, callback_data=f"filesub_{subject}"))
        if len(row) == 2:
            keyboard.append(row)
            row = []
    
    if row:
        keyboard.append(row)
    
    if not subjects:
        keyboard.append([InlineKeyboardButton("📭 فایلی موجود نیست", callback_data="none")])
    
    keyboard.append([InlineKeyboardButton("🔙 بازگشت", callback_data="main_menu")])
    
    return InlineKeyboardMarkup(keyboard)

def get_pending_requests_keyboard() -> InlineKeyboardMarkup:
    """کیبورد درخواست‌های در انتظار"""
    requests = get_pending_requests()
    
    keyboard = []
    for req in requests[:5]:  # حداکثر 5 درخواست
        keyboard.append([
            InlineKeyboardButton(
                f"👤 {req['username']} - {req['grade']} {req['field']}",
                callback_data=f"view_request_{req['request_id']}"
            )
        ])
    
    if not requests:
        keyboard.append([InlineKeyboardButton("📭 درخواستی موجود نیست", callback_data="none")])
    
    keyboard.append([
        InlineKeyboardButton("🔄 به‌روزرسانی", callback_data="admin_requests"),
        InlineKeyboardButton("🔙 بازگشت", callback_data="admin_panel")
    ])
    
    return InlineKeyboardMarkup(keyboard)

def get_request_action_keyboard(request_id: int) -> InlineKeyboardMarkup:
    """کیبورد اقدامات برای درخواست"""
    keyboard = [
        [
            InlineKeyboardButton("✅ تأیید", callback_data=f"approve_{request_id}"),
            InlineKeyboardButton("❌ رد", callback_data=f"reject_{request_id}")
        ],
        [
            InlineKeyboardButton("🔙 بازگشت", callback_data="admin_requests"),
            InlineKeyboardButton("🏠 منوی اصلی", callback_data="main_menu")
        ]
    ]
    return InlineKeyboardMarkup(keyboard)

# -----------------------------------------------------------
# هندلرهای دستورات
# -----------------------------------------------------------

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """دستور /start"""
    user = update.effective_user
    user_id = user.id
    
    # اگر کاربر ثبت‌نام نکرده
    if user_id not in [r[0] for r in db.execute_query("SELECT user_id FROM users WHERE user_id = %s", (user_id,), fetchall=True)]:
        await update.message.reply_text(
            "👋 به ربات Focus Todo خوش آمدید!\n\n"
            "📝 برای استفاده از ربات، ابتدا باید ثبت‌نام کنید.\n"
            "لطفا اطلاعات زیر را ارسال کنید:\n\n"
            "1. پایه تحصیلی\n"
            "2. رشته\n"
            "3. یک پیام آزاد درباره خودتان\n\n"
            "مثال:\n"
            "دوازدهم\n"
            "تجربی\n"
            "علاقه‌مند به یادگیری و پیشرفت"
        )
        context.user_data["awaiting_registration"] = True
        return
    
    # اگر کاربر ثبت‌نام کرده اما غیرفعال است
    if not is_user_active(user_id):
        await update.message.reply_text(
            "⏳ حساب کاربری شما در حال بررسی است.\n"
            "لطفا منتظر تأیید ادمین باشید."
        )
        return
    
    # کاربر فعال
    await update.message.reply_text(
        "🎯 به Focus Todo خوش آمدید!\n\n"
        "📚 سیستم مدیریت مطالعه و رقابت سالم\n"
        "⏰ تایمر هوشمند | 🏆 رتبه‌بندی آنلاین\n"
        "📖 منابع شخصی‌سازی شده\n\n"
        "لطفا یک گزینه انتخاب کنید:",
        reply_markup=get_main_menu()
    )

async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """دستور /admin (فقط برای ادمین‌ها)"""
    user_id = update.effective_user.id
    
    if not is_admin(user_id):
        await update.message.reply_text("❌ دسترسی denied.")
        return
    
    context.user_data["admin_mode"] = True
    await update.message.reply_text(
        "👨‍💼 پنل مدیریت\n"
        "لطفا یک عملیات انتخاب کنید:",
        reply_markup=get_admin_keyboard()
    )

async def active_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """فعال‌سازی کاربر توسط ادمین"""
    user_id = update.effective_user.id
    
    if not is_admin(user_id):
        await update.message.reply_text("❌ دسترسی denied.")
        return
    
    if not context.args:
        await update.message.reply_text(
            "⚠️ لطفا آیدی کاربر را وارد کنید:\n"
            "مثال: /active 123456789"
        )
        return
    
    try:
        target_user_id = int(context.args[0])
        if activate_user(target_user_id):
            await update.message.reply_text(f"✅ کاربر {target_user_id} فعال شد.")
        else:
            await update.message.reply_text("❌ کاربر یافت نشد.")
    except ValueError:
        await update.message.reply_text("❌ آیدی باید عددی باشد.")

async def deactive_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """غیرفعال‌سازی کاربر توسط ادمین"""
    user_id = update.effective_user.id
    
    if not is_admin(user_id):
        await update.message.reply_text("❌ دسترسی denied.")
        return
    
    if not context.args:
        await update.message.reply_text(
            "⚠️ لطفا آیدی کاربر را وارد کنید:\n"
            "مثال: /deactive 123456789"
        )
        return
    
    try:
        target_user_id = int(context.args[0])
        if deactivate_user(target_user_id):
            await update.message.reply_text(f"✅ کاربر {target_user_id} غیرفعال شد.")
        else:
            await update.message.reply_text("❌ کاربر یافت نشد.")
    except ValueError:
        await update.message.reply_text("❌ آیدی باید عددی باشد.")

async def addfile_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """افزودن فایل توسط ادمین"""
    user_id = update.effective_user.id
    
    if not is_admin(user_id):
        await update.message.reply_text("❌ دسترسی denied.")
        return
    
    if len(context.args) < 4:
        await update.message.reply_text(
            "⚠️ فرمت صحیح:\n"
            "/addfile <پایه> <رشته> <درس> <مبحث>\n\n"
            "مثال:\n"
            "/addfile دوازدهم تجربی فیزیک دینامیک\n\n"
            "📝 توضیح اختیاری را در خط بعدی بنویسید."
        )
        return
    
    grade = context.args[0]
    field = context.args[1]
    subject = context.args[2]
    topic = " ".join(context.args[3:])
    
    context.user_data["awaiting_file"] = {
        "grade": grade,
        "field": field,
        "subject": subject,
        "topic": topic,
        "description": "",
        "uploader_id": user_id
    }
    
    await update.message.reply_text(
        f"📤 آماده آپلود فایل:\n\n"
        f"🎓 پایه: {grade}\n"
        f"🧪 رشته: {field}\n"
        f"📚 درس: {subject}\n"
        f"🎯 مبحث: {topic}\n\n"
        f"📝 لطفا توضیحی برای فایل وارد کنید (اختیاری):\n"
        f"یا برای رد شدن از این مرحله /skip بزنید."
    )

async def skip_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """رد شدن از مرحله توضیح فایل"""
    user_id = update.effective_user.id
    
    if not is_admin(user_id) or "awaiting_file" not in context.user_data:
        await update.message.reply_text("❌ دستور نامعتبر.")
        return
    
    await update.message.reply_text(
        "✅ مرحله توضیح رد شد.\n"
        "📎 لطفا فایل را ارسال کنید..."
    )

# -----------------------------------------------------------
# هندلرهای پیام متنی
# -----------------------------------------------------------


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """پردازش پیام‌های متنی"""
    user_id = update.effective_user.id
    text = update.message.text.strip()
    
    # ثبت‌نام کاربر جدید
    if context.user_data.get("awaiting_registration"):
        lines = text.split('\n')
        if len(lines) >= 3:
            grade = lines[0].strip()
            field = lines[1].strip()
            message = '\n'.join(lines[2:]).strip()
            
            if register_user(user_id, update.effective_user.username, grade, field, message):
                await update.message.reply_text(
                    "✅ درخواست شما ثبت شد!\n\n"
                    "⏳ درخواست شما برای ادمین ارسال شد.\n"
                    "پس از تأیید، می‌توانید از ربات استفاده کنید.\n\n"
                    "برای بررسی وضعیت /start را بزنید."
                )
            else:
                await update.message.reply_text(
                    "❌ خطا در ثبت اطلاعات.\n"
                    "لطفا مجدد تلاش کنید."
                )
            
            context.user_data.clear()
        else:
            await update.message.reply_text(
                "❌ فرمت پیام صحیح نیست.\n"
                "لطفا به فرمت زیر ارسال کنید:\n\n"
                "پایه\nرشته\nپیام"
            )
        return
    # ۲. مبحث مطالعه (مهم: این باید قبل از awaiting_custom_time باشد)
    if context.user_data.get("awaiting_topic"):
        topic = text
        subject = context.user_data.get("selected_subject", "نامشخص")
        minutes = context.user_data.get("selected_time", 60)
        
        # شروع جلسه مطالعه
        session_id = start_study_session(user_id, subject, topic, minutes)
        
        if session_id:
            context.user_data["current_session"] = session_id
            date_str, time_str = get_iran_time()
            
            await update.message.reply_text(
                f"✅ تایمر شروع شد!\n\n"
                f"📚 درس: {subject}\n"
                f"🎯 مبحث: {topic}\n"
                f"⏱ مدت: {format_time(minutes)}\n"
                f"📅 تاریخ: {date_str}\n"
                f"🕒 شروع: {time_str}\n\n"
                f"⏳ تایمر در حال اجرا...\n\n"
                f"برای اتمام زودتر دکمه زیر را بزنید:",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("✅ اتمام مطالعه", callback_data="complete_study")
                ]])
            )
            
            # پاک کردن وضعیت
            context.user_data.pop("awaiting_topic", None)
            context.user_data.pop("selected_subject", None)
            context.user_data.pop("selected_time", None)
            
            # تنظیم تایمر برای اتمام خودکار
            context.job_queue.run_once(
                auto_complete_study,
                minutes * 60,
                data={"session_id": session_id, "chat_id": update.effective_chat.id, "user_id": user_id},
                name=str(session_id)
            )
        else:
            await update.message.reply_text(
                "❌ خطا در شروع تایمر.\n"
                "لطفا مجدد تلاش کنید.",
                reply_markup=get_main_menu()
            )
        return
    
    # ۳. زمان دلخواه (بعد از مبحث)
    if context.user_data.get("awaiting_custom_time"):
        try:
            minutes = int(text)
            if minutes < MIN_STUDY_TIME:
                await update.message.reply_text(
                    f"❌ زمان باید حداقل {MIN_STUDY_TIME} دقیقه باشد."
                )
            elif minutes > MAX_STUDY_TIME:
                await update.message.reply_text(
                    f"❌ زمان نباید بیشتر از {MAX_STUDY_TIME} دقیقه (۲ ساعت) باشد."
                )
            else:
                context.user_data["selected_time"] = minutes
                context.user_data["awaiting_topic"] = True  # 🔥 اینجا درست تنظیم شود
                
                subject = context.user_data.get("selected_subject", "نامشخص")
                await update.message.reply_text(
                    f"⏱ زمان انتخاب شده: {format_time(minutes)}\n\n"
                    f"📚 درس: {subject}\n\n"
                    f"✏️ لطفا مبحث مطالعه را وارد کنید:\n"
                    f"(مثال: حل مسائل فصل ۳)"
                )
                
                # پاک کردن وضعیت زمان
                context.user_data.pop("awaiting_custom_time", None)
        except ValueError:
            await update.message.reply_text(
                "❌ لطفا یک عدد وارد کنید.\n"
                f"(بین {MIN_STUDY_TIME} تا {MAX_STUDY_TIME} دقیقه)"
            )
        return
    
    # ۴. اگر پیام متنی دیگر بود
    await update.message.reply_text(
        "لطفا از منوی ربات استفاده کنید.",
        reply_markup=get_main_menu()
            )
    # توضیح فایل برای آپلود توسط ادمین
    if context.user_data.get("awaiting_file_description"):
        context.user_data["awaiting_file"]["description"] = text
        context.user_data["awaiting_file_document"] = True
        
        file_info = context.user_data["awaiting_file"]
        await update.message.reply_text(
            f"✅ توضیح ذخیره شد.\n\n"
            f"📤 آماده آپلود فایل:\n\n"
            f"🎓 پایه: {file_info['grade']}\n"
            f"🧪 رشته: {file_info['field']}\n"
            f"📚 درس: {file_info['subject']}\n"
            f"🎯 مبحث: {file_info['topic']}\n"
            f"📝 توضیح: {text}\n\n"
            f"📎 لطفا فایل را ارسال کنید..."
        )
        return
    
    # اگر پیام متنی دیگر بود
    await update.message.reply_text(
        "لطفا از منوی ربات استفاده کنید.",
        reply_markup=get_main_menu()
    )

# -----------------------------------------------------------
# هندلرهای فایل
# -----------------------------------------------------------

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """پردازش فایل‌های ارسالی"""
    user_id = update.effective_user.id
    document = update.message.document
    
    # اگر ادمین در حال آپلود فایل است
    if ("awaiting_file" in context.user_data or "awaiting_file_document" in context.user_data) and is_admin(user_id):
        
        if "awaiting_file" not in context.user_data:
            await update.message.reply_text("❌ ابتدا اطلاعات فایل را وارد کنید.")
            return
        
        file_info = context.user_data["awaiting_file"]
        
        # بررسی نوع فایل
        if not validate_file_type(document.file_name):
            await update.message.reply_text(
                f"❌ نوع فایل مجاز نیست.\n\n"
                f"✅ فرمت‌های مجاز:\n"
                f"PDF, DOC, DOCX, PPT, PPTX, XLS, XLSX\n"
                f"TXT, MP4, MP3, JPG, JPEG, PNG, ZIP, RAR"
            )
            return
        
        # بررسی حجم فایل
        file_size_limit = get_file_size_limit(document.file_name)
        if document.file_size > file_size_limit:
            size_mb = file_size_limit / (1024 * 1024)
            await update.message.reply_text(
                f"❌ حجم فایل زیاد است.\n"
                f"حداکثر حجم برای این نوع فایل: {size_mb:.1f} MB"
            )
            return
        
        # ذخیره فایل در دیتابیس
        file_data = add_file(
            grade=file_info["grade"],
            field=file_info["field"],
            subject=file_info["subject"],
            topic=file_info["topic"],
            description=file_info.get("description", ""),
            telegram_file_id=document.file_id,
            file_name=document.file_name,
            file_size=document.file_size,
            mime_type=document.mime_type,
            uploader_id=user_id
        )
        
        if file_data:
            await update.message.reply_text(
                f"✅ فایل با موفقیت آپلود شد!\n\n"
                f"📄 نام: {file_data['file_name']}\n"
                f"📦 حجم: {file_data['file_size'] // 1024} KB\n"
                f"🎓 پایه: {file_data['grade']}\n"
                f"🧪 رشته: {file_data['field']}\n"
                f"📚 درس: {file_data['subject']}\n"
                f"🎯 مبحث: {file_data['topic']}\n"
                f"🆔 کد فایل: FD-{file_data['file_id']}\n\n"
                f"این فایل در دسترس دانش‌آموزان مرتبط قرار گرفت."
            )
        else:
            await update.message.reply_text("❌ خطا در آپلود فایل.")
        
        # پاکسازی داده‌های موقت
        context.user_data.pop("awaiting_file", None)
        context.user_data.pop("awaiting_file_description", None)
        context.user_data.pop("awaiting_file_document", None)
        return
    
    await update.message.reply_text("📎 فایل دریافت شد.")

# -----------------------------------------------------------
# هندلرهای کال‌بک
# -----------------------------------------------------------

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """پردازش کلیک روی دکمه‌های اینلاین"""
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    callback_data = query.data
    
    # منوی اصلی
    if callback_data == "main_menu":
        await show_main_menu(query)
    
    # شروع مطالعه
    elif callback_data == "start_study":
        await start_study_process(query, context)
    
    # انتخاب درس
    elif callback_data == "choose_subject":
        await choose_subject(query)
    
    elif callback_data.startswith("subject_"):
        subject = callback_data.replace("subject_", "")
        await select_subject(query, context, subject)
    
    # انتخاب زمان
    elif callback_data.startswith("time_"):
        minutes = int(callback_data.replace("time_", ""))
        await select_time(query, context, minutes)
    
    elif callback_data == "custom_time":
        await request_custom_time(query, context)
    
    # اتمام مطالعه
    elif callback_data == "complete_study":
        await complete_study_process(query, context, user_id)
    
    # رتبه‌بندی
    elif callback_data == "rankings":
        await show_rankings(query, user_id)
    
    # منابع و فایل‌ها
    elif callback_data == "files":
        await show_files_menu(query, user_id)
    
    elif callback_data.startswith("filesub_"):
        subject = callback_data.replace("filesub_", "")
        await show_subject_files(query, user_id, subject)
    
    elif callback_data.startswith("download_"):
        file_id = int(callback_data.replace("download_", ""))
        await download_file(query, file_id, user_id, context)
    
    # پنل ادمین
    elif callback_data == "admin_panel":
        await show_admin_panel(query)
    
    elif callback_data == "admin_upload":
        await show_admin_upload(query)
    
    elif callback_data == "admin_requests":
        await show_admin_requests(query)
    
    elif callback_data == "admin_manage_files":
        await show_admin_manage_files(query)
    
    elif callback_data == "admin_stats":
        await show_admin_stats(query)
    
    elif callback_data.startswith("view_request_"):
        request_id = int(callback_data.replace("view_request_", ""))
        await show_request_details(query, request_id)
    
    elif callback_data.startswith("approve_"):
        request_id = int(callback_data.replace("approve_", ""))
        await approve_request(query, request_id, user_id, context)
    
    elif callback_data.startswith("reject_"):
        request_id = int(callback_data.replace("reject_", ""))
        await reject_request(query, request_id, context)
    
    elif callback_data.startswith("delete_file_"):
        file_id = int(callback_data.replace("delete_file_", ""))
        await delete_file_process(query, file_id, context)

async def show_main_menu(query) -> None:
    """نمایش منوی اصلی"""
    await query.edit_message_text(
        "🎯 به Focus Todo خوش آمدید!\n\n"
        "📚 سیستم مدیریت مطالعه و رقابت سالم\n"
        "⏰ تایمر هوشمند | 🏆 رتبه‌بندی آنلاین\n"
        "📖 منابع شخصی‌سازی شده\n\n"
        "لطفا یک گزینه انتخاب کنید:",
        reply_markup=get_main_menu()
    )

async def start_study_process(query, context) -> None:
    """شروع فرآیند ثبت مطالعه"""
    await query.edit_message_text(
        "📚 لطفا درس مورد نظر را انتخاب کنید:",
        reply_markup=get_subjects_keyboard()
    )

async def choose_subject(query) -> None:
    """انتخاب درس"""
    await query.edit_message_text(
        "📚 لطفا درس مورد نظر را انتخاب کنید:",
        reply_markup=get_subjects_keyboard()
    )

async def select_subject(query, context, subject: str) -> None:
    """ذخیره درس انتخاب شده و نمایش انتخاب زمان"""
    context.user_data["selected_subject"] = subject
    
    await query.edit_message_text(
        f"⏰ تنظیم تایمر\n\n"
        f"📝 درس انتخاب شده: **{subject}**\n\n"
        f"⏱ لطفا مدت زمان مطالعه را انتخاب کنید:\n"
        f"(حداکثر {MAX_STUDY_TIME//60} ساعت)",
        reply_markup=get_time_selection_keyboard(),
        parse_mode=ParseMode.MARKDOWN
    )

async def select_time(query, context, minutes: int) -> None:
    """ذخیره زمان انتخاب شده و درخواست مبحث"""
    context.user_data["selected_time"] = minutes
    context.user_data["awaiting_topic"] = True
    
    subject = context.user_data.get("selected_subject", "نامشخص")
    
    await query.edit_message_text(
        f"⏱ زمان انتخاب شده: {format_time(minutes)}\n\n"
        f"📚 درس: {subject}\n\n"
        f"✏️ لطفا مبحث مطالعه را وارد کنید:\n"
        f"(مثال: حل مسائل فصل ۳)"
    )

async def request_custom_time(query, context) -> None:
    """درخواست زمان دلخواه"""
    context.user_data["awaiting_custom_time"] = True
    
    await query.edit_message_text(
        f"✏️ زمان دلخواه\n\n"
        f"⏱ لطفا زمان را به دقیقه وارد کنید:\n"
        f"(بین {MIN_STUDY_TIME} تا {MAX_STUDY_TIME} دقیقه)\n\n"
        f"مثال: ۹۰ (برای ۱ ساعت و ۳۰ دقیقه)"
    )

async def complete_study_process(query, context, user_id: int) -> None:
    """اتمام جلسه مطالعه"""
    if "current_session" not in context.user_data:
        await query.edit_message_text(
            "❌ جلسه‌ای فعال نیست.",
            reply_markup=get_main_menu()
        )
        return
    
    # لغو جاب تایمر
    session_id = context.user_data["current_session"]
    jobs = context.job_queue.get_jobs_by_name(str(session_id))
    for job in jobs:
        job.schedule_removal()
    
    # تکمیل جلسه
    session = complete_study_session(session_id)
    
    if session:
        date_str, time_str = get_iran_time()
        score = calculate_score(session["minutes"])
        
        # دریافت رتبه کاربر
        rank, total_minutes = get_user_rank_today(user_id)
        
        rank_text = f"🏆 رتبه شما امروز: {rank}" if rank else ""
        
        await query.edit_message_text(
            f"✅ مطالعه تکمیل شد!\n\n"
            f"📚 درس: {session['subject']}\n"
            f"🎯 مبحث: {session['topic']}\n"
            f"⏰ مدت: {format_time(session['minutes'])}\n"
            f"🏆 امتیاز: +{score}\n"
            f"📅 تاریخ: {date_str}\n"
            f"🕒 زمان: {time_str}\n\n"
            f"{rank_text}",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("📖 منابع این درس", callback_data=f"filesub_{session['subject']}"),
                InlineKeyboardButton("🏆 رتبه‌بندی", callback_data="rankings")
            ], [
                InlineKeyboardButton("➕ مطالعه جدید", callback_data="start_study"),
                InlineKeyboardButton("🏠 منوی اصلی", callback_data="main_menu")
            ]])
        )
    else:
        await query.edit_message_text(
            "❌ خطا در ثبت اطلاعات.",
            reply_markup=get_main_menu()
        )
    
    context.user_data.pop("current_session", None)

async def show_rankings(query, user_id: int) -> None:
    """نمایش رتبه‌بندی"""
    rankings = get_today_rankings()
    date_str, time_str = get_iran_time()
    
    if not rankings:
        text = f"🏆 جدول برترین‌ها\n\n📅 {date_str}\n🕒 {time_str}\n\n📭 هنوز کسی مطالعه نکرده است!"
    else:
        text = f"🏆 جدول برترین‌های امروز\n\n📅 {date_str}\n🕒 {time_str}\n\n"
        
        medals = ["🥇", "🥈", "🥉"]
        for i, rank in enumerate(rankings[:10]):
            if i < 3:
                medal = medals[i]
            else:
                medal = f"{i+1}."
            
            hours = rank["total_minutes"] // 60
            mins = rank["total_minutes"] % 60
            time_display = f"{hours}س {mins}د" if hours > 0 else f"{mins}د"
            
            user_display = f"{rank['username']} ({rank['grade']} {rank['field']})"
            if rank["user_id"] == user_id:
                user_display = f"**{user_display}** ←"
            
            text += f"{medal} {user_display}: {time_display}\n"
    
    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🔄 به‌روزرسانی", callback_data="rankings"),
            InlineKeyboardButton("🏠 منوی اصلی", callback_data="main_menu")
        ]]),
        parse_mode=ParseMode.MARKDOWN
    )

async def show_files_menu(query, user_id: int) -> None:
    """نمایش منوی منابع"""
    user_files = get_user_files(user_id)
    
    if not user_files:
        await query.edit_message_text(
            "📭 فایلی برای شما موجود نیست.\n"
            "ادمین به زودی فایل‌های مرتبط را اضافه می‌کند.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🏠 منوی اصلی", callback_data="main_menu")
            ]])
        )
        return
    
    await query.edit_message_text(
        "📚 منابع آموزشی شما\n\n"
        "لطفا درس مورد نظر را انتخاب کنید:",
        reply_markup=get_file_subjects_keyboard(user_id)
    )

async def show_subject_files(query, user_id: int, subject: str) -> None:
    """نمایش فایل‌های یک درس خاص"""
    files = get_files_by_subject(user_id, subject)
    
    if not files:
        await query.edit_message_text(
            f"📭 فایلی برای درس {subject} موجود نیست.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 بازگشت", callback_data="files"),
                InlineKeyboardButton("🏠 منوی اصلی", callback_data="main_menu")
            ]])
        )
        return
    
    text = f"📚 منابع {subject}\n\n"
    
    for i, file in enumerate(files[:5], 1):
        text += f"{i}. **{file['topic']}**\n"
        if file['description']:
            text += f"   📝 {file['description'][:50]}"
            if len(file['description']) > 50:
                text += "..."
            text += "\n"
        
        size_mb = file['file_size'] / (1024 * 1024)
        text += f"   📦 {size_mb:.1f} MB | 📥 {file['download_count']} بار\n\n"
    
    if len(files) > 5:
        text += f"📊 و {len(files)-5} فایل دیگر...\n"
    
    keyboard = []
    for file in files[:3]:  # حداکثر 3 فایل اول
        button_text = f"⬇️ {file['topic'][:15]}"
        if len(file['topic']) > 15:
            button_text += "..."
        
        keyboard.append([
            InlineKeyboardButton(
                button_text,
                callback_data=f"download_{file['file_id']}"
            )
        ])
    
    keyboard.append([
        InlineKeyboardButton("🔙 بازگشت", callback_data="files"),
        InlineKeyboardButton("🏠 منوی اصلی", callback_data="main_menu")
    ])
    
    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.MARKDOWN
    )

async def download_file(query, file_id: int, user_id: int, context: ContextTypes.DEFAULT_TYPE) -> None:
    """ارسال فایل به کاربر"""
    file_data = get_file_by_id(file_id)
    
    if not file_data:
        await query.answer("❌ فایل یافت نشد.", show_alert=True)
        return
    
    # بررسی دسترسی کاربر به فایل
    user_info = get_user_info(user_id)
    if not user_info:
        await query.answer("❌ دسترسی denied.", show_alert=True)
        return
    
    if user_info["grade"] != file_data["grade"] or user_info["field"] != file_data["field"]:
        await query.answer("❌ شما به این فایل دسترسی ندارید.", show_alert=True)
        return
    
    try:
        # ارسال فایل
        await context.bot.send_document(
            chat_id=query.message.chat_id,
            document=file_data["telegram_file_id"],
            caption=(
                f"📄 **{file_data['file_name']}**\n\n"
                f"📚 درس: {file_data['subject']}\n"
                f"🎯 مبحث: {file_data['topic']}\n"
                f"📦 حجم: {file_data['file_size'] // 1024} KB\n"
                f"📅 تاریخ آپلود: {file_data['upload_date']}\n\n"
                f"✅ با موفقیت دانلود شد!"
            ),
            parse_mode=ParseMode.MARKDOWN
        )
        
        # افزایش شمارنده دانلود
        increment_download_count(file_id)
        
        await query.answer("✅ فایل ارسال شد!")
        
    except Exception as e:
        logger.error(f"خطا در ارسال فایل: {e}")
        await query.answer("❌ خطا در ارسال فایل.", show_alert=True)

async def show_admin_panel(query) -> None:
    """نمایش پنل ادمین"""
    await query.edit_message_text(
        "👨‍💼 پنل مدیریت\n"
        "لطفا یک عملیات انتخاب کنید:",
        reply_markup=get_admin_keyboard()
    )

async def show_admin_upload(query) -> None:
    """نمایش راهنمای آپلود فایل برای ادمین"""
    await query.edit_message_text(
        "📤 آپلود فایل\n\n"
        "روش‌های آپلود:\n\n"
        "۱. دستوری سریع:\n"
        "/addfile <پایه> <رشته> <درس> <مبحث>\n\n"
        "مثال:\n"
        "/addfile دوازدهم تجربی فیزیک دینامیک\n\n"
        "۲. مرحله‌ای:\n"
        "از منوی زیر استفاده کنید\n\n"
        "📎 پس از وارد کردن اطلاعات، فایل را ارسال کنید.",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("📝 شروع آپلود مرحله‌ای", callback_data="start_upload_wizard")
        ], [
            InlineKeyboardButton("🔙 بازگشت", callback_data="admin_panel")
        ]])
    )

async def show_admin_requests(query) -> None:
    """نمایش درخواست‌های ثبت‌نام"""
    requests = get_pending_requests()
    
    if not requests:
        text = "📭 هیچ درخواست ثبت‌نامی در انتظار نیست."
    else:
        text = f"📋 درخواست‌های در انتظار: {len(requests)}\n\n"
        for req in requests[:5]:
            # امن کردن username برای مارکداون
            safe_username = "نامشخص"
            if req['username']:
                # فرار کردن کاراکترهای خطرناک مارکداون
                safe_username = req['username'].replace('_', '\\_') \
                                                 .replace('*', '\\*') \
                                                 .replace('[', '\\[') \
                                                 .replace(']', '\\]') \
                                                 .replace('`', '\\`')
            
            user_id = req['user_id']
            grade = req['grade'] or "نامشخص"
            field = req['field'] or "نامشخص"
            created_at = req['created_at']
            
            if isinstance(created_at, datetime):
                date_str = created_at.strftime('%Y/%m/%d %H:%M')
            else:
                date_str = str(created_at)
            
            text += f"👤 *{safe_username}*\n"
            text += f"🆔 آیدی: `{user_id}`\n"
            text += f"🎓 {grade} | 🧪 {field}\n"
            text += f"📅 {date_str}\n\n"
    
    await query.edit_message_text(
        text,
        reply_markup=get_pending_requests_keyboard(),
        parse_mode=ParseMode.MARKDOWN_V2  # بهتر است از MARKDOWN_V2 استفاده کنید
    )

async def show_request_details(query, request_id: int) -> None:
    """نمایش جزئیات یک درخواست"""
    requests = get_pending_requests()
    request = next((r for r in requests if r["request_id"] == request_id), None)
    
    if not request:
        await query.answer("❌ درخواست یافت نشد.", show_alert=True)
        return
    
    text = (
        f"📋 جزئیات درخواست #{request_id}\n\n"
        f"👤 کاربر: **{request['username']}**\n"
        f"🆔 آیدی: `{request['user_id']}`\n"
        f"🎓 پایه: {request['grade']}\n"
        f"🧪 رشته: {request['field']}\n"
        f"📅 تاریخ درخواست: {request['created_at'].strftime('%Y/%m/%d %H:%M')}\n\n"
        f"📝 پیام کاربر:\n"
        f"_{request['message']}_\n\n"
        f"لطفا تصمیم بگیرید:"
    )
    
    await query.edit_message_text(
        text,
        reply_markup=get_request_action_keyboard(request_id),
        parse_mode=ParseMode.MARKDOWN
    )

async def approve_request(query, request_id: int, admin_id: int, context: ContextTypes.DEFAULT_TYPE) -> None:
    """تأیید درخواست ثبت‌نام"""
    if approve_registration(request_id, f"تأیید توسط ادمین {admin_id}"):
        # اطلاع به کاربر
        query_data = """
        SELECT user_id FROM registration_requests WHERE request_id = %s
        """
        result = db.execute_query(query_data, (request_id,), fetch=True)
        
        if result:
            target_user_id = result[0]
            try:
                await context.bot.send_message(
                    target_user_id,
                    "🎉 **درخواست شما تأیید شد!**\n\n"
                    "✅ اکنون می‌توانید از ربات استفاده کنید.\n"
                    "برای شروع /start را بزنید."
                )
            except Exception as e:
                logger.error(f"خطا در اطلاع به کاربر: {e}")
        
        await query.edit_message_text(
            f"✅ درخواست #{request_id} تأیید شد.\n"
            f"کاربر می‌تواند از ربات استفاده کند.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("📋 مشاهده درخواست‌ها", callback_data="admin_requests"),
                InlineKeyboardButton("🏠 منوی اصلی", callback_data="main_menu")
            ]])
        )
    else:
        await query.answer("❌ خطا در تأیید درخواست.", show_alert=True)

async def reject_request(query, request_id: int, context: ContextTypes.DEFAULT_TYPE) -> None:
    """رد درخواست ثبت‌نام"""
    await query.message.reply_text(
        f"📝 لطفا دلیل رد درخواست #{request_id} را وارد کنید:"
    )
    
    context.user_data["rejecting_request"] = request_id
    await query.answer()

async def show_admin_manage_files(query) -> None:
    """مدیریت فایل‌ها"""
    files = get_all_files()
    
    if not files:
        text = "📭 هیچ فایلی در سیستم وجود ندارد."
    else:
        text = f"📁 مدیریت فایل‌ها\n\nتعداد کل: {len(files)}\n\n"
        for file in files[:10]:
            text += f"📄 **{file['file_name']}**\n"
            text += f"🆔 کد: FD-{file['file_id']}\n"
            text += f"🎓 {file['grade']} | 🧪 {file['field']}\n"
            text += f"📚 {file['subject']} - {file['topic'][:30]}\n"
            text += f"📥 {file['download_count']} دانلود | 📅 {file['upload_date']}\n\n"
    
    keyboard = []
    for file in files[:3]:
        keyboard.append([
            InlineKeyboardButton(
                f"🗑 حذف {file['file_name'][:15]}...",
                callback_data=f"delete_file_{file['file_id']}"
            )
        ])
    
    keyboard.append([
        InlineKeyboardButton("🔄 به‌روزرسانی", callback_data="admin_manage_files"),
        InlineKeyboardButton("🔙 بازگشت", callback_data="admin_panel")
    ])
    
    await query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.MARKDOWN
    )

async def delete_file_process(query, file_id: int, context: ContextTypes.DEFAULT_TYPE) -> None:
    """حذف فایل"""
    file_data = get_file_by_id(file_id)
    
    if not file_data:
        await query.answer("❌ فایل یافت نشد.", show_alert=True)
        return
    
    if delete_file(file_id):
        await query.edit_message_text(
            f"✅ فایل حذف شد:\n\n"
            f"📄 نام: {file_data['file_name']}\n"
            f"🎓 پایه: {file_data['grade']}\n"
            f"🧪 رشته: {file_data['field']}\n"
            f"📚 درس: {file_data['subject']}",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("📁 بازگشت به مدیریت", callback_data="admin_manage_files")
            ]])
        )
    else:
        await query.answer("❌ خطا در حذف فایل.", show_alert=True)

async def show_admin_stats(query) -> None:
    """نمایش آمار ربات"""
    try:
        # آمار کاربران
        query_users = """
        SELECT 
            COUNT(*) as total_users,
            COUNT(CASE WHEN is_active THEN 1 END) as active_users,
            COALESCE(SUM(total_study_time), 0) as total_study_minutes
        FROM users
        """
        user_stats = db.execute_query(query_users, fetch=True)
        
        # آمار جلسات
        query_sessions = """
        SELECT 
            COUNT(*) as total_sessions,
            COUNT(CASE WHEN completed THEN 1 END) as completed_sessions,
            COALESCE(SUM(minutes), 0) as total_session_minutes
        FROM study_sessions
        """
        session_stats = db.execute_query(query_sessions, fetch=True)
        
        # آمار فایل‌ها
        query_files = """
        SELECT 
            COUNT(*) as total_files,
            COALESCE(SUM(download_count), 0) as total_downloads,
            COUNT(DISTINCT subject) as unique_subjects
        FROM files
        """
        file_stats = db.execute_query(query_files, fetch=True)
        
        # آمار امروز
        date_str, _ = get_iran_time()
        query_today = """
        SELECT 
            COUNT(DISTINCT user_id) as active_today,
            COALESCE(SUM(total_minutes), 0) as minutes_today
        FROM daily_rankings
        WHERE date = %s
        """
        today_stats = db.execute_query(query_today, (date_str,), fetch=True)
        
        text = f"📊 **آمار کامل ربات**\n\n"
        text += f"📅 تاریخ: {date_str}\n\n"
        
        text += f"👥 **کاربران:**\n"
        text += f"• کل کاربران: {user_stats[0]}\n"
        text += f"• کاربران فعال: {user_stats[1]}\n"
        text += f"• مجموع دقیقه مطالعه: {user_stats[2]:,}\n\n"
        
        text += f"⏰ **جلسات مطالعه:**\n"
        text += f"• کل جلسات: {session_stats[0]}\n"
        text += f"• جلسات تکمیل‌شده: {session_stats[1]}\n"
        text += f"• مجموع زمان: {session_stats[2]:,} دقیقه\n\n"
        
        text += f"📁 **فایل‌ها:**\n"
        text += f"• کل فایل‌ها: {file_stats[0]}\n"
        text += f"• کل دانلودها: {file_stats[1]:,}\n"
        text += f"• درس‌های منحصربه‌فرد: {file_stats[2]}\n\n"
        
        text += f"🎯 **امروز:**\n"
        text += f"• کاربران فعال: {today_stats[0] if today_stats else 0}\n"
        text += f"• مجموع زمان: {today_stats[1] if today_stats else 0} دقیقه\n"
        
        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔄 به‌روزرسانی", callback_data="admin_stats"),
                InlineKeyboardButton("🔙 بازگشت", callback_data="admin_panel")
            ]]),
            parse_mode=ParseMode.MARKDOWN
        )
        
    except Exception as e:
        logger.error(f"خطا در دریافت آمار: {e}")
        await query.edit_message_text(
            "❌ خطا در دریافت آمار.",
            reply_markup=get_admin_keyboard()
        )

# -----------------------------------------------------------
# توابع زمان‌بندی شده
# -----------------------------------------------------------

async def auto_complete_study(context) -> None:
    """اتمام خودکار جلسه مطالعه بعد از اتمام زمان"""
    job_data = context.job.data
    session_id = job_data["session_id"]
    chat_id = job_data["chat_id"]
    user_id = job_data["user_id"]
    
    session = complete_study_session(session_id)
    
    if session:
        date_str, time_str = get_iran_time()
        score = calculate_score(session["minutes"])
        
        await context.bot.send_message(
            chat_id,
            f"⏰ **زمان به پایان رسید!**\n\n"
            f"✅ مطالعه به صورت خودکار ثبت شد.\n\n"
            f"📚 درس: {session['subject']}\n"
            f"🎯 مبحث: {session['topic']}\n"
            f"⏰ مدت: {format_time(session['minutes'])}\n"
            f"🏆 امتیاز: +{score}\n"
            f"📅 تاریخ: {date_str}\n"
            f"🕒 زمان: {time_str}\n\n"
            f"🎉 آفرین! یک جلسه مفید داشتید.",
            reply_markup=get_main_menu()
        )
    else:
        await context.bot.send_message(
            chat_id,
            "❌ خطا در ثبت خودکار جلسه.",
            reply_markup=get_main_menu()
        )

# -----------------------------------------------------------
# تابع اصلی
# -----------------------------------------------------------

def main() -> None:
    """تابع اصلی اجرای ربات"""
    # ایجاد برنامه
    application = Application.builder().token(TOKEN).build()
    
    # ثبت هندلرهای دستورات
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("admin", admin_command))
    application.add_handler(CommandHandler("active", active_command))
    application.add_handler(CommandHandler("deactive", deactive_command))
    application.add_handler(CommandHandler("addfile", addfile_command))
    application.add_handler(CommandHandler("skip", skip_command))
    
    # ثبت هندلرهای پیام
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    application.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    
    # ثبت هندلرهای کال‌بک
    application.add_handler(CallbackQueryHandler(handle_callback))
    
    # راه‌اندازی ربات
    logger.info("✅ ربات در حال راه‌اندازی...")
    print("=" * 50)
    print("🤖 ربات Focus Todo راه‌اندازی شد!")
    print(f"👨‍💼 ادمین‌ها: {ADMIN_IDS}")
    print(f"⏰ محدودیت زمان مطالعه: {MAX_STUDY_TIME} دقیقه")
    print(f"🗄️ دیتابیس: PostgreSQL")
    print("=" * 50)
    
    application.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True
    )

if __name__ == '__main__':
    main()
