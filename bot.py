# ==================== bot.py - بخش 1/3 ====================
# ربات کامل مطالعه هوشمند - نسخه نهایی

import asyncio
import json
import logging
import os
import re
import time
from datetime import datetime, timedelta, date
from typing import Dict, List, Optional, Tuple, Any
from enum import Enum

import psycopg2
from psycopg2 import pool, sql
import jdatetime
import pytz
import httpx
from dotenv import load_dotenv

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove,
    InputMediaPhoto
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters, JobQueue
)
from telegram.constants import ParseMode
from telegram.error import TelegramError

from openai import AsyncOpenAI

# ==================== بارگذاری تنظیمات ====================
load_dotenv()

TOKEN = os.getenv("BOT_TOKEN")
AI_API_KEY = os.getenv("AI_API_KEY")
AI_BASE_URL = os.getenv("AI_BASE_URL")
AI_MODEL = os.getenv("AI_MODEL")

DB_CONFIG = {
    "host": os.getenv("DB_HOST", "localhost"),
    "database": os.getenv("DB_NAME", "study_bot_db"),
    "user": os.getenv("DB_USER", "postgres"),
    "password": os.getenv("DB_PASSWORD"),
    "port": os.getenv("DB_PORT", "5432")
}

ADMIN_IDS = [int(id.strip()) for id in os.getenv("ADMIN_IDS", "").split(",") if id.strip()]

IRAN_TZ = pytz.timezone('Asia/Tehran')

GRADE_RULES = {
    1: {"name": "آسان", "duration": 20, "emoji": "⭐"},
    2: {"name": "نسبتاً آسان", "duration": 30, "emoji": "⭐⭐"},
    3: {"name": "متوسط", "duration": 45, "emoji": "⭐⭐⭐"},
    4: {"name": "نسبتاً سخت", "duration": 60, "emoji": "⭐⭐⭐⭐"},
    5: {"name": "سخت", "duration": 75, "emoji": "⭐⭐⭐⭐⭐"},
}

PLAN_LEVELS = {
    0: {"name": "اولیه", "days": 1, "emoji": "🌱"},
    1: {"name": "روزانه", "days": 2, "emoji": "📈"},
    2: {"name": "شخصی‌سازی‌شده", "days": 8, "emoji": "🎯"},
    3: {"name": "شناور", "days": 15, "emoji": "🚀"}
}

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

client = AsyncOpenAI(
    base_url=AI_BASE_URL,
    api_key=AI_API_KEY,
    timeout=httpx.Timeout(60.0, connect=15.0)
)

# ==================== دیتابیس ====================
db_pool = None

def init_db_pool():
    global db_pool
    try:
        db_pool = psycopg2.pool.SimpleConnectionPool(
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

def get_connection():
    return db_pool.getconn()

def return_connection(conn):
    db_pool.putconn(conn)

def execute_query(query, params=None, fetch=False, fetchall=False, commit=True):
    conn = None
    cursor = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(query, params or ())
        if fetch:
            result = cursor.fetchone()
            if commit:
                conn.commit()
            return result
        elif fetchall:
            result = cursor.fetchall()
            if commit:
                conn.commit()
            return result
        else:
            if commit:
                conn.commit()
            return cursor.rowcount
    except Exception as e:
        logger.error(f"❌ خطا در اجرای کوئری: {e}")
        if conn:
            conn.rollback()
        raise
    finally:
        if cursor:
            cursor.close()
        if conn:
            return_connection(conn)

# ==================== توابع کمکی ====================
def get_iran_now() -> datetime:
    return datetime.now(IRAN_TZ)

def get_today_date() -> str:
    return get_iran_now().strftime("%Y-%m-%d")

def get_today_shamsi() -> str:
    now = get_iran_now()
    jdate = jdatetime.datetime.fromgregorian(datetime=now)
    return jdate.strftime("%Y/%m/%d")

def get_iran_time_str() -> str:
    return get_iran_now().strftime("%H:%M")

def get_shamsi_date(date_str: str) -> str:
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        dt = IRAN_TZ.localize(dt)
        jdate = jdatetime.datetime.fromgregorian(datetime=dt)
        return jdate.strftime("%Y/%m/%d")
    except:
        return date_str

def format_time_hours_minutes(minutes: int) -> str:
    if minutes < 60:
        return f"{minutes} دقیقه"
    hours = minutes // 60
    mins = minutes % 60
    if mins == 0:
        return f"{hours} ساعت"
    return f"{hours} ساعت و {mins} دقیقه"

def convert_persian_to_int(text: str) -> int:
    persian_to_english = {
        '۰': '0', '۱': '1', '۲': '2', '۳': '3', '۴': '4',
        '۵': '5', '۶': '6', '۷': '7', '۸': '8', '۹': '9'
    }
    result = text
    for persian, english in persian_to_english.items():
        result = result.replace(persian, english)
    try:
        return int(result)
    except:
        return None

def time_to_minutes(time_str: str) -> int:
    try:
        parts = time_str.split(":")
        return int(parts[0]) * 60 + int(parts[1])
    except:
        return 0

def minutes_to_time(minutes: int) -> str:
    hours = minutes // 60
    mins = minutes % 60
    return f"{hours:02d}:{mins:02d}"

def parse_time_slot(time_str: str) -> Tuple[Optional[str], Optional[str]]:
    try:
        time_str = time_str.strip().replace(" ", "")
        for p, e in {'۰': '0', '۱': '1', '۲': '2', '۳': '3', '۴': '4',
                     '۵': '5', '۶': '6', '۷': '7', '۸': '8', '۹': '9'}.items():
            time_str = time_str.replace(p, e)
        
        parts = time_str.split("-")
        if len(parts) != 2:
            return None, None
        
        start = parts[0].strip()
        end = parts[1].strip()
        
        if ":" not in start:
            start = f"{int(start):02d}:00"
        if ":" not in end:
            end = f"{int(end):02d}:00"
        
        start_h, start_m = map(int, start.split(":"))
        end_h, end_m = map(int, end.split(":"))
        
        if not (0 <= start_h <= 23 and 0 <= end_h <= 23):
            return None, None
        if not (0 <= start_m <= 59 and 0 <= end_m <= 59):
            return None, None
        
        return start, end
    except:
        return None, None

# ==================== ایجاد جداول ====================
def create_tables():
    queries = [
        """
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            telegram_id VARCHAR(50) UNIQUE NOT NULL,
            username VARCHAR(100),
            full_name VARCHAR(200),
            goal VARCHAR(50),
            grade VARCHAR(50),
            field VARCHAR(50),
            exam_date DATE,
            study_hours_per_week INTEGER,
            peak_time VARCHAR(20),
            learning_style VARCHAR(30),
            focus_duration INTEGER DEFAULT 45,
            break_duration INTEGER DEFAULT 10,
            weak_subjects JSONB,
            strong_subjects JSONB,
            daily_schedule JSONB,
            is_active BOOLEAN DEFAULT TRUE,
            is_onboarded BOOLEAN DEFAULT FALSE,
            current_phase INTEGER DEFAULT 0,
            plan_level INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_activity_date DATE,
            version INTEGER DEFAULT 1
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS subject_status (
            id SERIAL PRIMARY KEY,
            user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
            subject VARCHAR(50) NOT NULL,
            level VARCHAR(20),
            mastery_score FLOAT DEFAULT 0,
            total_sessions INTEGER DEFAULT 0,
            completed_sessions INTEGER DEFAULT 0,
            total_study_minutes INTEGER DEFAULT 0,
            avg_score FLOAT,
            best_score FLOAT,
            worst_score FLOAT,
            current_chapter INTEGER,
            current_topic VARCHAR(200),
            completed_chapters JSONB,
            completed_topics JSONB,
            weak_chapters JSONB,
            weak_topics JSONB,
            strong_topics JSONB,
            progress FLOAT DEFAULT 0,
            improvement_rate FLOAT DEFAULT 0,
            avg_session_duration INTEGER,
            best_time VARCHAR(20),
            last_studied DATE,
            last_score FLOAT,
            version INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, subject)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS activity_log (
            id SERIAL PRIMARY KEY,
            user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
            date DATE NOT NULL,
            session_id INTEGER,
            subject VARCHAR(50) NOT NULL,
            topic VARCHAR(200),
            activity_type VARCHAR(30),
            planned_duration INTEGER,
            actual_duration INTEGER,
            start_time TIME,
            end_time TIME,
            score FLOAT,
            status VARCHAR(20),
            difficulty VARCHAR(20),
            focus_rating INTEGER,
            energy_level INTEGER,
            mood VARCHAR(20),
            distractions JSONB,
            notes TEXT,
            break_duration INTEGER,
            break_time TIME,
            pages_count INTEGER,
            test_count INTEGER,
            correct_count INTEGER,
            part_order INTEGER DEFAULT 0,
            version INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            is_archived BOOLEAN DEFAULT FALSE
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS advisory_rules (
            id SERIAL PRIMARY KEY,
            topic VARCHAR(50) NOT NULL,
            label VARCHAR(50),
            condition TEXT,
            advice TEXT NOT NULL,
            priority INTEGER DEFAULT 5,
            time VARCHAR(20),
            frequency VARCHAR(30),
            days JSONB,
            applicable_for JSONB,
            subjects JSONB,
            is_active BOOLEAN DEFAULT TRUE,
            is_system_generated BOOLEAN DEFAULT FALSE,
            usage_count INTEGER DEFAULT 0,
            success_rate FLOAT DEFAULT 0,
            last_used TIMESTAMP,
            created_by BIGINT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            version INTEGER DEFAULT 1
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS study_sessions (
            session_id SERIAL PRIMARY KEY,
            user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
            date VARCHAR(20),
            total_parts INT,
            completed_parts INT DEFAULT 0,
            edit_count INT DEFAULT 0,
            max_edits INT DEFAULT 2,
            confirmed BOOLEAN DEFAULT FALSE,
            is_finished BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            time_slots TEXT,
            topics TEXT,
            archived BOOLEAN DEFAULT FALSE,
            plan_level INT DEFAULT 0,
            UNIQUE(user_id, date)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS study_parts (
            part_id SERIAL PRIMARY KEY,
            session_id INT REFERENCES study_sessions(session_id),
            part_number INT,
            title VARCHAR(200),
            grade INT,
            planned_minutes INT,
            actual_minutes INT DEFAULT 0,
            time_slot VARCHAR(50),
            completed BOOLEAN DEFAULT FALSE,
            is_hardest BOOLEAN DEFAULT FALSE,
            is_easiest BOOLEAN DEFAULT FALSE,
            started_at TIMESTAMP,
            completed_at TIMESTAMP,
            pages INT DEFAULT 0,
            planned_start_time TIME,
            planned_end_time TIME,
            actual_start_time TIMESTAMP,
            actual_end_time TIMESTAMP,
            is_fixed_time BOOLEAN DEFAULT FALSE,
            delay_minutes INT DEFAULT 0,
            alert_sent BOOLEAN DEFAULT FALSE,
            reason TEXT
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS user_insights (
            id SERIAL PRIMARY KEY,
            user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
            analysis_date DATE,
            best_time VARCHAR(20),
            weakest_subject VARCHAR(50),
            strongest_subject VARCHAR(50),
            avg_daily_hours FLOAT,
            completion_rate FLOAT,
            time_patterns JSONB,
            performance_patterns JSONB,
            quality_patterns JSONB,
            burnout_risk VARCHAR(20),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, analysis_date)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS daily_alerts (
            id SERIAL PRIMARY KEY,
            user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
            part_id INTEGER REFERENCES study_parts(part_id) ON DELETE CASCADE,
            alert_time TIMESTAMP,
            message TEXT,
            sent BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS personalized_plans (
            id SERIAL PRIMARY KEY,
            user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
            date DATE NOT NULL,
            daily_plan JSONB NOT NULL,
            reasoning JSONB,
            expected_outcome JSONB,
            applied_advice_ids JSONB,
            is_active BOOLEAN DEFAULT TRUE,
            was_completed BOOLEAN DEFAULT FALSE,
            completion_report JSONB,
            version INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, date)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS chat_messages (
            id SERIAL PRIMARY KEY,
            user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
            role VARCHAR(20) NOT NULL,
            content TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS user_quota (
            user_id INTEGER REFERENCES users(id) ON DELETE CASCADE PRIMARY KEY,
            daily_messages INTEGER DEFAULT 0,
            last_reset DATE DEFAULT CURRENT_DATE,
            plan_type VARCHAR(20) DEFAULT 'trial',
            plan_expiry DATE,
            UNIQUE(user_id)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS change_history (
            id SERIAL PRIMARY KEY,
            user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
            session_id INTEGER,
            part_id INTEGER,
            action_type VARCHAR(50),
            previous_data JSONB,
            new_data JSONB,
            extra_data JSONB,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            is_reverted BOOLEAN DEFAULT FALSE
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS pending_payments (
            id SERIAL PRIMARY KEY,
            user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
            photo_file_id VARCHAR(200),
            caption TEXT,
            status VARCHAR(20) DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    ]
    
    for query in queries:
        try:
            execute_query(query)
        except Exception as e:
            logger.warning(f"خطا در ایجاد جدول: {e}")
    
    # ============================================
    # Migrations - حذف Foreign Key های مسئله‌دار
    # ============================================
    migrations = [
        "ALTER TABLE change_history DROP CONSTRAINT IF EXISTS change_history_part_id_fkey",
        "ALTER TABLE change_history DROP CONSTRAINT IF EXISTS change_history_session_id_fkey",
    ]
    for migration in migrations:
        try:
            execute_query(migration)
        except Exception as e:
            logger.warning(f"خطا در migration: {e}")
    
    indexes = [
        "CREATE INDEX IF NOT EXISTS idx_users_telegram ON users(telegram_id)",
        "CREATE INDEX IF NOT EXISTS idx_subject_user ON subject_status(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_activity_user ON activity_log(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_activity_date ON activity_log(date)",
        "CREATE INDEX IF NOT EXISTS idx_sessions_user ON study_sessions(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_parts_session ON study_parts(session_id)",
        "CREATE INDEX IF NOT EXISTS idx_chat_user ON chat_messages(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_change_user ON change_history(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_payments_user ON pending_payments(user_id)"
    ]
    
    for idx in indexes:
        try:
            execute_query(idx)
        except:
            pass
    
    logger.info("✅ جداول دیتابیس ایجاد شدند")

# ==================== کیبوردها ====================
def get_main_keyboard() -> ReplyKeyboardMarkup:
    keyboard = [
        ["📝 برنامه امروز", "💬 چت با AI"],
        ["📊 گزارش", "📅 تقویم"],
        ["💰 خرید اشتراک", "👤 پروفایل"],
        ["🔙 برگشت به حالت قبل"]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def get_plan_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup([
        ["✏️ ویرایش ترتیب", "➕ اضافه کردن"],
        ["🔄 بازنشانی", "🔙 بازگشت"]
    ], resize_keyboard=True)

def get_part_buttons_initial(parts: List[Dict]) -> ReplyKeyboardMarkup:
    keyboard = []
    for part in parts:
        status = "⬜" if not part.get("completed") else "✅"
        grade_emoji = GRADE_RULES.get(part.get("grade", 3), GRADE_RULES[3])["emoji"]
        title = part.get("title", "بدون عنوان")
        planned_start = part.get("planned_start_time") or part.get("planned_start") or ""
        planned_end = part.get("planned_end_time") or part.get("planned_end") or ""
        time_info = ""
        if planned_start and planned_end:
            time_info = f" {planned_start}-{planned_end}"
        elif part.get("time_slot"):
            time_info = f" {part['time_slot']}"
        text = f"{status} {grade_emoji} {title} ({part.get('planned_minutes', 0)}د){time_info} ↕️ [{part.get('part_id', 0)}]"
        keyboard.append([text])
    keyboard.append(["✅ تایید برنامه"])
    keyboard.append(["🔙 بازگشت"])
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def get_part_buttons_final(parts: List[Dict], show_date: bool = False) -> ReplyKeyboardMarkup:
    keyboard = []
    if not parts:
        keyboard.append(["🔙 بازگشت"])
        return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    sorted_parts = sorted(parts, key=lambda x: x.get("part_number", 0))
    for part in sorted_parts:
        part_id = part.get("part_id")
        if not part_id:
            continue
        status = "✅" if part.get("completed", False) else "⬜"
        grade_emoji = GRADE_RULES.get(part.get("grade", 3), GRADE_RULES[3])["emoji"]
        title = part.get("title", "بدون عنوان")
        planned_start = part.get("planned_start_time") or part.get("planned_start") or ""
        planned_end = part.get("planned_end_time") or part.get("planned_end") or ""
        time_info = ""
        if planned_start and planned_end:
            time_info = f" {planned_start}-{planned_end}"
        elif part.get("time_slot"):
            time_info = f" {part['time_slot']}"
        fixed_tag = " 🔒" if part.get("is_fixed_time", False) else ""
        text = f"{status} {grade_emoji} {title} ({part.get('planned_minutes', 0)}د){time_info}{fixed_tag} [{part_id}]"
        keyboard.append([text])
    keyboard.append(["➕ اضافه کردن فعالیت"])
    keyboard.append(["✏️ ویرایش برنامه"])
    keyboard.append(["✅ اتمام برنامه"])
    keyboard.append(["🔙 برگشت به حالت قبل", "🔙 بازگشت"])
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def get_part_detail_buttons(part_id: int, is_timer_running: bool = False, elapsed_seconds: int = 0) -> ReplyKeyboardMarkup:
    keyboard = []
    if is_timer_running:
        keyboard.append(["⏹ توقف", "✅ تکمیل"])
    else:
        if elapsed_seconds > 0:
            keyboard.append(["▶️ ادامه تایمر", "✅ تکمیل"])
        else:
            keyboard.append(["⏱ تایمر", "✅ تکمیل"])
    keyboard.append(["🗑 حذف پارت"])
    keyboard.append(["🔙 بازگشت"])
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def get_edit_menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup([
        ["✏️ ویرایش دستی"],
        ["✏️ ویرایش آزاد (چت با AI)"],
        ["🔙 بازگشت"]
    ], resize_keyboard=True)

def get_calendar_keyboard(dates: List[str]) -> ReplyKeyboardMarkup:
    keyboard = []
    for date_str in dates:
        shamsi = get_shamsi_date(date_str)
        keyboard.append([f"📅 {shamsi}"])
    keyboard.append(["🔙 بازگشت"])
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def get_add_activity_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup([
        ["📖 مطالعه", "📝 تست"],
        ["📚 خلاصه‌نویسی", "🔁 مرور"],
        ["🔙 بازگشت"]
    ], resize_keyboard=True)

def get_duration_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup([
        ["⏱ ۲۰ دقیقه", "⏱ ۳۰ دقیقه"],
        ["⏱ ۴۵ دقیقه", "⏱ ۶۰ دقیقه"],
        ["✏️ دلخواه"]
    ], resize_keyboard=True)

def get_ai_chat_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup([
        ["🔄 مکالمه جدید", "🔙 بازگشت به منو"],
        ["📊 مصرف امروز"]
    ], resize_keyboard=True)

def get_build_plan_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup([
        ["🧠 ساخت با AI", "✏️ ساخت دستی"],
        ["🔙 بازگشت"]
    ], resize_keyboard=True)

def get_confirm_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup([
        ["✅ تایید تغییرات", "❌ لغو تغییرات"],
        ["🔙 بازگشت"]
    ], resize_keyboard=True)

def get_confirm_change_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup([
        ["✅ تایید تغییر", "❌ رد تغییر"],
        ["🔙 بازگشت"]
    ], resize_keyboard=True)

def get_confirm_clear_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup([
        ["🗑 بله، همه را پاک کن"],
        ["❌ نه، لغو"]
    ], resize_keyboard=True)

def get_confirm_delete_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup([
        ["✅ بله، حذف کن"],
        ["❌ نه، لغو"]
    ], resize_keyboard=True)

# ==================== توابع کاربری ====================
def get_user_id_by_telegram(telegram_id: int) -> Optional[int]:
    result = execute_query(
        "SELECT id FROM users WHERE telegram_id = %s",
        (str(telegram_id),),
        fetch=True
    )
    return result[0] if result else None

def get_user_data(telegram_id: int) -> Optional[Dict]:
    result = execute_query(
        """SELECT id, telegram_id, username, full_name, goal, grade, field, 
                  is_onboarded, current_phase, weak_subjects, strong_subjects,
                  study_hours_per_week, peak_time, learning_style, focus_duration,
                  break_duration, plan_level, created_at, version
           FROM users WHERE telegram_id = %s""",
        (str(telegram_id),),
        fetch=True
    )
    if not result:
        return None
    return {
        "id": result[0], "telegram_id": result[1], "username": result[2],
        "full_name": result[3], "goal": result[4], "grade": result[5],
        "field": result[6], "is_onboarded": result[7], "current_phase": result[8],
        "weak_subjects": result[9] or [], "strong_subjects": result[10] or [],
        "study_hours_per_week": result[11], "peak_time": result[12],
        "learning_style": result[13], "focus_duration": result[14] or 45,
        "break_duration": result[15] or 10, "plan_level": result[16] or 0,
        "created_at": result[17], "version": result[18] or 1
    }

def get_plan_by_date(user_id: int, date_str: str) -> Optional[Dict]:
    query = """
    SELECT s.session_id, s.total_parts, s.completed_parts, s.edit_count, 
           s.max_edits, s.confirmed, s.time_slots, s.topics, s.is_finished, s.archived, s.plan_level
    FROM study_sessions s
    WHERE s.user_id = %s AND s.date = %s AND s.archived = FALSE
    ORDER BY s.created_at DESC LIMIT 1
    """
    result = execute_query(query, (user_id, date_str), fetch=True)
    if not result:
        return None
    
    session_id, total_parts, completed_parts, edit_count, max_edits, confirmed, time_slots, topics, is_finished, archived, plan_level = result
    
    query_parts = """
    SELECT part_id, part_number, title, grade, planned_minutes, actual_minutes,
           time_slot, completed, is_hardest, is_easiest, pages,
           to_char(started_at, 'HH24:MI'), to_char(completed_at, 'HH24:MI'),
           planned_start_time, planned_end_time,
           actual_start_time, actual_end_time, is_fixed_time, delay_minutes,
           reason, alert_sent
    FROM study_parts
    WHERE session_id = %s ORDER BY part_number
    """
    parts_result = execute_query(query_parts, (session_id,), fetchall=True)
    
    parts = []
    for row in parts_result:
        planned_start = row[13]
        planned_end = row[14]
        if planned_start and hasattr(planned_start, 'strftime'):
            planned_start = planned_start.strftime('%H:%M')
        if planned_end and hasattr(planned_end, 'strftime'):
            planned_end = planned_end.strftime('%H:%M')
        parts.append({
            "part_id": row[0], "part_number": row[1], "title": row[2],
            "grade": row[3], "planned_minutes": row[4], "actual_minutes": row[5] or 0,
            "time_slot": row[6] or "", "completed": row[7],
            "is_hardest": row[8], "is_easiest": row[9], "pages": row[10] or 0,
            "start_time": row[11] or "", "end_time": row[12] or "",
            "planned_start_time": planned_start or "", "planned_end_time": planned_end or "",
            "planned_start": planned_start or "", "planned_end": planned_end or "",
            "actual_start": row[15], "actual_end": row[16],
            "is_fixed_time": row[17] or False, "delay_minutes": row[18] or 0,
            "reason": row[19] or "", "alert_sent": row[20] or False
        })
    
    if isinstance(time_slots, str):
        try: time_slots = json.loads(time_slots)
        except: time_slots = []
    if isinstance(topics, str):
        try: topics = json.loads(topics)
        except: topics = []
    
    return {
        "session_id": session_id, "total_parts": total_parts,
        "completed_parts": completed_parts, "edit_count": edit_count,
        "max_edits": max_edits, "confirmed": confirmed,
        "time_slots": time_slots, "topics": topics, "parts": parts,
        "date": date_str, "is_finished": is_finished,
        "archived": archived, "plan_level": plan_level or 0
    }

def get_today_activities(user_id: int) -> List[Dict]:
    today = get_today_date()
    results = execute_query(
        """SELECT id, subject, topic, activity_type, planned_duration, actual_duration,
                  start_time, end_time, score, status, difficulty, focus_rating,
                  energy_level, mood, distractions, notes, pages_count, test_count,
                  correct_count, part_order, created_at
           FROM activity_log 
           WHERE user_id = %s AND date = %s AND is_archived = FALSE
           ORDER BY part_order ASC, created_at ASC""",
        (user_id, today), fetchall=True
    )
    return [{
        "id": r[0], "subject": r[1], "topic": r[2], "activity_type": r[3],
        "planned_duration": r[4] or 0, "actual_duration": r[5] or 0,
        "start_time": r[6], "end_time": r[7], "score": r[8],
        "status": r[9] or "pending", "difficulty": r[10], "focus_rating": r[11],
        "energy_level": r[12], "mood": r[13], "distractions": r[14] or [],
        "notes": r[15], "pages_count": r[16] or 0, "test_count": r[17] or 0,
        "correct_count": r[18] or 0, "part_order": r[19] or 0, "created_at": r[20]
    } for r in results] if results else []

def get_activities_by_date_range(user_id: int, start_date: str, end_date: str) -> List[Dict]:
    """دریافت فعالیت‌ها در بازه تاریخی مشخص"""
    results = execute_query(
        """SELECT id, subject, topic, activity_type, planned_duration, actual_duration,
                  date, score, status, part_order
           FROM activity_log 
           WHERE user_id = %s AND date BETWEEN %s AND %s AND is_archived = FALSE
           ORDER BY date DESC, part_order ASC""",
        (user_id, start_date, end_date), fetchall=True
    )
    return [{
        "id": r[0], "subject": r[1], "topic": r[2], "activity_type": r[3],
        "planned_duration": r[4] or 0, "actual_duration": r[5] or 0,
        "date": str(r[6]), "score": r[7], "status": r[8] or "pending",
        "part_order": r[9] or 0
    } for r in results] if results else []

def get_yesterday_activities(user_id: int) -> List[Dict]:
    """دریافت فعالیت‌های دیروز"""
    yesterday = (get_iran_now() - timedelta(days=1)).strftime("%Y-%m-%d")
    return get_activities_by_date_range(user_id, yesterday, yesterday)

def get_user_study_summary(user_id: int, days: int = 7) -> Dict:
    """خلاصه مطالعه کاربر در N روز اخیر"""
    end_date = get_today_date()
    start_date = (get_iran_now() - timedelta(days=days)).strftime("%Y-%m-%d")
    
    # آمار activity_log
    stats = execute_query(
        """SELECT 
               COUNT(*) as total_sessions,
               COUNT(CASE WHEN status = 'done' THEN 1 END) as completed,
               COALESCE(SUM(actual_duration), 0) as total_minutes,
               COALESCE(AVG(CASE WHEN score IS NOT NULL THEN score END), 0) as avg_score,
               COUNT(DISTINCT date) as active_days
           FROM activity_log 
           WHERE user_id = %s AND date BETWEEN %s AND %s AND is_archived = FALSE""",
        (user_id, start_date, end_date), fetch=True
    )
    
    # آمار روزانه برای نمایش
    daily = execute_query(
        """SELECT date, 
                  COUNT(*) as sessions,
                  COALESCE(SUM(actual_duration), 0) as minutes,
                  COUNT(CASE WHEN status = 'done' THEN 1 END) as done
           FROM activity_log 
           WHERE user_id = %s AND date BETWEEN %s AND %s AND is_archived = FALSE
           GROUP BY date ORDER BY date DESC""",
        (user_id, start_date, end_date), fetchall=True
    )
    
    # آمار دروس
    subjects = execute_query(
        """SELECT subject, 
                  COUNT(*) as sessions,
                  COALESCE(SUM(actual_duration), 0) as minutes,
                  COALESCE(AVG(CASE WHEN score IS NOT NULL THEN score END), 0) as avg_score
           FROM activity_log 
           WHERE user_id = %s AND date BETWEEN %s AND %s AND is_archived = FALSE
           GROUP BY subject ORDER BY minutes DESC""",
        (user_id, start_date, end_date), fetchall=True
    )
    
    return {
        "days": days,
        "start_date": start_date,
        "end_date": end_date,
        "total_sessions": stats[0] if stats else 0,
        "completed_sessions": stats[1] if stats else 0,
        "total_minutes": stats[2] if stats else 0,
        "avg_score": float(stats[3]) if stats and stats[3] else 0,
        "active_days": stats[4] if stats else 0,
        "daily": [{
            "date": str(d[0]), "sessions": d[1], "minutes": d[2], "done": d[3]
        } for d in daily] if daily else [],
        "subjects": [{
            "subject": s[0], "sessions": s[1], "minutes": s[2],
            "avg_score": float(s[3]) if s[3] else 0
        } for s in subjects] if subjects else []
    }

def get_recent_dates(user_id: int, days: int = 10) -> List[str]:
    results = execute_query(
        """SELECT DISTINCT date FROM study_sessions 
           WHERE user_id = %s ORDER BY date DESC LIMIT %s""",
        (user_id, days), fetchall=True
    )
    return [r[0] for r in results] if results else []

def get_active_advice(user_id: int) -> List[Dict]:
    """دریافت همه توصیه‌های فعال (بدون فیلتر)"""
    results = execute_query(
        """SELECT id, topic, label, condition, advice, priority, time, frequency,
                  days, subjects, usage_count, success_rate
           FROM advisory_rules WHERE is_active = TRUE 
           ORDER BY priority DESC""",
        fetchall=True
    )
    return [{
        "id": r[0], "topic": r[1], "label": r[2], "condition": r[3],
        "advice": r[4], "priority": r[5], "time": r[6], "frequency": r[7],
        "days": r[8] or [], "subjects": r[9] or [],
        "usage_count": r[10] or 0, "success_rate": r[11] or 0
    } for r in results] if results else []

def get_active_advice_for_user(user_id: int, user_data: Dict = None) -> List[Dict]:
    """دریافت توصیه‌های فعال و مرتبط با کاربر (فیلترشده)"""
    results = execute_query(
        """SELECT id, topic, label, condition, advice, priority, time, frequency,
                  days, subjects, usage_count, success_rate
           FROM advisory_rules 
           WHERE is_active = TRUE 
           ORDER BY priority DESC, usage_count DESC""",
        fetchall=True
    )
    
    if not results:
        return []
    
    advice_list = []
    for r in results:
        advice = {
            "id": r[0], "topic": r[1], "label": r[2], "condition": r[3],
            "advice": r[4], "priority": r[5] or 5, "time": r[6],
            "frequency": r[7], "days": r[8] or [], "subjects": r[9] or [],
            "usage_count": r[10] or 0, "success_rate": r[11] or 0
        }
        
        if user_data and advice["subjects"]:
            user_weak = user_data.get("weak_subjects", []) or []
            user_strong = user_data.get("strong_subjects", []) or []
            all_user_subjects = user_weak + user_strong
            if all_user_subjects:
                if not any(subj in all_user_subjects for subj in advice["subjects"]):
                    continue
        
        advice_list.append(advice)
    
    return advice_list

def get_subject_status(user_id: int) -> List[Dict]:
    results = execute_query(
        """SELECT subject, level, avg_score, total_sessions, completed_sessions,
                  total_study_minutes, progress, last_studied, last_score
           FROM subject_status WHERE user_id = %s
           ORDER BY total_study_minutes DESC""",
        (user_id,), fetchall=True
    )
    return [{
        "subject": r[0], "level": r[1], "avg_score": r[2] or 0,
        "total_sessions": r[3] or 0, "completed_sessions": r[4] or 0,
        "total_study_minutes": r[5] or 0, "progress": r[6] or 0,
        "last_studied": r[7], "last_score": r[8]
    } for r in results] if results else []

def get_user_insights(user_id: int) -> Optional[Dict]:
    result = execute_query(
        """SELECT best_time, weakest_subject, strongest_subject, avg_daily_hours,
                  completion_rate, time_patterns, performance_patterns, quality_patterns,
                  burnout_risk
           FROM user_insights WHERE user_id = %s 
           ORDER BY analysis_date DESC LIMIT 1""",
        (user_id,), fetch=True
    )
    if not result:
        return None
    return {
        "best_time": result[0] or "نامشخص", "weakest_subject": result[1] or "نامشخص",
        "strongest_subject": result[2] or "نامشخص", "avg_daily_hours": result[3] or 0,
        "completion_rate": result[4] or 0, "time_patterns": result[5] or {},
        "performance_patterns": result[6] or {}, "quality_patterns": result[7] or {},
        "burnout_risk": result[8] or "low"
    }

def get_last_n_days_data(user_id: int, days: int = 7) -> List[Dict]:
    results = execute_query(
        """SELECT date, total_parts, completed_parts, plan_level
           FROM study_sessions WHERE user_id = %s 
           ORDER BY date DESC LIMIT %s""",
        (user_id, days), fetchall=True
    )
    return [{
        "date": r[0], "total_parts": r[1] or 0,
        "completed_parts": r[2] or 0, "plan_level": r[3] or 0
    } for r in results] if results else []

# ==================== Undo ====================
def save_change_history(user_id: int, session_id: int, part_id: int, action_type: str, 
                        previous_data: Dict, new_data: Dict = None, extra_data: Dict = None) -> None:
    try:
        def serialize_value(val):
            if hasattr(val, 'isoformat'):
                return val.isoformat()
            return val
        
        prev_serialized = {k: serialize_value(v) for k, v in previous_data.items() 
                          if k not in ["part_id", "session_id"]}
        new_serialized = {k: serialize_value(v) for k, v in (new_data or {}).items()}
        extra_serialized = {k: serialize_value(v) for k, v in (extra_data or {}).items()}
        
        execute_query(
            """INSERT INTO change_history (user_id, session_id, part_id, action_type, previous_data, new_data, extra_data)
               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (user_id, session_id, part_id, action_type, 
             json.dumps(prev_serialized), json.dumps(new_serialized), json.dumps(extra_serialized))
        )
        logger.info(f"✅ تغییر ذخیره شد: {action_type} - part_id: {part_id}")
    except Exception as e:
        logger.error(f"خطا در ذخیره تغییرات: {e}")

def get_last_change(user_id: int) -> Optional[Dict]:
    result = execute_query(
        """SELECT id, session_id, part_id, action_type, previous_data, new_data, extra_data, created_at
           FROM change_history WHERE user_id = %s AND is_reverted = FALSE
           ORDER BY created_at DESC LIMIT 1""",
        (user_id,), fetch=True
    )
    if not result:
        return None
    
    def safe_json_load(data):
        if not data:
            return {}
        try:
            return json.loads(data) if isinstance(data, str) else data
        except:
            return {}
    
    return {
        "id": result[0], "session_id": result[1], "part_id": result[2],
        "action_type": result[3], "previous_data": safe_json_load(result[4]),
        "new_data": safe_json_load(result[5]), "extra_data": safe_json_load(result[6]),
        "created_at": result[7]
    }

def revert_change(change_id: int) -> bool:
    try:
        execute_query("UPDATE change_history SET is_reverted = TRUE WHERE id = %s", (change_id,))
        return True
    except:
        return False

# ==================== چت AI و سقف مصرف ====================
def init_user_quota(user_id: int) -> None:
    try:
        execute_query("""
            INSERT INTO user_quota (user_id, daily_messages, last_reset, plan_type)
            VALUES (%s, 0, %s, 'trial')
            ON CONFLICT (user_id) DO UPDATE SET
                daily_messages = 0,
                last_reset = EXCLUDED.last_reset,
                plan_type = COALESCE(user_quota.plan_type, 'trial')
        """, (user_id, get_today_date()))
    except Exception as e:
        logger.error(f"خطا در بروزرسانی سقف مصرف: {e}")

def get_user_quota(user_id: int) -> Optional[Dict]:
    result = execute_query(
        """SELECT daily_messages, last_reset, plan_type, plan_expiry 
           FROM user_quota WHERE user_id = %s""",
        (user_id,), fetch=True
    )
    if not result:
        return None
    return {
        "daily_messages": result[0] or 0, "last_reset": result[1],
        "plan_type": result[2] or "trial", "plan_expiry": result[3]
    }

def get_remaining_messages(user_id: int) -> int:
    quota = get_user_quota(user_id)
    if not quota:
        return 10
    
    today = get_today_date()
    if str(quota["last_reset"]) != today:
        execute_query(
            "UPDATE user_quota SET daily_messages = 0, last_reset = %s WHERE user_id = %s",
            (today, user_id)
        )
        quota["daily_messages"] = 0
    
    limits = {"trial": 10, "basic": 15, "premium": 30}
    limit = limits.get(quota["plan_type"], 10)
    return max(0, limit - quota["daily_messages"])

def increment_quota(user_id: int) -> bool:
    try:
        execute_query(
            "UPDATE user_quota SET daily_messages = daily_messages + 1 WHERE user_id = %s",
            (user_id,)
        )
        return True
    except:
        return False

def save_chat_message(user_id: int, role: str, content: str) -> None:
    try:
        execute_query(
            "INSERT INTO chat_messages (user_id, role, content) VALUES (%s, %s, %s)",
            (user_id, role, content)
        )
    except Exception as e:
        logger.error(f"خطا در ذخیره پیام چت: {e}")

def get_chat_history(user_id: int, limit: int = 10) -> List[Dict]:
    results = execute_query(
        """SELECT role, content FROM chat_messages 
           WHERE user_id = %s ORDER BY created_at DESC LIMIT %s""",
        (user_id, limit * 2), fetchall=True
    )
    if not results:
        return []
    return [{"role": r[0], "content": r[1]} for r in reversed(results)]

def clear_chat_history(user_id: int) -> None:
    execute_query("DELETE FROM chat_messages WHERE user_id = %s", (user_id,))

# ==================== ذخیره‌سازی ====================
def save_user(user_data: Dict) -> Optional[int]:
    query = """
    INSERT INTO users (telegram_id, username, full_name, goal, grade, field,
                       exam_date, study_hours_per_week, peak_time, learning_style,
                       focus_duration, break_duration, weak_subjects, strong_subjects,
                       daily_schedule, is_active, is_onboarded, current_phase, plan_level)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (telegram_id) DO UPDATE SET
        username = EXCLUDED.username, full_name = EXCLUDED.full_name,
        goal = EXCLUDED.goal, grade = EXCLUDED.grade, field = EXCLUDED.field,
        exam_date = EXCLUDED.exam_date, study_hours_per_week = EXCLUDED.study_hours_per_week,
        peak_time = EXCLUDED.peak_time, learning_style = EXCLUDED.learning_style,
        focus_duration = EXCLUDED.focus_duration, break_duration = EXCLUDED.break_duration,
        weak_subjects = EXCLUDED.weak_subjects, strong_subjects = EXCLUDED.strong_subjects,
        daily_schedule = EXCLUDED.daily_schedule, is_onboarded = EXCLUDED.is_onboarded,
        plan_level = EXCLUDED.plan_level, updated_at = CURRENT_TIMESTAMP
    RETURNING id
    """
    result = execute_query(query, (
        user_data["telegram_id"], user_data.get("username"), user_data.get("full_name"),
        user_data.get("goal"), user_data.get("grade"), user_data.get("field"),
        user_data.get("exam_date"), user_data.get("study_hours_per_week"),
        user_data.get("peak_time"), user_data.get("learning_style"),
        user_data.get("focus_duration", 45), user_data.get("break_duration", 10),
        json.dumps(user_data.get("weak_subjects", [])),
        json.dumps(user_data.get("strong_subjects", [])),
        json.dumps(user_data.get("daily_schedule", {})),
        user_data.get("is_active", True), user_data.get("is_onboarded", False),
        user_data.get("current_phase", 0), user_data.get("plan_level", 0)
    ), fetch=True)
    return result[0] if result else None

def update_user_plan_level(user_id: int, level: int) -> bool:
    result = execute_query(
        "UPDATE users SET plan_level = %s, updated_at = CURRENT_TIMESTAMP WHERE id = %s",
        (level, user_id)
    )
    return result > 0 if result is not None else True

def save_session_with_parts(user_id: int, parts: List[Dict], time_slots: List[str], 
                           topics: List[Dict], plan_level: int = 0) -> Optional[int]:
    conn = None
    cursor = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        date = get_today_date()
        
        # آرشیو کردن سشن قبلی
        cursor.execute(
            "UPDATE study_sessions SET archived = TRUE WHERE user_id = %s AND date = %s AND archived = FALSE",
            (user_id, date)
        )
        
        cursor.execute("""
            INSERT INTO study_sessions (user_id, date, total_parts, max_edits, time_slots, topics, archived, plan_level)
            VALUES (%s, %s, %s, %s, %s, %s, FALSE, %s)
            RETURNING session_id
        """, (user_id, date, len(parts), 2, json.dumps(time_slots), json.dumps(topics), plan_level))
        
        result = cursor.fetchone()
        if not result:
            conn.rollback()
            return None
        
        session_id = result[0]
        
        for part in parts:
            planned_start = part.get("planned_start_time")
            planned_end = part.get("planned_end_time")
            
            if not planned_start and part.get("time_slot"):
                try:
                    start_str, end_str = part["time_slot"].split("-")
                    planned_start = start_str
                    planned_end = end_str
                except:
                    pass
            
            cursor.execute("""
                INSERT INTO study_parts (
                    session_id, part_number, title, grade,
                    planned_minutes, time_slot, is_hardest, is_easiest, pages,
                    planned_start_time, planned_end_time, is_fixed_time, reason
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                session_id, part["part_number"], part["title"],
                part.get("grade", 3), part["planned_minutes"],
                part.get("time_slot", ""), part.get("is_hardest", False),
                part.get("is_easiest", False), part.get("pages", 0),
                planned_start, planned_end, part.get("is_fixed_time", False),
                part.get("reason", "")
            ))
        
        conn.commit()
        return session_id
    except Exception as e:
        logger.error(f"❌ خطا در save_session_with_parts: {e}")
        if conn:
            conn.rollback()
        return None
    finally:
        if cursor: cursor.close()
        if conn: return_connection(conn)

def add_part_to_session(session_id: int, part_data: Dict) -> Optional[int]:
    conn = None
    cursor = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        
        cursor.execute(
            "SELECT COALESCE(MAX(part_number), 0) + 1 FROM study_parts WHERE session_id = %s",
            (session_id,)
        )
        result = cursor.fetchone()
        new_part_number = result[0] if result else 1
        
        cursor.execute("""
            INSERT INTO study_parts (
                session_id, part_number, title, grade,
                planned_minutes, time_slot, pages, completed,
                planned_start_time, planned_end_time, is_fixed_time, reason
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING part_id
        """, (
            session_id, new_part_number, part_data["title"],
            part_data.get("grade", 3), part_data["planned_minutes"],
            part_data.get("time_slot", ""), part_data.get("pages", 0),
            False, part_data.get("planned_start_time"),
            part_data.get("planned_end_time"),
            part_data.get("is_fixed_time", False), part_data.get("reason", "")
        ))
        
        result = cursor.fetchone()
        if not result:
            conn.rollback()
            return None
        
        part_id = result[0]
        cursor.execute(
            "UPDATE study_sessions SET total_parts = total_parts + 1 WHERE session_id = %s",
            (session_id,)
        )
        
        conn.commit()
        return part_id
    except Exception as e:
        logger.error(f"❌ خطا در add_part_to_session: {e}")
        if conn: conn.rollback()
        return None
    finally:
        if cursor: cursor.close()
        if conn: return_connection(conn)

def confirm_session(session_id: int) -> None:
    execute_query("UPDATE study_sessions SET confirmed = TRUE WHERE session_id = %s", (session_id,))

def finish_session(session_id: int) -> None:
    execute_query("UPDATE study_sessions SET is_finished = TRUE, archived = TRUE WHERE session_id = %s", (session_id,))

def save_plan(plan_data: Dict) -> Optional[int]:
    query = """
    INSERT INTO personalized_plans (
        user_id, date, daily_plan, reasoning, expected_outcome,
        applied_advice_ids, is_active
    ) VALUES (%s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (user_id, date) DO UPDATE SET
        daily_plan = EXCLUDED.daily_plan, reasoning = EXCLUDED.reasoning,
        expected_outcome = EXCLUDED.expected_outcome,
        applied_advice_ids = EXCLUDED.applied_advice_ids,
        is_active = EXCLUDED.is_active, updated_at = CURRENT_TIMESTAMP
    RETURNING id
    """
    result = execute_query(query, (
        plan_data["user_id"], plan_data["date"],
        json.dumps(plan_data["daily_plan"]),
        json.dumps(plan_data.get("reasoning", {})),
        json.dumps(plan_data.get("expected_outcome", {})),
        json.dumps(plan_data.get("applied_advice_ids", [])),
        plan_data.get("is_active", True)
    ), fetch=True)
    return result[0] if result else None

def save_advice(advice_data: Dict) -> Optional[int]:
    query = """
    INSERT INTO advisory_rules (
        topic, label, condition, advice, priority, time, frequency,
        days, applicable_for, subjects, is_active, is_system_generated, created_by
    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    RETURNING id
    """
    result = execute_query(query, (
        advice_data["topic"], advice_data.get("label"),
        advice_data.get("condition"), advice_data["advice"],
        advice_data.get("priority", 5), advice_data.get("time"),
        advice_data.get("frequency"), json.dumps(advice_data.get("days", [])),
        json.dumps(advice_data.get("applicable_for", {})),
        json.dumps(advice_data.get("subjects", [])),
        advice_data.get("is_active", True),
        advice_data.get("is_system_generated", False),
        advice_data.get("created_by")
    ), fetch=True)
    return result[0] if result else None

def save_activity(activity_data: Dict) -> Optional[int]:
    query = """
    INSERT INTO activity_log (
        user_id, date, subject, topic, activity_type, planned_duration,
        actual_duration, start_time, end_time, score, status, difficulty,
        focus_rating, energy_level, mood, distractions, notes,
        break_duration, pages_count, test_count, correct_count, part_order
    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    RETURNING id
    """
    result = execute_query(query, (
        activity_data["user_id"], activity_data["date"],
        activity_data["subject"], activity_data.get("topic"),
        activity_data.get("activity_type"), activity_data.get("planned_duration"),
        activity_data.get("actual_duration"), activity_data.get("start_time"),
        activity_data.get("end_time"), activity_data.get("score"),
        activity_data.get("status", "pending"), activity_data.get("difficulty"),
        activity_data.get("focus_rating"), activity_data.get("energy_level"),
        activity_data.get("mood"), json.dumps(activity_data.get("distractions", [])),
        activity_data.get("notes"), activity_data.get("break_duration"),
        activity_data.get("pages_count"), activity_data.get("test_count"),
        activity_data.get("correct_count"), activity_data.get("part_order", 0)
    ), fetch=True)
    return result[0] if result else None

def update_subject_status(user_id: int, subject: str, activity_data: Dict) -> None:
    current = execute_query(
        "SELECT * FROM subject_status WHERE user_id = %s AND subject = %s",
        (user_id, subject), fetch=True
    )
    
    if current:
        total_sessions = (current[4] or 0) + 1
        completed = (current[5] or 0) + (1 if activity_data.get("status") == "done" else 0)
        total_minutes = (current[6] or 0) + (activity_data.get("actual_duration") or 0)
        old_avg = current[7] or 0
        new_score = activity_data.get("score")
        avg_score = (old_avg * (total_sessions - 1) + new_score) / total_sessions if new_score is not None else old_avg
        
        execute_query(
            """UPDATE subject_status SET total_sessions = %s, completed_sessions = %s,
               total_study_minutes = %s, avg_score = %s, last_studied = %s, last_score = %s,
               updated_at = CURRENT_TIMESTAMP WHERE user_id = %s AND subject = %s""",
            (total_sessions, completed, total_minutes, avg_score,
             activity_data["date"], new_score, user_id, subject)
        )
    else:
        execute_query(
            """INSERT INTO subject_status (user_id, subject, total_sessions, 
               completed_sessions, total_study_minutes, avg_score, last_studied, last_score)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
            (user_id, subject, 1, 1 if activity_data.get("status") == "done" else 0,
             activity_data.get("actual_duration") or 0, activity_data.get("score"),
             activity_data["date"], activity_data.get("score"))
        )

def calculate_plan_level(user_id: int) -> int:
    """محاسبه سطح برنامه‌ریزی کاربر (بر اساس user_id داخلی دیتابیس)
    
    Returns:
        0: اولیه (کاربر تازه‌وارد)
        1: روزانه (حداقل ۱ روز فعال)
        2: شخصی‌سازی‌شده (۷ روز + ۵ روز مطالعه)
        3: شناور (۱۴ روز + ۱۰ روز مطالعه)
    """
    try:
        # ============================================
        # ۱. محاسبه روزهای فعال از ثبت‌نام
        # ============================================
        result = execute_query(
            "SELECT created_at FROM users WHERE id = %s",
            (user_id,),
            fetch=True
        )
        
        if not result or not result[0]:
            logger.warning(f"⚠️ کاربر {user_id} یافت نشد")
            return 0
        
        created_at = result[0]
        
        # تبدیل به datetime اگر رشته است
        if isinstance(created_at, str):
            try:
                created_at = datetime.strptime(created_at, "%Y-%m-%d %H:%M:%S")
            except:
                try:
                    created_at = datetime.fromisoformat(created_at)
                except:
                    logger.warning(f"⚠️ فرمت created_at نامعتبر: {created_at}")
                    return 0
        
        # حذف timezone برای محاسبه ساده
        if hasattr(created_at, 'tzinfo') and created_at.tzinfo is not None:
            created_at = created_at.replace(tzinfo=None)
        
        now = get_iran_now().replace(tzinfo=None)
        days_active = max(0, (now - created_at).days)
        
        logger.info(f"📊 کاربر {user_id}: days_active={days_active}")
        
        # ============================================
        # ۲. محاسبه روزهای مطالعه
        # ============================================
        sessions_result = execute_query(
            """SELECT COUNT(DISTINCT date) FROM study_sessions 
               WHERE user_id = %s AND archived = FALSE""",
            (user_id,),
            fetch=True
        )
        study_days = sessions_result[0] if sessions_result and sessions_result[0] else 0
        
        logger.info(f"📊 کاربر {user_id}: study_days={study_days}")
        
        # ============================================
        # ۳. تعیین سطح
        # ============================================
        if days_active >= 14 and study_days >= 10:
            level = 3
        elif days_active >= 7 and study_days >= 5:
            level = 2
        elif days_active >= 1 and study_days >= 1:
            level = 1
        else:
            level = 0
        
        logger.info(f"✅ سطح کاربر {user_id}: {level} (days_active={days_active}, study_days={study_days})")
        return level
        
    except Exception as e:
        logger.error(f"❌ خطا در calculate_plan_level برای کاربر {user_id}: {e}")
        return 0

def get_plan_level_name(level: int) -> str:
    return PLAN_LEVELS.get(level, PLAN_LEVELS[0])["name"]

def get_plan_level_emoji(level: int) -> str:
    return PLAN_LEVELS.get(level, PLAN_LEVELS[0])["emoji"]
# ==================== پرامپت‌های AI بر اساس سطح ====================

def generate_plan_prompt_level_0(user_data: Dict, user_id: int) -> str:
    weak = ", ".join(user_data.get("weak_subjects", [])) or "ندارد"
    strong = ", ".join(user_data.get("strong_subjects", [])) or "ندارد"
    user_request = user_data.get("user_request", "")
    request_instruction = f"\n\nدرخواست ویژه کاربر: {user_request}" if user_request else ""
    
    return f"""شما یک دستیار برنامه‌ریزی مطالعه هستید.

=== اطلاعات کاربر ===
هدف: {user_data.get('goal', 'نامشخص')}
پایه: {user_data.get('grade', 'نامشخص')}
رشته: {user_data.get('field', 'نامشخص')}
تاریخ آزمون: {user_data.get('exam_date', 'نامشخص')}

=== نقطه شروع ===
کاربر تازه وارد ربات شده است.

=== درس‌های ضعیف ===
{weak}

=== درس‌های قوی ===
{strong}

=== زمان موجود ===
{user_data.get('study_hours_per_week', 10)} ساعت در هفته
بهترین زمان: {user_data.get('peak_time', 'نامشخص')}{request_instruction}

=== وظیفه ===
یک برنامه مطالعه اولیه برای امروز طراحی کن.

قوانین:
1. درس‌های ضعیف اولویت دارند
2. هر جلسه {user_data.get('focus_duration', 45)} دقیقه با {user_data.get('break_duration', 10)} دقیقه استراحت
3. حداکثر ۳ جلسه در روز
4. اگر کاربر درخواست خاصی دارد، به آن توجه کن
5. **در این سطح (اولیه) زمان‌بندی ساعتی انجام نده — فقط ترتیب و مدت پارت‌ها را مشخص کن**

خروجی JSON:
{{
  "subjects": [
    {{"subject": "نام درس", "topic": "مبحث", "duration": 45, "priority": "high", "reason": "دلیل انتخاب"}}
  ],
  "breaks": [{{"duration": 10}}],
  "total_hours": 2.5,
  "recommendations": ["توصیه کلی"]
}}"""

def generate_plan_prompt_level_1(user_data: Dict, user_id: int, 
                                 subject_status: List[Dict], 
                                 yesterday_activities: List[Dict]) -> str:
    status_text = "\n".join([
        f"- {s['subject']}: میانگین {s.get('avg_score', 0):.1f}% | {s.get('completed_sessions', 0)} جلسه"
        for s in subject_status[:5]
    ]) if subject_status else "داده‌ای موجود نیست"
    
    yesterday_text = "\n".join([
        f"- {a['subject']}: {a.get('actual_duration', a.get('planned_duration', 0))} دقیقه | {'✅' if a.get('status') == 'done' else '⬜'}"
        for a in yesterday_activities[:5]
    ]) if yesterday_activities else "فعالیتی ثبت نشده"
    
    total_time = sum(a.get('actual_duration', a.get('planned_duration', 0)) for a in yesterday_activities)
    done = len([a for a in yesterday_activities if a.get('status') == 'done'])
    total = len(yesterday_activities)
    scores = [a.get('score') for a in yesterday_activities if a.get('score') is not None]
    avg_score = sum(scores) / len(scores) if scores else 0
    
    advice = get_active_advice_for_user(user_id, user_data)
    if advice:
        advice_text = "\n".join([
            f"- 🆔 {a['id']} | [{a['topic']}] {a['advice']}\n"
            f"  📊 اولویت ادمین: {a.get('priority', 5)}/10 | بار استفاده: {a.get('usage_count', 0)}"
            for a in advice[:10]
        ])
    else:
        advice_text = "توصیه‌ای موجود نیست"
    
    user_request = user_data.get("user_request", "")
    request_instruction = f"\n\nدرخواست ویژه کاربر: {user_request}" if user_request else ""
    
    return f"""شما یک دستیار برنامه‌ریزی مطالعه هستید.

=== اطلاعات کاربر ===
نام: {user_data.get('full_name', 'کاربر')}
هدف: {user_data.get('goal', 'نامشخص')}
پایه: {user_data.get('grade', 'نامشخص')}

=== وضعیت دروس ===
{status_text}

=== فعالیت‌های دیروز ===
{yesterday_text}

=== عملکرد دیروز ===
- کل زمان: {format_time_hours_minutes(total_time)}
- تکمیل‌شده: {done}/{total}
- میانگین نمره: {avg_score:.1f}%

=== توصیه‌های ادمین ===
{advice_text}

⚠️ **مهم درباره توصیه‌های ادمین:**
توصیه‌های بالا از طرف ادمین هستن. تو باید خودت تشخیص بدی:
1. کدوم توصیه برای این کاربر الان مناسب‌تره
2. چقدر باید در برنامه امروز اعمال بشه
3. کدوم توصیه اولویت بالاتری داره (با توجه به هدف، وضعیت دروس و عملکرد کاربر)
4. در فیلد reason پارت‌ها بنویس که کدوم توصیه ادمین باعث انتخاب اون شده

=== وظیفه ===
برنامه مطالعه امروز را بر اساس عملکرد دیروز و توصیه‌های ادمین طراحی کن.

قوانین:
1. درس‌های ضعیف را صبح بگذار
2. زمان هر جلسه بر اساس {user_data.get('focus_duration', 45)} دقیقه
3. بین جلسات {user_data.get('break_duration', 10)} دقیقه استراحت
4. **توصیه‌های ادمین رو با تشخیص خودت در برنامه اعمال کن**
5. **در این سطح (روزانه) زمان‌بندی ساعتی انجام بده اما ساده — از ۸ صبح شروع کن**{request_instruction}

خروجی JSON:
{{
  "subjects": [
    {{
      "subject": "نام درس",
      "topic": "مبحث",
      "duration": 45,
      "priority": "high",
      "reason": "دلیل انتخاب (شامل اشاره به توصیه ادمین اگه استفاده شده)"
    }}
  ],
  "breaks": [{{"duration": 10, "type": "استراحت"}}],
  "total_hours": 3,
  "applied_advice_ids": [1, 5],
  "recommendations": ["توصیه امروز"]
}}

در فیلد `applied_advice_ids` آیدی توصیه‌هایی که اعمال کردی رو بنویس.
"""

def generate_plan_prompt_level_2(user_data: Dict, user_id: int, 
                                 insights: Dict, advice: List[Dict]) -> str:
    if advice:
        advice_text = "\n".join([
            f"- 🆔 {a['id']} | [{a['topic']}] {a['advice']}\n"
            f"  📊 اولویت ادمین: {a.get('priority', 5)}/10 | "
            f"بار استفاده: {a.get('usage_count', 0)} | "
            f"نرخ موفقیت: {a.get('success_rate', 0)*100:.0f}%"
            for a in advice[:10]
        ])
    else:
        advice_text = "توصیه‌ای موجود نیست"
    
    sessions = get_last_n_days_data(user_id, 7)
    daily_data = "\n".join([
        f"روز {i+1}: {s['date']} - {s['completed_parts']}/{s['total_parts']} پارت"
        for i, s in enumerate(sessions)
    ]) if sessions else "داده‌ای موجود نیست"
    
    user_request = user_data.get("user_request", "")
    request_instruction = f"\n\nدرخواست ویژه کاربر: {user_request}" if user_request else ""
    
    return f"""شما یک تحلیلگر و برنامه‌ریز هوشمند مطالعه هستید.

=== اطلاعات کاربر ===
نام: {user_data.get('full_name', 'کاربر')}
هدف: {user_data.get('goal', 'نامشخص')}
پایه: {user_data.get('grade', 'نامشخص')}
رشته: {user_data.get('field', 'نامشخص')}
درس‌های ضعیف: {", ".join(user_data.get('weak_subjects', [])) or 'ندارد'}

=== داده‌های ۷ روز اخیر ===
{daily_data}

=== تحلیل الگوها ===
- بهترین زمان: {insights.get('best_time', 'نامشخص')}
- ضعیف‌ترین درس: {insights.get('weakest_subject', 'نامشخص')}
- قوی‌ترین درس: {insights.get('strongest_subject', 'نامشخص')}
- میانگین روزانه: {insights.get('avg_daily_hours', 0):.1f} ساعت
- نرخ تکمیل: {insights.get('completion_rate', 0):.1f}%

=== توصیه‌های ادمین ===
{advice_text}

⚠️ **مهم درباره توصیه‌های ادمین:**
تو باید خودت تشخیص بدی:
1. کدوم توصیه برای این کاربر با توجه به هدف و وضعیتش مناسب‌تره
2. اولویت‌بندی واقعی رو خودت انجام بده (نه فقط بر اساس اولویت ادمین)
3. توصیه‌ای که با الگوهای کاربر هماهنگ‌تره رو در اولویت بذار
4. اگه توصیه‌ای با وضعیت فعلی کاربر نمی‌خوره، اعمالش نکن و دلیلش رو بگو
5. در فیلد reason هر پارت، اگه از توصیه ادمین استفاده کردی، اشاره کن

مثال:
- اگه توصیه میگه "صبح ریاضی بخون" و کاربر بهترین زمانش صبحه → اعمال کن
- اگه توصیه میگه "روزانه ۴ ساعت مطالعه" ولی کاربر تازه‌کاره → با دوز کمتر اعمال کن

=== وظیفه ===
برنامه شخصی‌سازی‌شده برای امروز طراحی کن.

قوانین:
1. درس ضعیف را در بهترین زمان بگذار
2. زمان هر جلسه بر اساس {user_data.get('focus_duration', 45)} دقیقه
3. توصیه‌های ادمین رو با تشخیص خودت و متناسب با کاربر اعمال کن
4. **در این سطح، زمان‌بندی هوشمند بر اساس الگوهای کاربر انجام بده**{request_instruction}

خروجی JSON:
{{
  "subjects": [
    {{
      "subject": "نام درس",
      "topic": "مبحث",
      "duration": 45,
      "priority": "high",
      "time_slot": "morning",
      "reason": "چرا این زمان (اشاره به توصیه ادمین اگه استفاده شده)",
      "advice_used": "آیدی توصیه اگه استفاده شده"
    }}
  ],
  "breaks": [{{"duration": 10, "type": "استراحت"}}],
  "total_hours": 3.5,
  "applied_advice_ids": [1, 5],
  "skipped_advice_reasons": {{
    "3": "دلیل اینکه توصیه ۳ اعمال نشد"
  }},
  "recommendations": ["توصیه شخصی‌سازی‌شده"],
  "expected_outcome": {{
    "completion_probability": 0.85,
    "expected_score": 75
  }}
}}
"""

def generate_plan_prompt_level_3(user_data: Dict, user_id: int, 
                                 insights: Dict, advice: List[Dict]) -> str:
    if advice:
        advice_text = "\n".join([
            f"- 🆔 {a['id']} | [{a['topic']}] {a['advice']}\n"
            f"  📊 اولویت ادمین: {a.get('priority', 5)}/10 | "
            f"بار استفاده: {a.get('usage_count', 0)} | "
            f"نرخ موفقیت: {a.get('success_rate', 0)*100:.0f}%\n"
            f"  ⏰ زمان پیشنهادی: {a.get('time', 'نامشخص')} | "
            f"تکرار: {a.get('frequency', 'نامشخص')}"
            for a in advice[:10]
        ])
    else:
        advice_text = "توصیه‌ای موجود نیست"
    
    sessions = get_last_n_days_data(user_id, 14)
    daily_data = "\n".join([
        f"روز {i+1}: {s['date']} - {s['completed_parts']}/{s['total_parts']} پارت"
        for i, s in enumerate(sessions)
    ]) if sessions else "داده‌ای موجود نیست"
    
    time_patterns = insights.get('time_patterns', {})
    perf_patterns = insights.get('performance_patterns', {})
    quality_patterns = insights.get('quality_patterns', {})
    
    user_request = user_data.get("user_request", "")
    request_instruction = f"\n\nدرخواست ویژه کاربر: {user_request}" if user_request else ""
    
    return f"""شما یک دستیار هوشمند برنامه‌ریزی تطبیقی هستید.

=== اطلاعات کاربر ===
نام: {user_data.get('full_name', 'کاربر')}
هدف: {user_data.get('goal', 'نامشخص')}
پایه: {user_data.get('grade', 'نامشخص')}
درس‌های ضعیف: {", ".join(user_data.get('weak_subjects', [])) or 'ندارد'}

=== داده‌های ۱۴ روز اخیر ===
{daily_data}

=== الگوهای پیشرفته ===
⏰ الگوهای زمانی:
{json.dumps(time_patterns, ensure_ascii=False) if time_patterns else 'در حال جمع‌آوری'}

📊 الگوهای عملکردی:
{json.dumps(perf_patterns, ensure_ascii=False) if perf_patterns else 'در حال جمع‌آوری'}

🎯 الگوهای کیفی:
{json.dumps(quality_patterns, ensure_ascii=False) if quality_patterns else 'در حال جمع‌آوری'}

=== توصیه‌های ادمین ===
{advice_text}

⚠️ **مهم درباره توصیه‌های ادمین:**
تو یک دستیار پیشرفته هستی. باید خودت تصمیم بگیری:

1. **تشخیص ارتباط**: کدوم توصیه با وضعیت فعلی کاربر هماهنگه
2. **اولویت‌بندی پویا**: 
   - توصیه‌هایی که با الگوهای موفق کاربر همراستان → اولویت بالا
   - توصیه‌هایی که با نرخ موفقیت بالای قبلی همراه بودن → اولویت بالا
   - توصیه‌هایی که با وضعیت فعلی (خستگی، انرژی) نمی‌خونن → کم‌اولویت یا رد
3. **شخصی‌سازی**: توصیه رو با اعداد و زمان‌های کاربر تنظیم کن
   - اگه توصیه میگه "۲ ساعت ریاضی" ولی کاربر ۴۵ دقیقه تمرکز داره → به جلسات ۴۵ دقیقه‌ای بشکن
4. **ترکیب توصیه‌ها**: اگه دو توصیه مکمل هستن، ترکیبشون کن
5. **توضیح**: در فیلد reason هر پارت، دلیل انتخاب رو با اشاره به توصیه بنویس

=== وظیفه ===
برنامه شناور امروز رو با زمان‌بندی دقیق طراحی کن.

قوانین:
1. زمان‌ها بر اساس الگوهای کاربر
2. درس‌های سخت در زمان‌های با انرژی بالا
3. هر جلسه {user_data.get('focus_duration', 45)} دقیقه
4. ۱۰ دقیقه قبل از هر جلسه اعلان
5. **توصیه‌های ادمین رو با تشخیص پیشرفته و شخصی‌سازی اعمال کن**{request_instruction}

خروجی JSON:
{{
  "subjects": [
    {{
      "subject": "نام درس",
      "topic": "مبحث",
      "duration": 45,
      "priority": "high",
      "time": "08:00",
      "end_time": "08:45",
      "alert_before": 10,
      "flexible": true,
      "reason": "دلیل زمان‌بندی + اشاره به توصیه ادمین اگه استفاده شده",
      "advice_used": "آیدی توصیه"
    }}
  ],
  "breaks": [
    {{"time": "08:45", "duration": 10, "type": "استراحت"}}
  ],
  "total_hours": 4,
  "applied_advice_ids": [1, 5],
  "skipped_advice_reasons": {{
    "3": "چرا این توصیه اعمال نشد"
  }},
  "adaptive_rules": {{
    "if_late": "تطبیق",
    "if_tired": "کاهش"
  }},
  "recommendations": ["توصیه شناور"],
  "expected_outcome": {{
    "completion_probability": 0.9,
    "expected_score": 80,
    "burnout_risk": "{insights.get('burnout_risk', 'low')}"
  }},
  "alerts": [
    {{"time": "07:50", "message": "۱۰ دقیقه تا شروع ریاضی"}}
  ]
}}
"""

# ==================== تولید برنامه با AI ====================

async def call_ai(prompt: str, max_tokens: int = 1500, temperature: float = 0.3) -> Optional[str]:
    for attempt in range(3):
        try:
            completion = await client.chat.completions.create(
                model=AI_MODEL,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=max_tokens,
                temperature=temperature
            )
            return completion.choices[0].message.content
        except Exception as e:
            logger.error(f"AI error (attempt {attempt+1}): {e}")
            if attempt < 2:
                await asyncio.sleep(2 ** attempt)
            else:
                return None
    return None

async def generate_plan_with_ai(user_id: int, user_data: Dict) -> Dict:
    level = user_data.get('plan_level', 0)
    
    subject_status = get_subject_status(user_id)
    yesterday_activities = get_yesterday_activities(user_id)
    advice = get_active_advice_for_user(user_id, user_data)
    insights = get_user_insights(user_id)
    
    if level == 0:
        prompt = generate_plan_prompt_level_0(user_data, user_id)
    elif level == 1:
        prompt = generate_plan_prompt_level_1(user_data, user_id, subject_status, yesterday_activities)
    elif level == 2:
        prompt = generate_plan_prompt_level_2(user_data, user_id, insights or {}, advice)
    else:
        prompt = generate_plan_prompt_level_3(user_data, user_id, insights or {}, advice)
    
    response = await call_ai(prompt, max_tokens=1500, temperature=0.3)
    if not response:
        return {}
    
    try:
        json_match = re.search(r'\{.*\}', response, re.DOTALL)
        if json_match:
            return json.loads(json_match.group())
        return {}
    except Exception as e:
        logger.error(f"❌ خطا در پارس JSON: {e}")
        fix_prompt = f"خروجی قبلی JSON معتبر نبود. لطفاً فقط JSON خالص برگردان. خطا: {e}\nخروجی قبلی: {response[:200]}..."
        fixed_response = await call_ai(fix_prompt, max_tokens=800, temperature=0.1)
        if fixed_response:
            try:
                json_match = re.search(r'\{.*\}', fixed_response, re.DOTALL)
                if json_match:
                    return json.loads(json_match.group())
            except:
                pass
        return {}

def create_plan_from_ai_response(user_id: int, user_data: Dict, ai_response: Dict) -> Optional[int]:
    subjects = ai_response.get('subjects', [])
    if not subjects:
        return None
    
    level = user_data.get('plan_level', 0)
    parts = []
    current_time = 8 * 60
    applied_advice_ids = ai_response.get('applied_advice_ids', [])
    
    for idx, subj in enumerate(subjects):
        duration = subj.get('duration', user_data.get('focus_duration', 45))
        duration = max(20, min(90, duration))
        
        grade = 3
        if subj.get('priority') == 'high':
            grade = 4
        elif subj.get('priority') == 'low':
            grade = 2
        
        # ============================================
        # زمان‌بندی — فقط در سطوح ۱ به بالا
        # ============================================
        if level >= 2 and subj.get('time') and subj.get('end_time'):
            # سطح ۲ و ۳: زمان دقیق از AI
            try:
                start_str = subj['time']
                end_str = subj['end_time']
                start_h, start_m = map(int, start_str.split(':'))
                end_h, end_m = map(int, end_str.split(':'))
                current_time = start_h * 60 + start_m
                end_time = end_h * 60 + end_m
            except:
                end_time = current_time + duration
        else:
            # سطح ۰ و ۱: محاسبه خودکار
            end_time = current_time + duration
        
        start_h = current_time // 60
        start_m = current_time % 60
        end_h = end_time // 60
        end_m = end_time % 60
        
        # ============================================
        # در سطح ۰، زمان را خالی بگذار
        # ============================================
        if level == 0:
            time_slot_value = ""
            planned_start_value = None
            planned_end_value = None
        else:
            time_slot_value = f"{start_h:02d}:{start_m:02d}-{end_h:02d}:{end_m:02d}"
            planned_start_value = f"{start_h:02d}:{start_m:02d}"
            planned_end_value = f"{end_h:02d}:{end_m:02d}"
        
        part = {
            "part_number": idx + 1,
            "title": subj.get('subject', 'مطالعه'),
            "topic": subj.get('topic', ''),
            "grade": grade,
            "planned_minutes": duration,
            "pages": 0,
            "time_slot": time_slot_value,
            "planned_start_time": planned_start_value,
            "planned_end_time": planned_end_value,
            "completed": False,
            "is_fixed_time": False,
            "reason": subj.get('reason', '')
        }
        parts.append(part)
        
        # ادامه محاسبه current_time برای پارت بعدی
        breaks = ai_response.get('breaks', [])
        if breaks and idx < len(subjects) - 1:
            break_duration = breaks[0].get('duration', 10) if idx < len(breaks) else 10
            current_time = end_time + break_duration
        else:
            current_time = end_time + 5
    
    session_id = save_session_with_parts(user_id, parts, [], subjects, level)
    
    # ذخیره توصیه‌های اعمال‌شده
    if session_id and applied_advice_ids:
        for advice_id in applied_advice_ids:
            try:
                execute_query(
                    """UPDATE advisory_rules 
                       SET usage_count = COALESCE(usage_count, 0) + 1,
                           last_used = CURRENT_TIMESTAMP
                       WHERE id = %s""",
                    (advice_id,)
                )
            except Exception as e:
                logger.error(f"خطا در بروزرسانی توصیه {advice_id}: {e}")
        
        try:
            save_plan({
                "user_id": user_id,
                "date": get_today_date(),
                "daily_plan": {"parts": parts},
                "reasoning": ai_response.get("reasoning", {}),
                "expected_outcome": ai_response.get("expected_outcome", {}),
                "applied_advice_ids": applied_advice_ids,
                "is_active": True
            })
        except Exception as e:
            logger.error(f"خطا در ذخیره برنامه: {e}")
    
    if session_id and level == 3:
        alerts = ai_response.get('alerts', [])
        for alert in alerts:
            try:
                alert_time_str = alert.get('time', '')
                if alert_time_str:
                    h, m = map(int, alert_time_str.split(':'))
                    alert_dt = get_iran_now().replace(hour=h, minute=m, second=0, microsecond=0)
                    if alert_dt < get_iran_now():
                        alert_dt += timedelta(days=1)
                    
                    for part in parts:
                        if part.get('title') in alert.get('message', ''):
                            execute_query(
                                """INSERT INTO daily_alerts (user_id, part_id, alert_time, message)
                                   VALUES (%s, %s, %s, %s)""",
                                (user_id, part.get('part_id'), alert_dt, alert.get('message', ''))
                            )
                            break
            except Exception as e:
                logger.error(f"خطا در ذخیره اعلان: {e}")
    
    return session_id

# ==================== ساخت دستی ====================

def parse_manual_times(time_text: str) -> List[Tuple[str, str]]:
    time_slots = []
    for line in time_text.strip().split('\n'):
        line = line.strip()
        if not line:
            continue
        start, end = parse_time_slot(line)
        if start and end:
            time_slots.append((start, end))
    return time_slots

def parse_manual_activities(activity_text: str) -> List[Dict]:
    activities = []
    for line in activity_text.strip().split('\n'):
        line = line.strip()
        if not line:
            continue
        parts = line.split('|')
        if len(parts) >= 2:
            title = parts[0].strip()
            try:
                duration = int(parts[1].strip().replace('دقیقه', '').strip())
                priority = parts[2].strip() if len(parts) > 2 else 'متوسط'
                
                grade = 3
                if priority in ['بالا', 'زیاد', 'high']:
                    grade = 4
                elif priority in ['پایین', 'کم', 'low']:
                    grade = 2
                
                activities.append({
                    "title": title, "duration": duration,
                    "grade": grade, "priority": priority
                })
            except:
                continue
    return activities

def create_manual_plan(user_id: int, time_slots: List[Tuple[str, str]], activities: List[Dict]) -> Optional[int]:
    if not time_slots or not activities or len(time_slots) != len(activities):
        return None
    
    parts = []
    for i, (slot, activity) in enumerate(zip(time_slots, activities)):
        start_time, end_time = slot
        start_min = time_to_minutes(start_time)
        end_min = time_to_minutes(end_time)
        duration = end_min - start_min
        
        if activity.get('duration', 0) > 0:
            duration = activity['duration']
        
        parts.append({
            "part_number": i + 1,
            "title": activity['title'],
            "topic": "",
            "grade": activity.get('grade', 3),
            "planned_minutes": duration,
            "pages": 0,
            "time_slot": f"{start_time}-{end_time}",
            "planned_start_time": start_time,
            "planned_end_time": end_time,
            "completed": False,
            "is_fixed_time": True,
            "reason": f"اولویت: {activity.get('priority', 'متوسط')}"
        })
    
    return save_session_with_parts(user_id, parts, [], [], 0)

# ==================== تایمر ====================
active_timers = {}
timer_data = {}

async def update_timer(context: ContextTypes.DEFAULT_TYPE) -> None:
    job_data = context.job.data
    chat_id = job_data.get("chat_id")
    part_id = job_data.get("part_id")
    start_time = job_data.get("start_time")
    timer_message_id = job_data.get("timer_message_id")
    total_minutes = job_data.get("total_minutes", 0)
    elapsed_offset = job_data.get("elapsed_offset", 0)
    
    elapsed = elapsed_offset + int((datetime.now(IRAN_TZ) - start_time).total_seconds())
    minutes = elapsed // 60
    seconds = elapsed % 60
    
    result = execute_query(
        "SELECT title, planned_minutes, completed FROM study_parts WHERE part_id = %s",
        (part_id,), fetch=True
    )
    
    if not result:
        context.job.schedule_removal()
        active_timers.pop(part_id, None)
        timer_data.pop(part_id, None)
        return
    
    title, planned_minutes, completed = result
    
    if completed:
        context.job.schedule_removal()
        active_timers.pop(part_id, None)
        timer_data.pop(part_id, None)
        return
    
    if elapsed >= total_minutes * 60:
        context.job.schedule_removal()
        active_timers.pop(part_id, None)
        timer_data.pop(part_id, None)
        
        try:
            await context.bot.edit_message_text(
                f"✅ **تایمر {title} به پایان رسید!**\n\n"
                f"⏱ زمان: {total_minutes} دقیقه\n"
                f"🎯 هدف کامل شد!",
                chat_id=chat_id, message_id=timer_message_id,
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            logger.error(f"خطا در ارسال پیام پایان تایمر: {e}")
        return
    
    progress = min(100, int((elapsed / (total_minutes * 60)) * 100))
    bar_length = 20
    filled = int(bar_length * progress / 100)
    bar = "█" * filled + "░" * (bar_length - filled)
    
    remaining_seconds = (total_minutes * 60) - elapsed
    remaining_minutes = remaining_seconds // 60
    remaining_secs = remaining_seconds % 60
    
    message_text = f"⏱ **تایمر: {title}**\n\n"
    message_text += f"⏳ زمان سپری شده: {minutes:02d}:{seconds:02d}\n"
    message_text += f"⏳ زمان باقی‌مانده: {remaining_minutes:02d}:{remaining_secs:02d}\n"
    message_text += f"📊 پیشرفت: {progress}%\n"
    message_text += f"`{bar}`\n"
    message_text += f"🎯 هدف: {total_minutes} دقیقه"
    
    if remaining_minutes <= 2:
        message_text += f"\n\n⚠️ **{remaining_minutes} دقیقه تا پایان!**"
    
    try:
        if timer_message_id:
            await context.bot.edit_message_text(
                message_text, chat_id=chat_id,
                message_id=timer_message_id, parse_mode=ParseMode.HTML
            )
    except Exception as e:
        logger.error(f"خطا در آپدیت تایمر: {e}")

# ==================== پردازش تغییرات با AI واحد ====================

async def process_ai_plan_change(update: Update, context: ContextTypes.DEFAULT_TYPE, 
                                 user_text: str, ai_reply: str) -> bool:
    """پردازش هوشمند درخواست‌های کاربر
    
    Returns:
        True: اگر action غیر از chat بود و پیام نمایش داده شد
        False: اگر action = chat بود (پیام AI باید نمایش داده شود)
    """
    logger.info("🔍 وارد process_ai_plan_change شدیم")
    
    user_id = get_user_id_by_telegram(update.effective_user.id)
    if not user_id:
        return False
    
    today = get_today_date()
    user_data = get_user_data(str(update.effective_user.id))
    plan = get_plan_by_date(user_id, today)
    
    # آماده‌سازی خلاصه برنامه فعلی
    parts_summary = ""
    if plan and plan.get("parts"):
        for i, p in enumerate(plan["parts"], 1):
            status = "✅" if p.get("completed") else "⬜"
            parts_summary += f"{i}. {status} {p.get('title')} ({p.get('planned_minutes')}د) {p.get('time_slot', '')}\n"
    else:
        parts_summary = "هیچ برنامه‌ای وجود ندارد"
    
    system_prompt = f"""تو یک دستیار هوشمند مدیریت برنامه روزانه هستی که به کاربر در مدیریت زمان و بهره‌وری کمک می‌کنی.

=== برنامه فعلی امروز ===
{parts_summary}

=== درخواست کاربر ===
{user_text}

=== اطلاعات کاربر ===
هدف: {user_data.get('goal', 'نامشخص') if user_data else 'نامشخص'}
پایه: {user_data.get('grade', 'نامشخص') if user_data else 'نامشخص'}
رشته: {user_data.get('field', 'نامشخص') if user_data else 'نامشخص'}
درس‌های ضعیف: {", ".join(user_data.get('weak_subjects', [])) if user_data else 'ندارد'}
مدت تمرکز: {user_data.get('focus_duration', 45) if user_data else 45} دقیقه

=== وظیفه ===
با توجه به درخواست کاربر، **اول تشخیص بده که آیا درخواست اصلاً مربوط به برنامه است یا خیر**، سپس یکی از این کارها رو انجام بده:

### الف) درخواست‌هایی که **باید رد شوند** (action = chat):
اگر کاربر فقط:
- سلام و احوال‌پرسی می‌کند
- سوال عمومی می‌پرسد ("چطوری؟" ، "کجایی؟")
- درباره برنامه نظری می‌دهد بدون درخواست تغییر
- فقط از برنامه تشکر می‌کند
- درباره خودش حرف می‌زند بدون درخواست اضافه/حذف/تغییر

→ action = "chat" و هیچ تغییری اعمال نکن

### ب) درخواست‌های مربوط به برنامه (action‌های واقعی):

1. **build_new**: ساخت برنامه کاملاً جدید
   - "برنامه بساز"، "برنامه جدید"، "یه برنامه برام بچین"
   - **توجه:** فقط زمانی که کاربر واقعاً می‌خواهد **برنامه روزانه** بسازد

2. **add**: اضافه کردن پارت به برنامه موجود
   - "یه پارت ریاضی اضافه کن"، "شیمی رو به برنامه اضافه کن"
   - **مهم:** هر چیزی که کاربر می‌گوید اضافه کن، **لزوماً درس نیست**. می‌تواند:
     - ورزش، باشگاه (gym)، پیاده‌روی، شنا
     - استراحت، خواب، ناهار، شام
     - تفریح، بازی، فیلم دیدن، موسیقی
     - کارهای شخصی، خرید، دیدار دوستان
     - عبادت، مدیتیشن، یوگا
   
   در این موارد عنوان پارت را **همان چیزی که کاربر گفت** قرار بده، نه «مطالعه».
   مثال: "میخوام هر روز برم gym" → title = "باشگاه (Gym)"

3. **delete**: حذف یک پارت
   - "پارت ریاضی رو حذف کن"
   - می‌تواند هر پارتی باشد، نه فقط درس

4. **update**: تغییر یک پارت موجود
   - "زمان ریاضی رو عوض کن"
   - می‌تواند زمان ورزش، استراحت و... را تغییر دهد

5. **clear**: پاک کردن همه پارت‌ها

### ج) قوانین مهم:

- **هیچ پارت جدیدی را با موضوع درسی اجباری پر نکن.** اگر کاربر می‌گوید "gym"، عنوان پارت را "Gym" یا "باشگاه" بگذار، نه "ورزش - مرور کلی".
- اگر کاربر می‌گوید "می‌خوام هر روز برم gym"، منظور این است که می‌خواهد در برنامه امروز یک پارت با عنوان "باشگاه (Gym)" داشته باشد.
- اگر کاربر از کلماتی مثل "میخوام"، "قصد دارم"، "باید" استفاده کرد، این نشانه درخواست اضافه کردن است.
- اگر کاربر درخواست **غیرمرتبط با برنامه** داد (مثل سوال عمومی)، action = "chat" برگردان.

### د) نمونه‌ها:

| درخواست کاربر | action | title |
|--------------|--------|-------|
| "یه پارت ریاضی اضافه کن" | add | "ریاضی" |
| "میخوام برم gym" | add | "باشگاه (Gym)" |
| "شام رو یادم نندازی" | add | "شام" |
| "برای پیاده‌روی وقت بذار" | add | "پیاده‌روی" |
| "سلام چطوری" | chat | - |
| "برنامه‌م خوبه" | chat | - |
| "همه رو پاک کن" | clear | - |

=== خروجی JSON ===
{{
  "action": "build_new|add|delete|update|clear|chat",
  "subjects": [
    {{
      "subject": "عنوان فعالیت (می‌تواند درس، ورزش، استراحت، تفریح و... باشد)",
      "topic": "توضیح اضافی (اختیاری)",
      "duration": 45,
      "priority": "high|medium|low",
      "reason": "دلیل"
    }}
  ],
  "target": {{
    "title": "عنوان فعالیت",
    "current_title": "عنوان فعلی (برای update)",
    "duration": 45,
    "start_time": "09:00",
    "end_time": "09:45",
    "grade": 3
  }},
  "reason": "دلیل کلی تغییر",
  "message": "پیام توضیحی برای کاربر"
}}

⚠️ **در خروجی JSON، فیلد target را همیشه به عنوان یک آبجکت برگردان — حتی اگر خالی.**
هرگز target: null برنگردان. اگر اطلاعاتی نداری، از {{}} استفاده کن.

فقط JSON خالص برگردان.
"""
    
    response = await call_ai(system_prompt, max_tokens=1500, temperature=0.3)
    
    if not response:
        logger.warning("پاسخ AI خالی بود")
        return False
    
    try:
        json_match = re.search(r'\{.*\}', response, re.DOTALL)
        if not json_match:
            logger.warning("JSON در پاسخ پیدا نشد")
            return False
        interpretation = json.loads(json_match.group())
    except Exception as e:
        logger.error(f"❌ خطا در پارس JSON: {e}")
        return False
    
    action = interpretation.get("action", "chat")
    logger.info(f"🔍 action تشخیص داده شده: {action}")
    
    # ============================================
    # اگر فقط چت بود → False برگردان تا پیام AI نمایش داده شود
    # ============================================
    if action == "chat":
        return False
    
    # ============================================
    # امن‌سازی target - همیشه dict
    # ============================================
    target = interpretation.get("target") or {}
    reason = interpretation.get("reason", "")
    
    # ============================================
    # BUILD_NEW
    # ============================================
    if action == "build_new":
        subjects = interpretation.get("subjects", [])
        if not subjects:
            await update.message.reply_text(
                "❓ نتونستم برنامه‌ای طراحی کنم. لطفاً دقیق‌تر بگو."
            )
            return True
        
        context.user_data["pending_change"] = {
            "action": "build_new",
            "subjects": subjects,
            "reason": reason,
            "session_id": plan.get("session_id") if plan else None,
            "user_id": user_id,
            "has_existing": bool(plan and plan.get("parts")),
            "timestamp": datetime.now(IRAN_TZ).isoformat()
        }
        
        text_msg = "🆕 **برنامه پیشنهادی جدید**\n\n"
        total_min = 0
        for i, subj in enumerate(subjects, 1):
            duration = subj.get("duration", 45)
            total_min += duration
            priority_emoji = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(
                subj.get("priority", "medium"), "🟡"
            )
            text_msg += f"{i}. {priority_emoji} **{subj.get('subject', 'فعالیت')}**"
            if subj.get('topic'):
                text_msg += f" - {subj.get('topic')}"
            text_msg += f" ({duration} دقیقه)\n"
            if subj.get('reason'):
                text_msg += f"   💡 {subj.get('reason')}\n"
        
        text_msg += f"\n⏱ زمان کل: {format_time_hours_minutes(total_min)}\n"
        
        if plan and plan.get("parts"):
            text_msg += f"\n⚠️ **برنامه فعلی ({len(plan['parts'])} پارت) جایگزین می‌شود.**"
        
        text_msg += "\n\nآیا تایید می‌کنید؟"
        
        await update.message.reply_text(
            text_msg,
            reply_markup=get_confirm_change_keyboard(),
            parse_mode=ParseMode.HTML
        )
        return True
    
    # ============================================
    # چک: آیا برنامه‌ای برای امروز وجود دارد؟
    # ============================================
    if not plan or not plan.get("parts"):
        await update.message.reply_text(
            "📝 **برنامه‌ای برای امروز وجود ندارد.**\n\n"
            "برای ساخت برنامه، از دکمه <b>برنامه امروز</b> استفاده کن یا بگو «برنامه بساز».",
            reply_markup=get_main_keyboard(),
            parse_mode=ParseMode.HTML
        )
        return True
    
    parts = plan.get("parts", [])
    session_id = plan.get("session_id")
    
    # ============================================
    # امن‌سازی: اگر target خالی است ولی action نیاز دارد
    # ============================================
    if action in ["add", "delete", "update"] and not target:
        await update.message.reply_text(
            "❓ **درخواست شما نامشخص است.**\n\n"
            "لطفاً دقیق‌تر بگو:\n"
            "• کدام درس/فعالیت؟\n"
            "• چه تغییری؟\n\n"
            "مثال: «یه پارت ریاضی اضافه کن» یا «زمان فیزیک رو ۶۰ دقیقه کن»",
            parse_mode=ParseMode.HTML
        )
        return True
    
    pending_change = {
        "action": action,
        "target": target,
        "reason": reason,
        "session_id": session_id,
        "user_id": user_id,
        "parts": parts.copy(),
        "timestamp": datetime.now(IRAN_TZ).isoformat()
    }
    
    # ============================================
    # ADD
    # ============================================
    if action == "add":
        title = target.get("title") or "فعالیت"
        duration = target.get("duration", 45)
        start_time = target.get("start_time")
        end_time = target.get("end_time")
        grade = target.get("grade", 3)
        
        if not start_time or not end_time:
            # پیدا کردن بازه خالی
            used_slots = []
            for p in parts:
                if p.get("planned_start_time") and p.get("planned_end_time"):
                    used_slots.append((p["planned_start_time"], p["planned_end_time"]))
            
            available_slot = None
            for hour in range(8, 21):
                start = f"{hour:02d}:00"
                end = f"{(hour + 1):02d}:00"
                conflict = False
                for used_start, used_end in used_slots:
                    if start < used_end and end > used_start:
                        conflict = True
                        break
                if not conflict:
                    available_slot = (start, end)
                    break
            
            if available_slot:
                start_time, end_time = available_slot
                pending_change["target"]["start_time"] = start_time
                pending_change["target"]["end_time"] = end_time
            else:
                await update.message.reply_text(
                    "❌ بازه خالی برای اضافه کردن وجود ندارد.\n"
                    "لطفاً یک پارت را حذف یا تکمیل کن."
                )
                return True
        
        context.user_data["pending_change"] = pending_change
        
        grade_emoji = GRADE_RULES.get(grade, GRADE_RULES[3])["emoji"]
        grade_name = GRADE_RULES.get(grade, GRADE_RULES[3])["name"]
        
        text_msg = f"📋 **تغییر پیشنهادی: اضافه کردن پارت**\n\n"
        text_msg += f"📚 فعالیت: {title}\n"
        text_msg += f"⭐ درجه: {grade_name} {grade_emoji}\n"
        text_msg += f"⏱ مدت: {duration} دقیقه\n"
        text_msg += f"🕒 زمان: {start_time} - {end_time}\n"
        if reason:
            text_msg += f"💡 دلیل: {reason}\n"
        text_msg += "\nآیا تایید می‌کنید؟"
        
        await update.message.reply_text(
            text_msg,
            reply_markup=get_confirm_change_keyboard(),
            parse_mode=ParseMode.HTML
        )
        return True
    
    # ============================================
    # DELETE
    # ============================================
    elif action == "delete":
        current_title = target.get("title") or target.get("current_title") or ""
        
        if not current_title:
            await update.message.reply_text(
                "❓ کدام پارت را حذف کنم؟ لطفاً نامش را بگو."
            )
            return True
        
        target_part = None
        for p in parts:
            if p.get("title") == current_title:
                target_part = p
                break
        
        if not target_part:
            await update.message.reply_text(
                f"❌ پارت «{current_title}» در برنامه امروز پیدا نشد."
            )
            return True
        
        if target_part.get("completed"):
            await update.message.reply_text(
                f"❌ پارت «{current_title}» تکمیل شده و قابل حذف نیست."
            )
            return True
        
        pending_change["target_part"] = target_part
        context.user_data["pending_change"] = pending_change
        
        text_msg = f"⚠️ **حذف پارت**\n\n"
        text_msg += f"📚 {current_title}\n"
        text_msg += f"⏱ {target_part.get('planned_minutes', 0)} دقیقه\n"
        text_msg += f"🕒 {target_part.get('time_slot', 'نامشخص')}\n"
        if reason:
            text_msg += f"💡 دلیل: {reason}\n"
        text_msg += "\nمطمئنی؟"
        
        await update.message.reply_text(
            text_msg,
            reply_markup=get_confirm_delete_keyboard(),
            parse_mode=ParseMode.HTML
        )
        return True
    
    # ============================================
    # UPDATE
    # ============================================
    elif action == "update":
        current_title = target.get("current_title") or target.get("title") or ""
        
        if not current_title:
            await update.message.reply_text(
                "❓ کدام پارت را می‌خواهی تغییر بدهی؟ لطفاً نامش را بگو."
            )
            return True
        
        target_part = None
        for p in parts:
            if p.get("title") == current_title:
                target_part = p
                break
        
        if not target_part:
            await update.message.reply_text(
                f"❌ پارت «{current_title}» در برنامه امروز پیدا نشد."
            )
            return True
        
        updates = {}
        new_title = target.get("title")
        if new_title and new_title != current_title:
            updates["عنوان"] = f"{current_title} → {new_title}"
        
        new_duration = target.get("duration")
        if new_duration:
            updates["مدت"] = f"{target_part.get('planned_minutes', 0)} → {new_duration} دقیقه"
        
        new_start = target.get("start_time")
        new_end = target.get("end_time")
        if new_start and new_end:
            updates["زمان"] = f"{target_part.get('time_slot', 'نامشخص')} → {new_start}-{new_end}"
        
        new_grade = target.get("grade")
        if new_grade:
            old_grade = GRADE_RULES.get(target_part.get("grade", 3), GRADE_RULES[3])["name"]
            new_grade_name = GRADE_RULES.get(new_grade, GRADE_RULES[3])["name"]
            updates["درجه"] = f"{old_grade} → {new_grade_name}"
        
        if not updates:
            await update.message.reply_text(
                "❓ چه تغییری می‌خواهی اعمال کنم؟\n"
                "مثال: «مدت ریاضی رو ۶۰ دقیقه کن» یا «زمان فیزیک رو ۱۰ ببر»"
            )
            return True
        
        pending_change["target_part"] = target_part
        pending_change["updates"] = updates
        context.user_data["pending_change"] = pending_change
        
        text_msg = f"📝 **به‌روزرسانی پارت**\n\n"
        text_msg += f"📚 {current_title}\n\n"
        text_msg += "📋 تغییرات:\n"
        for key, value in updates.items():
            text_msg += f"• {key}: {value}\n"
        if reason:
            text_msg += f"\n💡 دلیل: {reason}\n"
        text_msg += "\nتایید می‌کنی؟"
        
        await update.message.reply_text(
            text_msg,
            reply_markup=get_confirm_change_keyboard(),
            parse_mode=ParseMode.HTML
        )
        return True
    
    # ============================================
    # CLEAR
    # ============================================
    elif action == "clear":
        pending_change["parts"] = parts.copy()
        context.user_data["pending_change"] = pending_change
        
        text_msg = f"⚠️ **پاک کردن همه پارت‌ها**\n\n"
        text_msg += f"📊 تعداد: {len(parts)}\n"
        if reason:
            text_msg += f"💡 دلیل: {reason}\n"
        text_msg += "\nمطمئنی؟"
        
        await update.message.reply_text(
            text_msg,
            reply_markup=get_confirm_clear_keyboard(),
            parse_mode=ParseMode.HTML
        )
        return True
    
    # حالت پیش‌فرض
    context.user_data.pop("pending_change", None)
    return False


async def apply_pending_change(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """اعمال تغییر تاییدشده"""
    pending = context.user_data.get("pending_change")
    if not pending:
        await update.message.reply_text("❌ هیچ تغییر در انتظار تاییدی وجود ندارد.")
        return
    
    action = pending.get("action")
    user_id = pending.get("user_id")
    today = get_today_date()
    
    try:
        # ============================================
        # BUILD_NEW: ساخت برنامه جدید
        # ============================================
        if action == "build_new":
            subjects = pending.get("subjects", [])
            if not subjects:
                await update.message.reply_text("❌ اطلاعات برنامه وجود ندارد.")
                context.user_data.pop("pending_change", None)
                return
            
            if pending.get("has_existing") and pending.get("session_id"):
                execute_query(
                    "UPDATE study_sessions SET archived = TRUE WHERE session_id = %s",
                    (pending["session_id"],)
                )
            
            user_data = get_user_data(str(update.effective_user.id))
            level = user_data.get('plan_level', 0) if user_data else 0
            if user_data:
                user_data['plan_level'] = level
            
            ai_response = {"subjects": subjects}
            session_id = create_plan_from_ai_response(user_id, user_data, ai_response)
            
            if session_id:
                new_plan = get_plan_by_date(user_id, today)
                if new_plan:
                    context.user_data["current_plan"] = new_plan
                    context.user_data.pop("pending_change", None)
                    
                    await update.message.reply_text(
                        f"✅ **برنامه جدید ساخته شد!**\n\n"
                        f"📊 تعداد: {len(new_plan['parts'])} پارت\n"
                        f"⏱ زمان کل: {format_time_hours_minutes(sum(p.get('planned_minutes', 0) for p in new_plan['parts']))}",
                        reply_markup=get_main_keyboard(),
                        parse_mode=ParseMode.HTML
                    )
                    await show_parts_initial(update, context, new_plan["parts"])
                    return
            else:
                await update.message.reply_text("❌ خطا در ساخت برنامه.")
                context.user_data.pop("pending_change", None)
                return
        
        target = pending.get("target") or {}
        session_id = pending.get("session_id")
        reason = pending.get("reason", "")
        
        # ============================================
        # ADD
        # ============================================
        if action == "add":
            title = target.get("title", "فعالیت")
            duration = target.get("duration", 45)
            start_time = target.get("start_time")
            end_time = target.get("end_time")
            grade = target.get("grade", 3)
            
            if not start_time or not end_time:
                plan = get_plan_by_date(user_id, today)
                parts = plan.get("parts", []) if plan else []
                
                used_slots = []
                for p in parts:
                    if p.get("planned_start_time") and p.get("planned_end_time"):
                        used_slots.append((p["planned_start_time"], p["planned_end_time"]))
                
                available_slot = None
                for hour in range(8, 21):
                    start = f"{hour:02d}:00"
                    end = f"{(hour + 1):02d}:00"
                    conflict = False
                    for used_start, used_end in used_slots:
                        if start < used_end and end > used_start:
                            conflict = True
                            break
                    if not conflict:
                        available_slot = (start, end)
                        break
                
                if available_slot:
                    start_time, end_time = available_slot
                else:
                    await update.message.reply_text("❌ بازه خالی برای اضافه کردن وجود ندارد.")
                    context.user_data.pop("pending_change", None)
                    return
            
            part_data = {
                "title": title, "grade": grade,
                "planned_minutes": duration,
                "time_slot": f"{start_time}-{end_time}",
                "planned_start_time": start_time,
                "planned_end_time": end_time,
                "is_fixed_time": True,
                "reason": f"اضافه شده توسط AI - {reason}" if reason else "اضافه شده توسط AI",
                "pages": 0
            }
            
            new_part_id = add_part_to_session(session_id, part_data)
            
            if new_part_id:
                save_change_history(user_id, session_id, new_part_id, "add", part_data)
                context.user_data.pop("pending_change", None)
                updated_plan = get_plan_by_date(user_id, today)
                if updated_plan:
                    context.user_data["current_plan"] = updated_plan
                    await update.message.reply_text(
                        f"✅ **پارت جدید اضافه شد!**\n\n"
                        f"📚 {title} | ⏱ {duration}د | 🕒 {start_time}-{end_time}",
                        reply_markup=get_main_keyboard(),
                        parse_mode=ParseMode.HTML
                    )
                return
        
        # ============================================
        # DELETE
        # ============================================
        elif action == "delete":
            target_part = pending.get("target_part")
            if not target_part:
                await update.message.reply_text("❌ اطلاعات پارت یافت نشد.")
                context.user_data.pop("pending_change", None)
                return
            
            part_id = target_part.get("part_id")
            title = target_part.get("title")
            
            previous_data = {k: v for k, v in target_part.items() if k not in ["part_id", "session_id"]}
            save_change_history(user_id, session_id, part_id, "delete", previous_data)
            
            execute_query("DELETE FROM study_parts WHERE part_id = %s", (part_id,))
            execute_query(
                "UPDATE study_sessions SET total_parts = total_parts - 1 WHERE session_id = %s",
                (session_id,)
            )
            
            context.user_data.pop("pending_change", None)
            updated_plan = get_plan_by_date(user_id, today)
            if updated_plan:
                context.user_data["current_plan"] = updated_plan
                await update.message.reply_text(
                    f"✅ **پارت '{title}' حذف شد!**\n\n"
                    f"🔙 برای برگشت، دکمه <b>🔙 برگشت به حالت قبل</b> رو بزن.",
                    reply_markup=get_main_keyboard(),
                    parse_mode=ParseMode.HTML
                )
            return
        
        # ============================================
        # UPDATE
        # ============================================
        elif action == "update":
            target_part = pending.get("target_part")
            updates = pending.get("updates", {})
            if not target_part:
                await update.message.reply_text("❌ اطلاعات پارت یافت نشد.")
                context.user_data.pop("pending_change", None)
                return
            
            part_id = target_part.get("part_id")
            current_title = target_part.get("title")
            
            previous_data = {k: v for k, v in target_part.items() if k not in ["part_id", "session_id"]}
            
            update_fields = {}
            if target.get("title") and target.get("title") != current_title:
                update_fields["title"] = target.get("title")
            if target.get("duration"):
                update_fields["planned_minutes"] = target.get("duration")
            if target.get("start_time") and target.get("end_time"):
                update_fields["planned_start_time"] = target.get("start_time")
                update_fields["planned_end_time"] = target.get("end_time")
                update_fields["time_slot"] = f"{target.get('start_time')}-{target.get('end_time')}"
                update_fields["is_fixed_time"] = True
            if target.get("grade"):
                update_fields["grade"] = target.get("grade")
            
            if update_fields:
                set_clause = ", ".join([f"{k} = %s" for k in update_fields.keys()])
                values = list(update_fields.values()) + [part_id]
                execute_query(f"UPDATE study_parts SET {set_clause} WHERE part_id = %s", tuple(values))
                save_change_history(user_id, session_id, part_id, "update", previous_data, update_fields)
            
            context.user_data.pop("pending_change", None)
            updated_plan = get_plan_by_date(user_id, today)
            if updated_plan:
                context.user_data["current_plan"] = updated_plan
                
                change_text = "\n".join([f"• {key}: {value}" for key, value in updates.items()])
                await update.message.reply_text(
                    f"✅ **پارت '{current_title}' تغییر کرد!**\n\n"
                    f"📝 تغییرات:\n{change_text}\n\n"
                    f"🔙 برای برگشت، دکمه <b>🔙 برگشت به حالت قبل</b> رو بزن.",
                    reply_markup=get_main_keyboard(),
                    parse_mode=ParseMode.HTML
                )
            return
        
        # ============================================
        # CLEAR
        # ============================================
        elif action == "clear":
            parts_data = []
            for p in pending.get("parts", []):
                parts_data.append({
                    "title": p.get("title"), "grade": p.get("grade"),
                    "planned_minutes": p.get("planned_minutes"),
                    "time_slot": p.get("time_slot"),
                    "planned_start_time": p.get("planned_start_time"),
                    "planned_end_time": p.get("planned_end_time"),
                    "pages": p.get("pages", 0),
                    "is_fixed_time": p.get("is_fixed_time", False),
                    "reason": p.get("reason", ""),
                    "part_number": p.get("part_number", 0)
                })
            
            save_change_history(user_id, session_id, None, "clear", 
                               {"count": len(parts_data)}, {}, {"parts": parts_data})
            
            execute_query("DELETE FROM study_parts WHERE session_id = %s", (session_id,))
            execute_query(
                "UPDATE study_sessions SET total_parts = 0, completed_parts = 0 WHERE session_id = %s",
                (session_id,)
            )
            
            context.user_data.pop("pending_change", None)
            updated_plan = get_plan_by_date(user_id, today)
            if updated_plan:
                context.user_data["current_plan"] = updated_plan
            
            await update.message.reply_text(
                "🗑 **همه پارت‌ها پاک شدند!**\n\n"
                "🔙 برای برگشت، دکمه <b>🔙 برگشت به حالت قبل</b> رو بزن.",
                reply_markup=get_main_keyboard(),
                parse_mode=ParseMode.HTML
            )
            return
        
        await update.message.reply_text("❌ خطا در اعمال تغییرات.")
        context.user_data.pop("pending_change", None)
        
    except Exception as e:
        logger.error(f"خطا در apply_pending_change: {e}")
        await update.message.reply_text(f"❌ خطا در اعمال تغییرات: {str(e)[:100]}")
        context.user_data.pop("pending_change", None)


async def reject_pending_change(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """رد تغییر پیشنهادی"""
    pending = context.user_data.get("pending_change")
    if not pending:
        await update.message.reply_text("❌ هیچ تغییر در انتظار تاییدی وجود ندارد.")
        return
    
    action = pending.get("action")
    action_names = {
        "add": "اضافه کردن", "delete": "حذف",
        "update": "به‌روزرسانی", "clear": "پاک کردن همه",
        "build_new": "ساخت برنامه جدید"
    }
    action_name = action_names.get(action, "تغییر")
    
    context.user_data.pop("pending_change", None)
    
    await update.message.reply_text(
        f"❌ **{action_name} لغو شد.**\n\nتغییری در برنامه اعمال نشد.",
        reply_markup=get_main_keyboard()
    )

# ==================== تایمرها و آپدیت پارت‌ها ====================

def update_part_times_and_shift_remaining(session_id: int, completed_part_id: int, actual_end_time: datetime) -> None:
    conn = None
    cursor = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT planned_start_time, planned_end_time, planned_minutes, is_fixed_time, part_number
            FROM study_parts WHERE part_id = %s
        """, (completed_part_id,))
        part_info = cursor.fetchone()
        if not part_info:
            return
        
        planned_end = part_info[1]
        planned_minutes = part_info[2]
        part_number = part_info[4]
        
        if planned_end:
            planned_end_time = datetime.combine(actual_end_time.date(), planned_end)
            if planned_end_time.tzinfo is None:
                planned_end_time = IRAN_TZ.localize(planned_end_time)
            
            delay = int((actual_end_time - planned_end_time).total_seconds() / 60)
        else:
            delay = 0
        
        cursor.execute("""
            UPDATE study_parts
            SET actual_end_time = %s, actual_minutes = %s, completed = TRUE, delay_minutes = %s
            WHERE part_id = %s
        """, (actual_end_time, planned_minutes, delay, completed_part_id))
        
        cursor.execute("""
            SELECT part_id, planned_start_time, planned_end_time, is_fixed_time, planned_minutes, part_number
            FROM study_parts
            WHERE session_id = %s AND part_number > %s AND completed = FALSE
            ORDER BY part_number
        """, (session_id, part_number))
        
        next_parts = cursor.fetchall()
        
        if not next_parts:
            conn.commit()
            return
        
        current_time = actual_end_time
        
        for next_part in next_parts:
            next_part_id = next_part[0]
            next_duration = next_part[4]
            
            new_start = current_time
            new_end = current_time + timedelta(minutes=next_duration)
            
            cursor.execute("""
                UPDATE study_parts
                SET planned_start_time = %s, planned_end_time = %s,
                    time_slot = %s, delay_minutes = delay_minutes + %s
                WHERE part_id = %s
            """, (
                new_start.strftime("%H:%M"), new_end.strftime("%H:%M"),
                f"{new_start.strftime('%H:%M')}-{new_end.strftime('%H:%M')}",
                delay, next_part_id
            ))
            
            current_time = new_end
        
        conn.commit()
    except Exception as e:
        logger.error(f"❌ خطا در update_part_times: {e}")
        if conn: conn.rollback()
    finally:
        if cursor: cursor.close()
        if conn: return_connection(conn)
# ==================== هندلرهای اصلی ====================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    telegram_id = str(user.id)
    user_data = get_user_data(telegram_id)
    
    if user_data and user_data.get("is_onboarded"):
        level = user_data.get('plan_level', 0)
        level_name = get_plan_level_name(level)
        level_emoji = get_plan_level_emoji(level)
        
        if not get_user_quota(user_data["id"]):
            init_user_quota(user_data["id"])
        
        await update.message.reply_text(
            f"🎯 سلام {user.full_name}! به کمپ خوش آمدید.\n\n"
            f"📚 امروز {get_today_shamsi()} - ساعت {get_iran_time_str()}\n"
            f"📊 سطح برنامه: {level_emoji} {level_name}\n"
            f"💬 پیام‌های باقی‌مانده AI: {get_remaining_messages(user_data['id'])}\n\n"
            "برای شروع، دکمه‌های منو رو بزن.",
            reply_markup=get_main_keyboard(),
            parse_mode=ParseMode.HTML
        )
        return
    
    context.user_data["onboarding_step"] = 0
    context.user_data["onboarding_data"] = {
        "telegram_id": telegram_id,
        "username": user.username,
        "full_name": user.full_name
    }
    
    await update.message.reply_text(
        "👋 سلام! به ربات هوشمند مطالعه خوش اومدی!\n\n"
        "📋 لطفاً به سوالات زیر جواب بده:\n\n"
        "❓ هدف اصلی‌ات از مطالعه چیه؟\n"
        "[کنکور] [معدل] [تقویت پایه] [✏️ سایر]",
        reply_markup=ReplyKeyboardMarkup(
            [["کنکور"], ["معدل"], ["تقویت پایه"], ["✏️ سایر"]],
            resize_keyboard=True, one_time_keyboard=True
        )
    )

async def onboarding_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.message.text.strip()
    step = context.user_data.get("onboarding_step", 0)
    data = context.user_data.get("onboarding_data", {})
    
    if step == 0:
        if text == "✏️ سایر":
            await update.message.reply_text("✏️ لطفاً هدف خودت رو بنویس:")
            context.user_data["awaiting_custom"] = "goal"
            return
        data["goal"] = text
        context.user_data["onboarding_step"] = 1
        await update.message.reply_text(
            "❓ پایه تحصیلی‌ات چیه؟\n[دهم] [یازدهم] [دوازدهم] [دانشجو] [✏️ سایر]",
            reply_markup=ReplyKeyboardMarkup(
                [["دهم"], ["یازدهم"], ["دوازدهم"], ["دانشجو"], ["✏️ سایر"]],
                resize_keyboard=True, one_time_keyboard=True
            )
        )
    elif step == 1:
        if text == "✏️ سایر":
            await update.message.reply_text("✏️ لطفاً پایه خودت رو بنویس:")
            context.user_data["awaiting_custom"] = "grade"
            return
        data["grade"] = text
        context.user_data["onboarding_step"] = 2
        await update.message.reply_text(
            "❓ رشته‌ات چیه؟\n[ریاضی] [تجربی] [انسانی] [سایر] [✏️ سایر]",
            reply_markup=ReplyKeyboardMarkup(
                [["ریاضی"], ["تجربی"], ["انسانی"], ["سایر"], ["✏️ سایر"]],
                resize_keyboard=True, one_time_keyboard=True
            )
        )
    elif step == 2:
        if text == "✏️ سایر":
            await update.message.reply_text("✏️ لطفاً رشته خودت رو بنویس:")
            context.user_data["awaiting_custom"] = "field"
            return
        data["field"] = text
        context.user_data["onboarding_step"] = 3
        await update.message.reply_text(
            "❓ تاریخ کنکور یا آزمون مهم رو بگو (مثلاً 1404/04/15):\n(اگر ندارید، 'ندارم' رو بزنید)",
            reply_markup=ReplyKeyboardMarkup([["ندارم"]], resize_keyboard=True, one_time_keyboard=True)
        )
    elif step == 3:
        if text != "ندارم":
            try:
                parts = text.split("/")
                if len(parts) == 3:
                    year, month, day = map(int, parts)
                    jdate = jdatetime.date(year, month, day)
                    data["exam_date"] = jdate.togregorian().strftime("%Y-%m-%d")
            except:
                data["exam_date"] = None
        else:
            data["exam_date"] = None
        context.user_data["onboarding_step"] = 4
        await update.message.reply_text(
            "❓ بهترین زمان مطالعه‌ت کیه؟\n[صبح] [عصر] [شب]",
            reply_markup=ReplyKeyboardMarkup(
                [["صبح"], ["عصر"], ["شب"]],
                resize_keyboard=True, one_time_keyboard=True
            )
        )
    elif step == 4:
        data["peak_time"] = text
        context.user_data["onboarding_step"] = 5
        await update.message.reply_text(
            "❓ درس‌هایی که ضعیفی رو بگو (مثلاً: ریاضی، فیزیک):",
            reply_markup=ReplyKeyboardMarkup([["رد کردن"]], resize_keyboard=True, one_time_keyboard=True)
        )
    elif step == 5:
        if text != "رد کردن":
            data["weak_subjects"] = [s.strip() for s in text.split("،") if s.strip()]
        else:
            data["weak_subjects"] = []
        context.user_data["onboarding_step"] = 6
        await update.message.reply_text(
            "❓ چقدر می‌تونی تمرکز کنی؟\n[۲۰ دقیقه] [۳۰ دقیقه] [۴۵ دقیقه] [۶۰ دقیقه] [۹۰ دقیقه]",
            reply_markup=ReplyKeyboardMarkup(
                [["۲۰ دقیقه"], ["۳۰ دقیقه"], ["۴۵ دقیقه"], ["۶۰ دقیقه"], ["۹۰ دقیقه"]],
                resize_keyboard=True, one_time_keyboard=True
            )
        )
    elif step == 6:
        try:
            focus = int(text.replace("دقیقه", "").strip())
            data["focus_duration"] = focus
        except:
            data["focus_duration"] = 45
        
        data["is_onboarded"] = True
        data["plan_level"] = 0
        
        user_id = save_user(data)
        
        if user_id:
            init_user_quota(user_id)
            await update.message.reply_text(
                "✅ **ثبت‌نام شما با موفقیت انجام شد!**\n\n"
                f"📚 هدف: {data.get('goal')}\n"
                f"🎓 پایه: {data.get('grade')}\n"
                f"🧪 رشته: {data.get('field')}\n"
                f"🌱 سطح برنامه: اولیه\n"
                f"💬 ۱۰ پیام رایگان AI برای آزمایش\n\n"
                "🧠 در حال ساخت برنامه اولیه...",
                reply_markup=get_main_keyboard(),
                parse_mode=ParseMode.HTML
            )
            await generate_initial_plan(update, context, user_id, data)
        else:
            await update.message.reply_text(
                "❌ خطا در ثبت اطلاعات. لطفاً دوباره /start رو بزن.",
                reply_markup=get_main_keyboard()
            )

async def generate_initial_plan(update: Update, context: ContextTypes.DEFAULT_TYPE, 
                                user_id: int, user_data: Dict) -> None:
    level = calculate_plan_level(user_id)
    user_data['plan_level'] = level
    update_user_plan_level(user_id, level)
    
    wait_msg = await update.message.reply_text("🧠 در حال ساخت برنامه شخصی‌سازی‌شده...")
    ai_response = await generate_plan_with_ai(user_id, user_data)
    await wait_msg.delete()
    
    if ai_response and ai_response.get('subjects'):
        session_id = create_plan_from_ai_response(user_id, user_data, ai_response)
        if session_id:
            plan = get_plan_by_date(user_id, get_today_date())
            if plan:
                context.user_data["current_plan"] = plan
                await show_parts_initial(update, context, plan["parts"])
                return
    
    await update.message.reply_text(
        "📝 **برنامه‌ای برای امروز وجود ندارد.**\n\nچگونه می‌خواهید برنامه امروز را بسازید؟",
        reply_markup=get_build_plan_keyboard(),
        parse_mode=ParseMode.HTML
    )

# ==================== نمایش پارت‌ها ====================

async def show_parts_initial(update: Update, context: ContextTypes.DEFAULT_TYPE, parts: List[Dict]) -> None:
    if not parts:
        await update.message.reply_text("❌ هیچ پارتی وجود ندارد.", reply_markup=get_main_keyboard())
        return
    
    user_id = get_user_id_by_telegram(update.effective_user.id)
    user_data = get_user_data(str(update.effective_user.id)) if user_id else None
    level = user_data.get('plan_level', 0) if user_data else 0
    level_name = get_plan_level_name(level)
    level_emoji = get_plan_level_emoji(level)
    
    text = f"📋 **برنامه پیشنهادی** {level_emoji} سطح {level_name}\n\n"
    text += f"📊 تعداد پارت‌ها: {len(parts)}\n"
    text += f"⏱ زمان کل: {format_time_hours_minutes(sum(p['planned_minutes'] for p in parts))}\n\n"
    
    for part in sorted(parts, key=lambda x: x.get("part_number", 0)):
        grade_emoji = GRADE_RULES.get(part.get("grade", 3), GRADE_RULES[3])["emoji"]
        planned_start = part.get("planned_start_time") or part.get("planned_start") or ""
        planned_end = part.get("planned_end_time") or part.get("planned_end") or ""
        time_info = f" {planned_start}-{planned_end}" if planned_start and planned_end else (
            f" {part['time_slot']}" if part.get("time_slot") else "")
        text += f"{part['part_number']}. ⬜ {grade_emoji} {part['title']} ({part['planned_minutes']}د){time_info} ↕️\n"
    
    text += "\n🔧 **مرحله اول: تنظیم ترتیب پارت‌ها**\n"
    text += "• با زدن دکمه <b>↕️</b> کنار هر پارت، آن پارت یک ردیف بالا می‌رود\n"
    text += "• بعد از رضایت، دکمه <b>تایید برنامه</b> رو بزن"
    
    await update.message.reply_text(text, reply_markup=get_part_buttons_initial(parts), parse_mode=ParseMode.HTML)

async def show_parts_final(update: Update, context: ContextTypes.DEFAULT_TYPE, parts: List[Dict], show_date: bool = False) -> None:
    if not parts:
        await update.message.reply_text("📭 هیچ پارتی وجود ندارد.", reply_markup=get_main_keyboard())
        return
    
    sorted_parts = sorted(parts, key=lambda x: x.get("part_number", 0))
    
    user_id = get_user_id_by_telegram(update.effective_user.id)
    user_data = get_user_data(str(update.effective_user.id)) if user_id else None
    level = user_data.get('plan_level', 0) if user_data else 0
    level_name = get_plan_level_name(level)
    level_emoji = get_plan_level_emoji(level)
    
    text = f"📋 برنامه نهایی {level_emoji} سطح {level_name}\n\n"
    
    if show_date:
        date_str = context.user_data.get("selected_date") or get_today_date()
        shamsi = get_shamsi_date(date_str)
        text = f"📋 برنامه {shamsi} {level_emoji} سطح {level_name}\n\n"
    
    total_parts = len(sorted_parts)
    completed_parts = sum(1 for p in sorted_parts if p.get("completed", False))
    total_minutes = sum(p.get("planned_minutes", 0) for p in sorted_parts)
    
    text += f"📊 تعداد: {total_parts} | ✅ انجام: {completed_parts} | ⬜ باقی: {total_parts - completed_parts}\n"
    text += f"⏱ زمان کل: {format_time_hours_minutes(total_minutes)}\n\n"
    
    for part in sorted_parts:
        status = "✅" if part.get("completed", False) else "⬜"
        grade_emoji = GRADE_RULES.get(part.get("grade", 3), GRADE_RULES[3])["emoji"]
        planned_start = part.get("planned_start_time") or part.get("planned_start") or ""
        planned_end = part.get("planned_end_time") or part.get("planned_end") or ""
        time_info = f" {planned_start}-{planned_end}" if planned_start and planned_end else (
            f" {part['time_slot']}" if part.get("time_slot") else "")
        actual_info = f" (واقعی: {part['actual_minutes']}د)" if part.get("completed") and part.get("actual_minutes", 0) > 0 else ""
        reason = f" 📝 {part.get('reason', '')}" if part.get('reason') else ""
        text += f"{part.get('part_number', 0)}. {status} {grade_emoji} {part['title']} ({part.get('planned_minutes', 0)}د){time_info}{actual_info}{reason}\n"
    
    text += "\n⏰ روی هر پارت کلیک کن تا عملیات نمایش داده شود"
    
    last_change = get_last_change(user_id) if user_id else None
    if last_change:
        text += f"\n\n🔙 یک تغییر قابل برگشت وجود دارد: {last_change['action_type']}"
    
    await update.message.reply_text(text, reply_markup=get_part_buttons_final(sorted_parts, show_date), parse_mode=ParseMode.HTML)

async def show_part_detail(update: Update, context: ContextTypes.DEFAULT_TYPE, part_id: int) -> None:
    plan = context.user_data.get("current_plan", {})
    parts = plan.get("parts", [])
    part = next((p for p in parts if p.get("part_id") == part_id), None)
    
    if not part:
        db_result = execute_query(
            """SELECT part_id, part_number, title, grade, planned_minutes, actual_minutes,
                      time_slot, completed, pages, planned_start_time, planned_end_time,
                      is_fixed_time, delay_minutes, reason
               FROM study_parts WHERE part_id = %s""",
            (part_id,), fetch=True
        )
        if not db_result:
            await update.message.reply_text("❌ پارت یافت نشد.")
            return
        planned_start = db_result[9]
        planned_end = db_result[10]
        if planned_start and hasattr(planned_start, 'strftime'):
            planned_start = planned_start.strftime('%H:%M')
        if planned_end and hasattr(planned_end, 'strftime'):
            planned_end = planned_end.strftime('%H:%M')
        part = {
            "part_id": db_result[0], "part_number": db_result[1], "title": db_result[2],
            "grade": db_result[3], "planned_minutes": db_result[4], "actual_minutes": db_result[5] or 0,
            "time_slot": db_result[6] or "", "completed": db_result[7], "pages": db_result[8] or 0,
            "planned_start_time": planned_start or "", "planned_end_time": planned_end or "",
            "planned_start": planned_start or "", "planned_end": planned_end or "",
            "is_fixed_time": db_result[11] or False, "delay_minutes": db_result[12] or 0,
            "reason": db_result[13] or ""
        }
        if not any(p.get("part_id") == part_id for p in parts):
            parts.append(part)
            plan["parts"] = parts
            context.user_data["current_plan"] = plan
    
    grade_info = GRADE_RULES.get(part.get("grade", 3), GRADE_RULES[3])
    
    if part.get("completed"):
        text = f"✅ <b>{part['title']}</b> (انجام شده)\n\n"
        text += f"⭐ {grade_info['name']} {grade_info['emoji']}\n"
        text += f"⏱ برنامه: {part['planned_minutes']}د | واقعی: {part.get('actual_minutes', part['planned_minutes'])}د\n"
        if part.get("planned_start") and part.get("planned_end"):
            text += f"🕒 {part['planned_start']} - {part['planned_end']}\n"
        await update.message.reply_text(text, parse_mode=ParseMode.HTML)
        return
    
    text = f"📖 <b>{part['title']}</b>\n\n"
    text += f"⭐ {grade_info['name']} {grade_info['emoji']}\n"
    text += f"⏱ زمان: {part['planned_minutes']} دقیقه\n"
    if part.get("planned_start") and part.get("planned_end"):
        text += f"🕒 {part['planned_start']} - {part['planned_end']}\n"
    if part.get("is_fixed_time"):
        text += "🔒 زمان ثابت\n"
    if part.get("reason"):
        text += f"📝 {part['reason']}\n"
    text += "✅ وضعیت: در انتظار ⬜\n"
    
    context.user_data["active_part"] = part_id
    is_running = part_id in active_timers
    elapsed = timer_data.get(part_id, {}).get("elapsed_offset", 0)
    
    await update.message.reply_text(text, reply_markup=get_part_detail_buttons(part_id, is_running, elapsed), parse_mode=ParseMode.HTML)

# ==================== مدیریت دکمه‌های پارت ====================

async def handle_part_click(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.message.text.strip()
    user_id = get_user_id_by_telegram(update.effective_user.id)
    if not user_id:
        await update.message.reply_text("❌ لطفاً اول /start رو بزن.")
        return
    
    plan = context.user_data.get("current_plan", {})
    parts = plan.get("parts", [])
    is_confirmed = plan.get("confirmed", False)
    is_edit_mode = context.user_data.get("edit_mode", False)
    
    if "[" in text and "]" in text:
        id_match = re.search(r'\[(\d+)\]', text)
        if id_match:
            part_id = int(id_match.group(1))
            found_part = next((p for p in parts if p.get("part_id") == part_id), None)
            if not found_part:
                await update.message.reply_text("❌ پارت یافت نشد.")
                return
            
            if is_edit_mode:
                previous_data = {k: v for k, v in found_part.items()}
                await move_part_up(update, context, part_id)
                new_part = next((p for p in parts if p.get("part_id") == part_id), None)
                if new_part:
                    save_change_history(user_id, plan.get("session_id"), part_id, "move", previous_data, new_part)
                return
            
            if is_confirmed:
                await show_part_detail(update, context, part_id)
                return
            
            await move_part_up(update, context, part_id)
            return

async def move_part_up(update: Update, context: ContextTypes.DEFAULT_TYPE, part_id: int) -> None:
    plan = context.user_data.get("current_plan", {})
    parts = plan.get("parts", [])
    
    index = next((i for i, p in enumerate(parts) if p["part_id"] == part_id), None)
    if index is None:
        await update.message.reply_text("❌ پارت یافت نشد.")
        return
    
    if index > 0:
        parts[index], parts[index-1] = parts[index-1], parts[index]
        for i, p in enumerate(parts):
            p["part_number"] = i + 1
        
        current_time = 8 * 60
        for p in sorted(parts, key=lambda x: x.get("part_number", 0)):
            duration = p["planned_minutes"]
            start_h = current_time // 60
            start_m = current_time % 60
            end_time = current_time + duration
            end_h = end_time // 60
            end_m = end_time % 60
            p["planned_start_time"] = f"{start_h:02d}:{start_m:02d}"
            p["planned_end_time"] = f"{end_h:02d}:{end_m:02d}"
            p["time_slot"] = f"{p['planned_start_time']}-{p['planned_end_time']}"
            current_time = end_time
        
        for p in parts:
            execute_query(
                """UPDATE study_parts SET part_number = %s, planned_start_time = %s,
                   planned_end_time = %s, time_slot = %s WHERE part_id = %s""",
                (p["part_number"], p["planned_start_time"], p["planned_end_time"], p["time_slot"], p["part_id"])
            )
        
        plan["parts"] = parts
        context.user_data["current_plan"] = plan
        
        await update.message.reply_text(f"⬆️ {parts[index]['title']} یک ردیف بالا رفت!")
        
        if plan.get("confirmed", False):
            await show_parts_final(update, context, parts)
        else:
            await show_parts_initial(update, context, parts)
    else:
        await update.message.reply_text("❌ این پارت در بالاترین ردیف است.")

# ==================== هندلر Undo ====================

async def handle_undo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = get_user_id_by_telegram(update.effective_user.id)
    if not user_id:
        await update.message.reply_text("❌ لطفاً اول /start رو بزن.")
        return
    
    last_change = get_last_change(user_id)
    if not last_change:
        await update.message.reply_text("❌ هیچ تغییری برای برگشت وجود ندارد.")
        return
    
    action_type = last_change.get("action_type")
    session_id = last_change.get("session_id")
    part_id = last_change.get("part_id")
    previous_data = last_change.get("previous_data", {})
    extra_data = last_change.get("extra_data", {})
    change_id = last_change.get("id")
    today = get_today_date()
    
    try:
        if action_type == "delete":
            if not previous_data:
                await update.message.reply_text("❌ داده کافی برای برگشت وجود ندارد.")
                return
            part_data = {
                "title": previous_data.get("title", "بدون عنوان"),
                "grade": previous_data.get("grade", 3),
                "planned_minutes": previous_data.get("planned_minutes", 45),
                "time_slot": previous_data.get("time_slot", ""),
                "planned_start_time": previous_data.get("planned_start_time"),
                "planned_end_time": previous_data.get("planned_end_time"),
                "pages": previous_data.get("pages", 0),
                "is_fixed_time": previous_data.get("is_fixed_time", False),
                "reason": previous_data.get("reason", "بازیابی شده")
            }
            new_part_id = add_part_to_session(session_id, part_data)
            if new_part_id:
                execute_query("UPDATE study_sessions SET total_parts = total_parts + 1 WHERE session_id = %s", (session_id,))
                revert_change(change_id)
                plan = get_plan_by_date(user_id, today)
                if plan:
                    context.user_data["current_plan"] = plan
                    await update.message.reply_text(f"✅ پارت '{part_data['title']}' بازیابی شد!")
                    await show_parts_final(update, context, plan["parts"])
                    return
        
        elif action_type == "add":
            if not part_id:
                await update.message.reply_text("❌ اطلاعات پارت وجود ندارد.")
                return
            check = execute_query("SELECT completed, title FROM study_parts WHERE part_id = %s", (part_id,), fetch=True)
            if not check:
                await update.message.reply_text("❌ پارت قبلاً حذف شده.")
                return
            if check[0]:
                await update.message.reply_text(f"❌ پارت '{check[1]}' تکمیل شده و قابل حذف نیست.")
                return
            title = check[1]
            execute_query("DELETE FROM study_parts WHERE part_id = %s", (part_id,))
            execute_query("UPDATE study_sessions SET total_parts = total_parts - 1 WHERE session_id = %s", (session_id,))
            revert_change(change_id)
            plan = get_plan_by_date(user_id, today)
            if plan:
                context.user_data["current_plan"] = plan
                await update.message.reply_text(f"✅ پارت '{title}' حذف شد!")
                await show_parts_final(update, context, plan["parts"])
                return
        
        elif action_type == "update":
            if not part_id or not previous_data:
                await update.message.reply_text("❌ داده کافی وجود ندارد.")
                return
            check = execute_query("SELECT part_id FROM study_parts WHERE part_id = %s", (part_id,), fetch=True)
            if not check:
                await update.message.reply_text("❌ پارت وجود ندارد.")
                return
            
            update_fields = []
            values = []
            for key, value in previous_data.items():
                if key not in ["part_id", "session_id", "completed", "actual_minutes"]:
                    update_fields.append(f"{key} = %s")
                    values.append(value)
            
            if update_fields:
                values.append(part_id)
                execute_query(f"UPDATE study_parts SET {', '.join(update_fields)} WHERE part_id = %s", tuple(values))
            
            revert_change(change_id)
            plan = get_plan_by_date(user_id, today)
            if plan:
                context.user_data["current_plan"] = plan
                await update.message.reply_text("✅ تغییرات برگشت داده شد!")
                await show_parts_final(update, context, plan["parts"])
                return
        
        elif action_type == "complete":
            if not part_id:
                await update.message.reply_text("❌ اطلاعات پارت وجود ندارد.")
                return
            check = execute_query("SELECT completed, title FROM study_parts WHERE part_id = %s", (part_id,), fetch=True)
            if not check:
                await update.message.reply_text("❌ پارت وجود ندارد.")
                return
            completed, title = check
            if not completed:
                await update.message.reply_text(f"⚠️ پارت '{title}' ناتمام است.")
                return
            
            execute_query(
                """UPDATE study_parts SET completed = FALSE, actual_minutes = 0,
                   actual_end_time = NULL, completed_at = NULL WHERE part_id = %s""",
                (part_id,)
            )
            execute_query(
                """UPDATE study_sessions SET completed_parts = (
                    SELECT COUNT(*) FROM study_parts WHERE session_id = %s AND completed = TRUE
                ) WHERE session_id = %s""",
                (session_id, session_id)
            )
            execute_query(
                "DELETE FROM activity_log WHERE part_order = (SELECT part_number FROM study_parts WHERE part_id = %s) AND date = %s",
                (part_id, today)
            )
            revert_change(change_id)
            plan = get_plan_by_date(user_id, today)
            if plan:
                context.user_data["current_plan"] = plan
                await update.message.reply_text(f"✅ تکمیل پارت '{title}' برگشت داده شد!")
                await show_parts_final(update, context, plan["parts"])
                return
        
        elif action_type == "move":
            if not part_id or not previous_data:
                await update.message.reply_text("❌ داده کافی وجود ندارد.")
                return
            part_number = previous_data.get("part_number")
            if part_number is not None:
                execute_query("UPDATE study_parts SET part_number = %s WHERE part_id = %s", (part_number, part_id))
            
            planned_start = previous_data.get("planned_start_time")
            planned_end = previous_data.get("planned_end_time")
            time_slot = previous_data.get("time_slot")
            if planned_start and planned_end:
                execute_query(
                    """UPDATE study_parts SET planned_start_time = %s,
                       planned_end_time = %s, time_slot = %s WHERE part_id = %s""",
                    (planned_start, planned_end, time_slot, part_id)
                )
            revert_change(change_id)
            plan = get_plan_by_date(user_id, today)
            if plan:
                context.user_data["current_plan"] = plan
                await update.message.reply_text("✅ جابه‌جایی برگشت داده شد!")
                await show_parts_final(update, context, plan["parts"])
                return
        
        elif action_type == "clear":
            parts = extra_data.get("parts", [])
            if not parts:
                await update.message.reply_text("❌ اطلاعاتی برای بازیابی وجود ندارد.")
                return
            
            restored_count = 0
            for part in parts:
                part_data = {
                    "title": part.get("title", "بدون عنوان"),
                    "grade": part.get("grade", 3),
                    "planned_minutes": part.get("planned_minutes", 45),
                    "time_slot": part.get("time_slot", ""),
                    "planned_start_time": part.get("planned_start_time"),
                    "planned_end_time": part.get("planned_end_time"),
                    "pages": part.get("pages", 0),
                    "is_fixed_time": part.get("is_fixed_time", False),
                    "reason": part.get("reason", "")
                }
                new_part_id = add_part_to_session(session_id, part_data)
                if new_part_id:
                    restored_count += 1
            
            if restored_count > 0:
                revert_change(change_id)
                plan = get_plan_by_date(user_id, today)
                if plan:
                    context.user_data["current_plan"] = plan
                    await update.message.reply_text(f"✅ {restored_count} پارت بازیابی شد!")
                    await show_parts_final(update, context, plan["parts"])
                    return
        
        await update.message.reply_text("❌ خطا در برگشت تغییرات.")
    except Exception as e:
        logger.error(f"خطا در Undo: {e}")
        await update.message.reply_text(f"❌ خطا: {str(e)[:100]}")

# ==================== تایید برنامه ====================

async def confirm_plan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    plan = context.user_data.get("current_plan", {})
    parts = plan.get("parts", [])
    session_id = plan.get("session_id")
    
    if not parts:
        await update.message.reply_text("❌ برنامه‌ای وجود ندارد.")
        return
    
    if session_id:
        confirm_session(session_id)
    
    plan["confirmed"] = True
    context.user_data["current_plan"] = plan
    context.user_data["edit_mode"] = False
    
    user_id = get_user_id_by_telegram(update.effective_user.id)
    if user_id:
        level = calculate_plan_level(user_id)
        update_user_plan_level(user_id, level)
    
    level = plan.get("plan_level", 0)
    level_name = get_plan_level_name(level)
    level_emoji = get_plan_level_emoji(level)
    
    await update.message.reply_text(
        f"✅ <b>برنامه تایید شد!</b> {level_emoji} سطح {level_name}",
        parse_mode=ParseMode.HTML
    )
    await show_parts_final(update, context, parts)

async def handle_finish_plan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    plan = context.user_data.get("current_plan", {})
    parts = plan.get("parts", [])
    session_id = plan.get("session_id")
    
    if not parts:
        await update.message.reply_text("❌ برنامه‌ای وجود ندارد.")
        return
    
    completed_parts = [p for p in parts if p.get("completed", False)]
    incomplete_parts = [p for p in parts if not p.get("completed", False)]
    
    if session_id:
        finish_session(session_id)
    
    user_id = get_user_id_by_telegram(update.effective_user.id)
    if user_id:
        level = calculate_plan_level(user_id)
        update_user_plan_level(user_id, level)
    
    text = f"📅 برنامه امروز به پایان رسید!\n\n"
    text += f"📊 پیشرفت: {len(completed_parts)}/{len(parts)}\n\n"
    
    if incomplete_parts:
        text += "📋 انجام نشده:\n"
        for part in incomplete_parts[:5]:
            text += f"⬜ {part['title']} ({part.get('planned_minutes', 0)}د)\n"
    
    context.user_data.pop("current_plan", None)
    context.user_data.pop("active_part", None)
    
    await update.message.reply_text(text, reply_markup=get_main_keyboard(), parse_mode=ParseMode.HTML)

# ==================== مدیریت دکمه‌های برنامه ====================

async def handle_plan_actions(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.message.text.strip()
    user_id = get_user_id_by_telegram(update.effective_user.id)
    if not user_id:
        await update.message.reply_text("❌ لطفاً اول /start رو بزن.")
        return
    
    plan = context.user_data.get("current_plan", {})
    parts = plan.get("parts", [])
    
    if text == "🔙 بازگشت":
        context.user_data.pop("current_plan", None)
        context.user_data.pop("active_part", None)
        context.user_data.pop("edit_mode", None)
        await update.message.reply_text("🔙 بازگشت به صفحه اصلی", reply_markup=get_main_keyboard())
        return
    
    if text == "🔙 برگشت به حالت قبل":
        await handle_undo(update, context)
        return
    
    if text == "✅ تایید برنامه":
        await confirm_plan(update, context)
        return
    
    if text == "✅ اتمام برنامه":
        await handle_finish_plan(update, context)
        return
    
    if text == "✏️ ویرایش برنامه":
        await show_edit_menu(update, context)
        return
    
    if text == "✏️ ویرایش دستی":
        context.user_data["edit_mode"] = True
        await show_parts_initial(update, context, parts)
        return
    
    if text == "✏️ ویرایش آزاد (چت با AI)":
        context.user_data["mode"] = "ai_chat"
        context.user_data["edit_mode"] = True
        await update.message.reply_text(
            "💬 **حالت ویرایش آزاد با AI**\n\n"
            "تغییرات مورد نظر رو به زبان خودت بگو.\n"
            "مثال: «زمان ریاضی رو به ۱ ساعت افزایش بده»",
            reply_markup=get_ai_chat_keyboard()
        )
        return
    
    if text == "➕ اضافه کردن فعالیت":
        await start_add_activity(update, context)
        return
    
    if text == "🔄 بازنشانی":
        if plan.get("session_id"):
            execute_query("UPDATE study_sessions SET archived = TRUE WHERE session_id = %s", (plan["session_id"],))
        context.user_data.pop("current_plan", None)
        await update.message.reply_text("🔄 برنامه بازنشانی شد!", reply_markup=get_main_keyboard())
        return
    
    if text in ["⏱ تایمر", "▶️ ادامه تایمر", "⏹ توقف", "✅ تکمیل", "🗑 حذف پارت"]:
        active_part = context.user_data.get("active_part")
        if not active_part:
            await update.message.reply_text("❌ ابتدا روی یک پارت کلیک کن.")
            return
        if text in ["⏱ تایمر", "▶️ ادامه تایمر"]:
            await start_timer_command(update, context, active_part)
        elif text == "⏹ توقف":
            await stop_timer_command(update, context, active_part)
        elif text == "✅ تکمیل":
            await handle_done_part(update, context, active_part)
        elif text == "🗑 حذف پارت":
            await handle_delete_part(update, context, active_part)
        return
    
    if text == "🧠 ساخت با AI":
        await handle_build_with_ai(update, context)
        return
    
    if text == "✏️ ساخت دستی":
        context.user_data["build_mode"] = "manual"
        context.user_data["build_step"] = "times"
        await update.message.reply_text(
            "✏️ **ساخت دستی**\n\nمرحله ۱: ساعت‌های مطالعه:\n\n"
            "📝 هر سطر یک بازه:\n۸-۱۰\n۱۰:۳۰-۱۲\n۱۶-۱۸\n\n"
            "برای پایان، دکمه <b>✅ تایید</b> رو بزن.",
            reply_markup=ReplyKeyboardMarkup([["✅ تایید"], ["🔙 بازگشت"]], resize_keyboard=True),
            parse_mode=ParseMode.HTML
        )
        return

async def show_edit_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "✏️ **ویرایش برنامه**\n\nنوع ویرایش:",
        reply_markup=get_edit_menu_keyboard(),
        parse_mode=ParseMode.HTML
    )

# ==================== تایمر ====================

async def start_timer_command(update: Update, context: ContextTypes.DEFAULT_TYPE, part_id: int) -> None:
    chat_id = update.effective_chat.id
    
    if part_id in active_timers:
        await update.message.reply_text("⏱ تایمر در حال اجراست!")
        return
    
    result = execute_query(
        "SELECT title, planned_minutes, completed FROM study_parts WHERE part_id = %s",
        (part_id,), fetch=True
    )
    if not result:
        await update.message.reply_text("❌ پارت یافت نشد.")
        return
    
    title, total_minutes, completed = result
    if completed:
        await update.message.reply_text("❌ این پارت قبلاً انجام شده.")
        return
    
    if part_id in timer_data:
        elapsed_offset = timer_data[part_id].get("elapsed_offset", 0)
        start_time = datetime.now(IRAN_TZ) - timedelta(seconds=elapsed_offset)
        await update.message.reply_text(f"▶️ ادامه تایمر: {title}")
    else:
        elapsed_offset = 0
        start_time = datetime.now(IRAN_TZ)
        await update.message.reply_text(f"⏱ شروع تایمر: {title} ({total_minutes} دقیقه)")
    
    msg = await update.message.reply_text(f"⏱ **تایمر: {title}**\n\n⏳ در حال اجرا...", parse_mode=ParseMode.HTML)
    
    job_data = {
        "chat_id": chat_id, "part_id": part_id, "start_time": start_time,
        "timer_message_id": msg.message_id, "total_minutes": total_minutes,
        "elapsed_offset": elapsed_offset
    }
    
    if context.job_queue:
        job = context.job_queue.run_repeating(update_timer, interval=10, first=10, data=job_data)
        active_timers[part_id] = job
        timer_data[part_id] = {"elapsed_offset": elapsed_offset, "last_update": datetime.now(IRAN_TZ)}
        await show_part_detail(update, context, part_id)

async def stop_timer_command(update: Update, context: ContextTypes.DEFAULT_TYPE, part_id: int) -> None:
    if part_id in active_timers:
        job = active_timers[part_id]
        job_data = job.data
        start_time = job_data.get("start_time")
        elapsed_offset = job_data.get("elapsed_offset", 0)
        total_minutes = job_data.get("total_minutes", 0)
        elapsed = elapsed_offset + int((datetime.now(IRAN_TZ) - start_time).total_seconds())
        
        timer_data[part_id] = {"elapsed_offset": elapsed, "last_update": datetime.now(IRAN_TZ)}
        active_timers[part_id].schedule_removal()
        del active_timers[part_id]
        
        remaining = max(0, total_minutes * 60 - elapsed)
        await update.message.reply_text(
            f"⏹ تایمر متوقف شد.\n⏱ سپری: {elapsed // 60:02d}:{elapsed % 60:02d}\n⏳ باقی: {remaining // 60:02d}:{remaining % 60:02d}",
        )
        await show_part_detail(update, context, part_id)
    else:
        await update.message.reply_text("❌ تایمر فعالی نیست.")

# ==================== تکمیل و حذف پارت ====================

async def handle_done_part(update: Update, context: ContextTypes.DEFAULT_TYPE, part_id: int) -> None:
    user_id = get_user_id_by_telegram(update.effective_user.id)
    
    if part_id in active_timers:
        active_timers[part_id].schedule_removal()
        del active_timers[part_id]
    timer_data.pop(part_id, None)
    
    check_result = execute_query(
        """SELECT completed, title, planned_minutes, actual_minutes, session_id,
                  planned_start_time, planned_end_time, is_fixed_time, part_number
           FROM study_parts WHERE part_id = %s""",
        (part_id,), fetch=True
    )
    if not check_result:
        await update.message.reply_text("❌ پارت یافت نشد.")
        return
    
    is_completed, title, planned_minutes, actual_minutes, session_id, planned_start, planned_end, is_fixed, part_number = check_result
    
    if is_completed:
        await update.message.reply_text(f"⚠️ <b>{title}</b> قبلاً انجام شده.", parse_mode=ParseMode.HTML)
        return
    
    now = datetime.now(IRAN_TZ)
    actual_minutes_calc = planned_minutes
    
    previous_data = {
        "title": title, "planned_minutes": planned_minutes,
        "part_number": part_number, "completed": False, "actual_minutes": 0
    }
    save_change_history(user_id, session_id, part_id, "complete", previous_data)
    
    execute_query(
        """UPDATE study_parts SET completed = TRUE, completed_at = %s,
           actual_minutes = %s, actual_end_time = %s WHERE part_id = %s""",
        (now, actual_minutes_calc, now, part_id)
    )
    
    update_part_times_and_shift_remaining(session_id, part_id, now)
    
    execute_query(
        """UPDATE study_sessions SET completed_parts = (
            SELECT COUNT(*) FROM study_parts WHERE session_id = %s AND completed = TRUE
        ) WHERE session_id = %s""",
        (session_id, session_id)
    )
    
    activity_data = {
        "user_id": user_id, "date": get_today_date(), "subject": title,
        "topic": "", "activity_type": "مطالعه",
        "planned_duration": planned_minutes, "actual_duration": actual_minutes_calc,
        "status": "done", "score": None, "part_order": part_number
    }
    save_activity(activity_data)
    update_subject_status(user_id, title, activity_data)
    
    context.user_data.pop("active_part", None)
    
    await update.message.reply_text(
        f"✅ <b>{title} تکمیل شد!</b>\n\n⏱ زمان: {actual_minutes_calc} دقیقه\n\n"
        f"🔙 برای برگشت، دکمه <b>🔙 برگشت به حالت قبل</b> رو بزن.",
        parse_mode=ParseMode.HTML
    )
    
    plan = context.user_data.get("current_plan", {})
    parts = plan.get("parts", [])
    for p in parts:
        if p["part_id"] == part_id:
            p["completed"] = True
            p["actual_minutes"] = actual_minutes_calc
            break
    
    await show_parts_final(update, context, parts, True)

async def handle_delete_part(update: Update, context: ContextTypes.DEFAULT_TYPE, part_id: int = None) -> None:
    if part_id is None:
        part_id = context.user_data.get("active_part")
    if not part_id:
        await update.message.reply_text("❌ پارت فعالی نیست.")
        return
    
    plan = context.user_data.get("current_plan", {})
    parts = plan.get("parts", [])
    part = next((p for p in parts if p["part_id"] == part_id), None)
    if not part:
        await update.message.reply_text("❌ پارت یافت نشد.")
        return
    if part.get("completed"):
        await update.message.reply_text("❌ پارت انجام شده قابل حذف نیست.")
        return
    
    previous_data = {
        "title": part.get("title"), "grade": part.get("grade"),
        "planned_minutes": part.get("planned_minutes"),
        "time_slot": part.get("time_slot"),
        "planned_start_time": part.get("planned_start_time"),
        "planned_end_time": part.get("planned_end_time"),
        "pages": part.get("pages", 0),
        "is_fixed_time": part.get("is_fixed_time", False),
        "reason": part.get("reason", ""), "part_number": part.get("part_number", 0)
    }
    
    await update.message.reply_text(
        f"⚠️ **آیا مطمئنی {part['title']} حذف بشه؟**\n\nقابل برگشت است.",
        reply_markup=get_confirm_delete_keyboard()
    )
    context.user_data["pending_delete"] = {
        "part_id": part_id, "previous_data": previous_data,
        "part_title": part['title'], "session_id": plan.get("session_id")
    }

async def handle_confirm_delete(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.message.text.strip()
    
    if text == "✅ بله، حذف کن":
        pending = context.user_data.get("pending_delete")
        if not pending:
            await update.message.reply_text("❌ عملیات لغو شد.")
            return
        
        part_id = pending["part_id"]
        previous_data = pending["previous_data"]
        part_title = pending["part_title"]
        session_id = pending.get("session_id")
        
        plan = context.user_data.get("current_plan", {})
        user_id = get_user_id_by_telegram(update.effective_user.id)
        
        save_change_history(user_id, session_id, part_id, "delete", previous_data)
        execute_query("DELETE FROM study_parts WHERE part_id = %s", (part_id,))
        
        plan["parts"] = [p for p in plan.get("parts", []) if p["part_id"] != part_id]
        for i, p in enumerate(plan["parts"]):
            p["part_number"] = i + 1
        
        execute_query("UPDATE study_sessions SET total_parts = %s WHERE session_id = %s",
                     (len(plan["parts"]), session_id))
        
        context.user_data["current_plan"] = plan
        context.user_data.pop("pending_delete", None)
        context.user_data.pop("active_part", None)
        
        await update.message.reply_text(f"🗑 <b>{part_title}</b> حذف شد!", parse_mode=ParseMode.HTML)
        await update.message.reply_text(
            "🔙 برای برگشت، دکمه <b>🔙 برگشت به حالت قبل</b> رو بزن.",
            reply_markup=get_part_buttons_final(plan["parts"]),
            parse_mode=ParseMode.HTML
        )
    elif text == "❌ نه، لغو":
        context.user_data.pop("pending_delete", None)
        await update.message.reply_text("❌ حذف لغو شد.")
        plan = context.user_data.get("current_plan", {})
        if plan.get("parts"):
            await show_parts_final(update, context, plan["parts"])
        else:
            await update.message.reply_text("🔙 بازگشت", reply_markup=get_main_keyboard())

# ==================== تقویم ====================

async def handle_calendar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = get_user_id_by_telegram(update.effective_user.id)
    if not user_id:
        await update.message.reply_text("❌ لطفاً اول /start رو بزن.")
        return
    
    dates = get_recent_dates(user_id, 10)
    if not dates:
        await update.message.reply_text(
            "📭 برنامه‌ای در ۱۰ روز اخیر نداشتی.",
            reply_markup=get_main_keyboard()
        )
        return
    
    await update.message.reply_text(
        "📅 <b>۱۰ روز اخیر:</b>",
        reply_markup=get_calendar_keyboard(dates),
        parse_mode=ParseMode.HTML
    )

async def handle_calendar_date(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.message.text
    if not text.startswith("📅 "):
        return
    
    shamsi_date = text.replace("📅 ", "").strip()
    user_id = get_user_id_by_telegram(update.effective_user.id)
    if not user_id:
        await update.message.reply_text("❌ لطفاً اول /start رو بزن.")
        return
    
    try:
        parts = shamsi_date.split("/")
        if len(parts) == 3:
            year, month, day = map(int, parts)
            jdate = jdatetime.date(year, month, day)
            date_str = jdate.togregorian().strftime("%Y-%m-%d")
        else:
            await update.message.reply_text("❌ تاریخ نامعتبر.")
            return
    except:
        await update.message.reply_text("❌ تاریخ نامعتبر.")
        return
    
    plan = get_plan_by_date(user_id, date_str)
    if not plan or not plan["parts"]:
        await update.message.reply_text(f"📭 در {shamsi_date} برنامه‌ای نداشتی.", reply_markup=get_main_keyboard())
        return
    
    context.user_data["current_plan"] = plan
    context.user_data["selected_date"] = date_str
    
    if plan.get("confirmed", False):
        await show_parts_final(update, context, plan["parts"], True)
    else:
        await show_parts_initial(update, context, plan["parts"])

# ==================== برنامه امروز ====================

async def handle_today_plan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = get_user_id_by_telegram(update.effective_user.id)
    if not user_id:
        await update.message.reply_text("❌ لطفاً اول /start رو بزن.")
        return
    
    today = get_today_date()
    plan = get_plan_by_date(user_id, today)
    
    if plan and plan["parts"]:
        context.user_data["current_plan"] = plan
        context.user_data["selected_date"] = today
        if plan.get("confirmed", False):
            await show_parts_final(update, context, plan["parts"])
        else:
            await show_parts_initial(update, context, plan["parts"])
        return
    
    await update.message.reply_text(
        "📝 **برنامه‌ای وجود ندارد.**\n\nچگونه بسازیم؟",
        reply_markup=get_build_plan_keyboard(),
        parse_mode=ParseMode.HTML
    )

async def handle_build_with_ai(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = get_user_id_by_telegram(update.effective_user.id)
    if not user_id:
        await update.message.reply_text("❌ لطفاً اول /start رو بزن.")
        return
    
    remaining = get_remaining_messages(user_id)
    if remaining <= 0:
        await update.message.reply_text(
            "⛔️ سقف پیام AI تموم شده! برای خرید اشتراک از منو استفاده کن.",
            reply_markup=get_main_keyboard()
        )
        return
    
    user_data = get_user_data(str(update.effective_user.id))
    if not user_data:
        await update.message.reply_text("❌ لطفاً اول /start رو بزن.")
        return
    
    level = calculate_plan_level(user_id)
    user_data['plan_level'] = level
    update_user_plan_level(user_id, level)
    
    wait_msg = await update.message.reply_text("🧠 در حال ساخت برنامه با AI...")
    ai_response = await generate_plan_with_ai(user_id, user_data)
    await wait_msg.delete()
    
    if ai_response and ai_response.get('subjects'):
        session_id = create_plan_from_ai_response(user_id, user_data, ai_response)
        if session_id:
            plan = get_plan_by_date(user_id, get_today_date())
            if plan:
                context.user_data["current_plan"] = plan
                await show_parts_initial(update, context, plan["parts"])
                return
    
    await update.message.reply_text(
        "❌ خطا در ساخت با AI. از ساخت دستی استفاده کن.",
        reply_markup=get_build_plan_keyboard()
    )

async def handle_build_manual(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.message.text.strip()
    
    if text == "🔙 بازگشت":
        context.user_data.pop("build_mode", None)
        context.user_data.pop("build_step", None)
        context.user_data.pop("build_times", None)
        context.user_data.pop("build_time_slots", None)
        context.user_data.pop("build_activities", None)
        await update.message.reply_text("🔙 بازگشت", reply_markup=get_main_keyboard())
        return
    
    if text == "✅ تایید":
        build_times = context.user_data.get("build_times", "")
        if not build_times.strip():
            await update.message.reply_text("❌ حداقل یک بازه وارد کن.")
            return
        
        time_slots = parse_manual_times(build_times)
        if not time_slots:
            await update.message.reply_text("❌ فرمت نامعتبر. مثال: ۸-۱۰")
            return
        
        context.user_data["build_time_slots"] = time_slots
        context.user_data["build_step"] = "activities"
        
        await update.message.reply_text(
            f"✅ {len(time_slots)} بازه ثبت شد.\n\n"
            "✏️ مرحله ۲: فعالیت‌ها:\n"
            "عنوان | مدت | اولویت\n\n"
            "مثال:\nریاضی - فصل ۴ | ۴۵ | بالا\nفیزیک - حرکت | ۶۰ | بالا\n\n"
            "⚠️ تعداد باید برابر باشد.",
            reply_markup=ReplyKeyboardMarkup([["✅ تایید"], ["🔙 بازگشت"]], resize_keyboard=True),
            parse_mode=ParseMode.HTML
        )
        return
    
    current = context.user_data.get("build_times", "")
    current = current + "\n" + text if current else text
    context.user_data["build_times"] = current
    
    await update.message.reply_text(
        f"✅ ثبت شد: {text}\n\n📋 زمان‌ها:\n{current}\n\nبرای پایان دکمه ✅ تایید رو بزن.",
        parse_mode=ParseMode.HTML
    )

async def handle_build_manual_activities(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.message.text.strip()
    
    if text == "🔙 بازگشت":
        context.user_data.pop("build_mode", None)
        context.user_data.pop("build_step", None)
        context.user_data.pop("build_times", None)
        context.user_data.pop("build_time_slots", None)
        context.user_data.pop("build_activities", None)
        await update.message.reply_text("🔙 بازگشت", reply_markup=get_main_keyboard())
        return
    
    if text == "✅ تایید":
        build_activities = context.user_data.get("build_activities", "")
        if not build_activities.strip():
            await update.message.reply_text("❌ حداقل یک فعالیت وارد کن.")
            return
        
        activities = parse_manual_activities(build_activities)
        if not activities:
            await update.message.reply_text("❌ فرمت نامعتبر.")
            return
        
        time_slots = context.user_data.get("build_time_slots", [])
        
        if len(activities) != len(time_slots):
            await update.message.reply_text(
                f"⚠️ تعداد فعالیت ({len(activities)}) با بازه ({len(time_slots)}) برابر نیست."
            )
            return
        
        user_id = get_user_id_by_telegram(update.effective_user.id)
        session_id = create_manual_plan(user_id, time_slots, activities)
        
        if session_id:
            plan = get_plan_by_date(user_id, get_today_date())
            if plan:
                context.user_data["current_plan"] = plan
                context.user_data.pop("build_mode", None)
                context.user_data.pop("build_step", None)
                context.user_data.pop("build_times", None)
                context.user_data.pop("build_time_slots", None)
                context.user_data.pop("build_activities", None)
                
                await update.message.reply_text("✅ برنامه ساخته شد!")
                await show_parts_initial(update, context, plan["parts"])
                return
        
        await update.message.reply_text("❌ خطا در ساخت.")
        return
    
    current = context.user_data.get("build_activities", "")
    current = current + "\n" + text if current else text
    context.user_data["build_activities"] = current
    
    await update.message.reply_text(
        f"✅ ثبت شد: {text}\n\nبرای پایان دکمه ✅ تایید رو بزن.",
        parse_mode=ParseMode.HTML
    )

# ==================== اضافه کردن فعالیت ====================

async def start_add_activity(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    plan = context.user_data.get("current_plan", {})
    parts = plan.get("parts", [])
    
    if not parts:
        await update.message.reply_text("❌ ابتدا برنامه بساز.")
        return
    
    text = "📝 **اضافه کردن فعالیت**\n\nمرحله ۱: بازه زمانی:\n\n"
    keyboard = []
    for i, part in enumerate(parts):
        if not part.get("completed"):
            start = part.get("planned_start_time") or ""
            end = part.get("planned_end_time") or ""
            if start and end:
                text += f"{i+1}. ⬜ {start}-{end} (خالی)\n"
                keyboard.append([f"⏰ بازه {i+1}"])
    
    if not keyboard:
        await update.message.reply_text("❌ همه بازه‌ها پر هستند.")
        return
    
    keyboard.append(["✏️ بازه دلخواه", "🔙 بازگشت"])
    context.user_data["add_activity_step"] = "select_time"
    
    await update.message.reply_text(text, reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True), parse_mode=ParseMode.HTML)

async def handle_add_activity_time(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.message.text.strip()
    
    if text == "🔙 بازگشت":
        context.user_data.pop("add_activity_step", None)
        plan = context.user_data.get("current_plan", {})
        if plan.get("parts"):
            await show_parts_final(update, context, plan["parts"])
        else:
            await update.message.reply_text("🔙 بازگشت", reply_markup=get_main_keyboard())
        return
    
    if text == "✏️ بازه دلخواه":
        await update.message.reply_text("✏️ بازه رو وارد کن:\nمثال: ۱۴-۱۶")
        context.user_data["add_activity_step"] = "custom_time"
        return
    
    if text.startswith("⏰ بازه "):
        try:
            index = int(text.replace("⏰ بازه ", "")) - 1
            plan = context.user_data.get("current_plan", {})
            parts = plan.get("parts", [])
            
            found = None
            count = 0
            for part in parts:
                if not part.get("completed"):
                    if count == index:
                        found = part
                        break
                    count += 1
            
            if found:
                start = found.get("planned_start_time") or ""
                end = found.get("planned_end_time") or ""
                context.user_data["add_activity_time_slot"] = f"{start}-{end}"
                context.user_data["add_activity_part_id"] = found.get("part_id")
                
                await update.message.reply_text(
                    f"✅ بازه {start}-{end} انتخاب شد.\n\n"
                    "✏️ مرحله ۲: فعالیت:\nعنوان | مدت | اولویت\n\nمثال:\nشیمی | ۴۵ | بالا",
                    reply_markup=ReplyKeyboardMarkup([["✅ تایید"], ["🔙 بازگشت"]], resize_keyboard=True),
                    parse_mode=ParseMode.HTML
                )
                context.user_data["add_activity_step"] = "enter_activity"
        except:
            await update.message.reply_text("❌ خطا.")
        return

async def handle_add_activity_custom_time(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.message.text.strip()
    
    if text == "🔙 بازگشت":
        context.user_data.pop("add_activity_step", None)
        plan = context.user_data.get("current_plan", {})
        if plan.get("parts"):
            await show_parts_final(update, context, plan["parts"])
        return
    
    start, end = parse_time_slot(text)
    if not start or not end:
        await update.message.reply_text("❌ فرمت نامعتبر.")
        return
    
    context.user_data["add_activity_time_slot"] = f"{start}-{end}"
    context.user_data["add_activity_part_id"] = None
    
    await update.message.reply_text(
        f"✅ بازه {start}-{end}.\n\n✏️ فعالیت:\nعنوان | مدت | اولویت",
        reply_markup=ReplyKeyboardMarkup([["✅ تایید"], ["🔙 بازگشت"]], resize_keyboard=True)
    )
    context.user_data["add_activity_step"] = "enter_activity"

async def handle_add_activity_enter(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.message.text.strip()
    
    if text == "🔙 بازگشت":
        context.user_data.pop("add_activity_step", None)
        plan = context.user_data.get("current_plan", {})
        if plan.get("parts"):
            await show_parts_final(update, context, plan["parts"])
        return
    
    if text == "✅ تایید":
        activity_text = context.user_data.get("add_activity_text", "")
        if not activity_text.strip():
            await update.message.reply_text("❌ فعالیت وارد کن.")
            return
        
        activities = parse_manual_activities(activity_text)
        if not activities:
            await update.message.reply_text("❌ فرمت نامعتبر.")
            return
        
        activity = activities[0]
        time_slot = context.user_data.get("add_activity_time_slot", "")
        start, end = parse_time_slot(time_slot)
        if not start or not end:
            await update.message.reply_text("❌ خطا در بازه.")
            return
        
        part_id = context.user_data.get("add_activity_part_id")
        plan = context.user_data.get("current_plan", {})
        session_id = plan.get("session_id")
        user_id = get_user_id_by_telegram(update.effective_user.id)
        
        if part_id:
            execute_query(
                """UPDATE study_parts SET title = %s, grade = %s, planned_minutes = %s,
                   planned_start_time = %s, planned_end_time = %s, time_slot = %s, reason = %s
                   WHERE part_id = %s""",
                (activity['title'], activity.get('grade', 3), activity.get('duration', 45),
                 start, end, time_slot, f"اولویت: {activity.get('priority', 'متوسط')}", part_id)
            )
        else:
            part_data = {
                "title": activity['title'], "grade": activity.get('grade', 3),
                "planned_minutes": activity.get('duration', 45),
                "time_slot": time_slot, "planned_start_time": start,
                "planned_end_time": end, "is_fixed_time": True,
                "reason": f"اولویت: {activity.get('priority', 'متوسط')}", "pages": 0
            }
            new_part_id = add_part_to_session(session_id, part_data)
            if new_part_id:
                save_change_history(user_id, session_id, new_part_id, "add", part_data)
        
        plan = get_plan_by_date(user_id, get_today_date())
        if plan:
            context.user_data["current_plan"] = plan
            context.user_data.pop("add_activity_step", None)
            context.user_data.pop("add_activity_time_slot", None)
            context.user_data.pop("add_activity_part_id", None)
            context.user_data.pop("add_activity_text", None)
            
            await update.message.reply_text("✅ فعالیت اضافه شد!")
            await show_parts_final(update, context, plan["parts"])
        return
    
    context.user_data["add_activity_text"] = text
    await update.message.reply_text(f"✅ ثبت شد: {text}\n\nدکمه ✅ تایید رو بزن.")

# ==================== چت با AI ====================

async def handle_ai_chat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = get_user_id_by_telegram(update.effective_user.id)
    if not user_id:
        await update.message.reply_text("❌ لطفاً اول /start رو بزن.")
        return
    
    if not get_user_quota(user_id):
        init_user_quota(user_id)
    
    remaining = get_remaining_messages(user_id)
    
    if remaining <= 0:
        await update.message.reply_text(
            "⛔️ **سقف پیام رایگان امروزت تموم شده!**\n\n"
            "💰 از دکمه خرید اشتراک استفاده کن.",
            parse_mode=ParseMode.HTML
        )
        return
    
    context.user_data["mode"] = "ai_chat"
    user_data = get_user_data(str(update.effective_user.id))
    context_summary = ""
    if user_data:
        weak = ", ".join(user_data.get("weak_subjects", [])) or "ندارد"
        context_summary = f"هدف: {user_data.get('goal', 'نامشخص')} | ضعیف: {weak}"
    
    context.user_data["ai_context_summary"] = context_summary
    
    await update.message.reply_text(
        f"💬 **چت با دستیار هوشمند**\n\n"
        f"📊 پیام باقی‌مانده: {remaining}\n"
        f"📌 {context_summary}",
        reply_markup=get_ai_chat_keyboard(),
        parse_mode=ParseMode.HTML
    )

async def handle_ai_chat_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = get_user_id_by_telegram(update.effective_user.id)
    if not user_id:
        await update.message.reply_text("❌ لطفاً اول /start رو بزن.")
        context.user_data["mode"] = None
        return
    
    text = update.message.text.strip()
    
    if text == "🔙 بازگشت به منو":
        context.user_data["mode"] = None
        context.user_data.pop("ai_context_summary", None)
        context.user_data.pop("edit_mode", None)
        await update.message.reply_text("🔙 برگشتی به منو 👇", reply_markup=get_main_keyboard())
        return
    
    if text == "🔄 مکالمه جدید":
        clear_chat_history(user_id)
        await update.message.reply_text("🔄 مکالمه جدید. سوال بپرس.", reply_markup=get_ai_chat_keyboard())
        return
    
    if text == "📊 مصرف امروز":
        remaining = get_remaining_messages(user_id)
        quota = get_user_quota(user_id)
        plan_type = quota.get("plan_type", "trial") if quota else "trial"
        plan_names = {"trial": "آزمایشی", "basic": "پایه", "premium": "پیشرفته"}
        await update.message.reply_text(
            f"📊 **مصرف امروز**\n\n📌 اشتراک: {plan_names.get(plan_type, 'آزمایشی')}\n"
            f"💬 باقی‌مانده: {remaining}\n📅 {get_today_shamsi()}",
            parse_mode=ParseMode.HTML
        )
        return
    
    if len(text) > 1000:
        await update.message.reply_text("⚠️ پیام کوتاه‌تر بفرست.")
        return
    
    remaining = get_remaining_messages(user_id)
    if remaining <= 0:
        await update.message.reply_text(
            "⛔️ سقف پیام تموم شده! اشتراک تهیه کن.",
        )
        context.user_data["mode"] = None
        await update.message.reply_text("🔙 برگشتی به منو", reply_markup=get_main_keyboard())
        return
    
    # ============================================
    # آماده‌سازی داده‌های کامل برای AI
    # ============================================
    today = get_today_date()
    yesterday_str = (get_iran_now() - timedelta(days=1)).strftime("%Y-%m-%d")
    
    # برنامه امروز
    plan = get_plan_by_date(user_id, today)
    plan_summary = ""
    if plan and plan.get("parts"):
        plan_summary = "📋 برنامه امروز:\n"
        for p in plan["parts"]:
            status = "✅" if p.get("completed") else "⬜"
            plan_summary += f"  {status} {p.get('title')} ({p.get('planned_minutes')}د) {p.get('time_slot', '')}\n"
    else:
        plan_summary = "📋 برنامه‌ای برای امروز وجود ندارد."
    
    # فعالیت‌های امروز
    today_acts = get_today_activities(user_id)
    if today_acts:
        today_summary = "📊 فعالیت‌های امروز:\n"
        for a in today_acts:
            status = "✅" if a.get("status") == "done" else "⬜"
            today_summary += f"  {status} {a['subject']} - {a.get('actual_duration', 0)}د"
            if a.get('score') is not None:
                today_summary += f" (نمره: {a['score']:.0f}%)"
            today_summary += "\n"
    else:
        today_summary = "📊 امروز فعالیتی ثبت نشده."
    
    # فعالیت‌های دیروز
    yesterday_acts = get_yesterday_activities(user_id)
    if yesterday_acts:
        yesterday_summary = "📅 فعالیت‌های دیروز:\n"
        total_y = 0
        for a in yesterday_acts:
            status = "✅" if a.get("status") == "done" else "⬜"
            dur = a.get('actual_duration', 0)
            total_y += dur
            yesterday_summary += f"  {status} {a['subject']} - {dur}د"
            if a.get('score') is not None:
                yesterday_summary += f" (نمره: {a['score']:.0f}%)"
            yesterday_summary += "\n"
        yesterday_summary += f"  📊 جمع دیروز: {format_time_hours_minutes(total_y)}\n"
    else:
        yesterday_summary = "📅 دیروز فعالیتی ثبت نشده."
    
    # خلاصه ۷ روز اخیر
    week_summary_data = get_user_study_summary(user_id, days=7)
    week_summary = "📈 خلاصه ۷ روز اخیر:\n"
    week_summary += f"  • روزهای فعال: {week_summary_data['active_days']} روز\n"
    week_summary += f"  • کل زمان: {format_time_hours_minutes(week_summary_data['total_minutes'])}\n"
    week_summary += f"  • جلسات: {week_summary_data['completed_sessions']}/{week_summary_data['total_sessions']} تکمیل شده\n"
    if week_summary_data['avg_score'] > 0:
        week_summary += f"  • میانگین نمره: {week_summary_data['avg_score']:.1f}%\n"
    
    if week_summary_data['daily']:
        week_summary += "  • جزئیات روزانه:\n"
        for d in week_summary_data['daily'][:7]:
            shamsi = get_shamsi_date(d['date'])
            week_summary += f"    - {shamsi}: {format_time_hours_minutes(d['minutes'])} ({d['done']}/{d['sessions']})\n"
    
    if week_summary_data['subjects']:
        week_summary += "  • دروس:\n"
        for s in week_summary_data['subjects'][:5]:
            week_summary += f"    - {s['subject']}: {format_time_hours_minutes(s['minutes'])}"
            if s['avg_score'] > 0:
                week_summary += f" (میانگین {s['avg_score']:.0f}%)"
            week_summary += "\n"
    
    # ============================================
    # پرامپت کامل با داده‌های تاریخی
    # ============================================
    system_prompt = f"""تو یک **دستیار هوشمند مدیریت برنامه روزانه** هستی که به کاربر در مدیریت زمان، بهره‌وری و مطالعه کمک می‌کند.

=== اطلاعات کاربر ===
{context.user_data.get('ai_context_summary', '')}

=== برنامه امروز ===
{plan_summary}

=== فعالیت‌های امروز ===
{today_summary}

=== فعالیت‌های دیروز ===
{yesterday_summary}

=== خلاصه آماری ===
{week_summary}

⚠️ **قوانین مهم:**

1. **دامنه کاری تو گسترده است:** فقط درباره مطالعه صحبت نکن. درباره برنامه‌ریزی روزانه، ورزش، استراحت، تفریح، خواب، تغذیه، مدیریت زمان، انگیزه و بهره‌وری هم می‌توانی کمک کنی.

2. **اگر کاربر گفت می‌خواهد کاری انجام دهد** (مثل "میخوام برم gym"، "باید ناهار بخورم")، این یک درخواست اضافه کردن به برنامه است. سیستم به‌طور خودکار آن را به برنامه اضافه می‌کند. پاسخ تو کوتاه باشد، مثل: "باشه، به برنامه‌ت اضافه می‌کنم."

3. **اگر کاربر درخواست تغییر برنامه دارد** (اضافه/حذف/تغییر)، پاسخ متنی کوتاه بده. سیستم خودش پیام تایید را نمایش می‌دهد. سعی نکن خودت برنامه را توضیح بدهی.

4. **اگر کاربر درباره گذشته پرسید** (مثل "دیروز چقدر خوندم")، از بخش‌های بالا جواب دقیق بده. اگر داده‌ای وجود ندارد، بگو ثبت نشده.

5. **اگر کاربر فقط سلام کرد یا سوال عمومی پرسید**، دوستانه پاسخ بده و او را به سمت برنامه راهنمایی کن.

6. پاسخ‌ها **کوتاه، دقیق و فارسی روان** باشند.
"""
    
    history = get_chat_history(user_id, limit=10)
    messages = [{"role": "system", "content": system_prompt}]
    messages += history
    messages.append({"role": "user", "content": text})
    
    await context.bot.send_chat_action(update.effective_chat.id, "typing")
    
    try:
        completion = await client.chat.completions.create(
            model=AI_MODEL, messages=messages,
            max_tokens=800, temperature=0.6
        )
        reply = completion.choices[0].message.content
        
        save_chat_message(user_id, "user", text)
        save_chat_message(user_id, "assistant", reply)
        increment_quota(user_id)
        
        remaining_after = get_remaining_messages(user_id)
        
        # پردازش هوشمند تغییرات
        plan_changed = await process_ai_plan_change(update, context, text, reply)
        
        # اگر action = chat بود، پیام AI را نمایش بده
        if not plan_changed:
            await update.message.reply_text(
                f"{reply}\n\n📊 {remaining_after} پیام باقی مونده",
                parse_mode=ParseMode.HTML
            )
        else:
            # فقط تعداد پیام باقی‌مانده را نمایش بده
            await update.message.reply_text(
                f"📊 {remaining_after} پیام باقی مونده"
            )
    except Exception as e:
        logger.error(f"خطا در چت AI: {e}")
        await update.message.reply_text(f"⚠️ خطا: {str(e)[:100]}")

# ==================== گزارش ====================

async def handle_report(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = get_user_id_by_telegram(update.effective_user.id)
    if not user_id:
        await update.message.reply_text("❌ لطفاً اول /start رو بزن.")
        return
    
    activities = get_today_activities(user_id)
    subject_status = get_subject_status(user_id)
    
    if not activities and not subject_status:
        await update.message.reply_text("📭 فعالیتی ثبت نکردی.", reply_markup=get_main_keyboard())
        return
    
    text = f"📊 **گزارش {get_today_shamsi()}** - {get_iran_time_str()}\n\n"
    
    if activities:
        total_time = sum(a.get("actual_duration", a.get("planned_duration", 0)) for a in activities)
        done = len([a for a in activities if a.get("status") == "done"])
        scores = [a.get("score") for a in activities if a.get("score") is not None]
        avg_score = sum(scores) / len(scores) if scores else 0
        text += f"⏱ زمان کل: {format_time_hours_minutes(total_time)}\n"
        text += f"✅ تکمیل: {done}/{len(activities)}\n"
        if scores:
            text += f"📊 میانگین: {avg_score:.1f}%\n"
        text += "\n📋 فعالیت‌ها:\n"
        for a in activities:
            status = "✅" if a.get("status") == "done" else "⬜"
            text += f"{status} {a['subject']} - {a.get('actual_duration', 0)}د\n"
    
    if subject_status:
        text += "\n📚 وضعیت دروس:\n"
        for s in subject_status[:5]:
            level_emoji = "🔴" if s.get("level") == "weak" else "🟡" if s.get("level") == "medium" else "🟢"
            text += f"{level_emoji} {s['subject']}: {s.get('avg_score', 0):.0f}%\n"
    
    remaining = get_remaining_messages(user_id)
    text += f"\n💬 پیام‌های AI: {remaining}"
    
    await update.message.reply_text(text, reply_markup=get_main_keyboard(), parse_mode=ParseMode.HTML)

# ==================== اشتراک و پروفایل ====================

async def handle_subscription(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = get_user_id_by_telegram(update.effective_user.id)
    if not user_id:
        await update.message.reply_text("❌ لطفاً اول /start رو بزن.")
        return
    
    quota = get_user_quota(user_id)
    remaining = get_remaining_messages(user_id)
    plan_names = {"trial": "🌱 آزمایشی", "basic": "📘 پایه", "premium": "🚀 پیشرفته"}
    plan_type = quota.get("plan_type", "trial") if quota else "trial"
    
    text = f"""💰 **اشتراک**

📌 وضعیت: {plan_names.get(plan_type)}
💬 پیام باقی‌مانده: {remaining}

---

🌟 **پلن‌ها:**

📘 **پایه** - ۵۰۰,۰۰۰ تومان
• ۱۵ پیام AI روزانه
• برنامه هوشمند
• تحلیل هفتگی

🚀 **پیشرفته** - ۱,۰۰۰,۰۰۰ تومان
• ۳۰ پیام AI روزانه
• تحلیل عمیق
• اولویت پشتیبانی

---

💳 شماره کارت: **۶۲۱۹۸۶۱۸۳۷۵۶۹۶۸۹**
👤 به نام: **مصطفی فرخندئی**

📸 بعد از واریز، عکس رسید رو بفرست.
"""
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)

async def handle_payment_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = get_user_id_by_telegram(update.effective_user.id)
    if not user_id:
        await update.message.reply_text("❌ لطفاً اول /start رو بزن.")
        return
    
    photo = update.message.photo[-1]
    file_id = photo.file_id
    user = update.effective_user
    
    execute_query(
        "INSERT INTO pending_payments (user_id, photo_file_id, caption) VALUES (%s, %s, %s)",
        (user_id, file_id, f"رسید از {user.full_name} (@{user.username})")
    )
    
    caption = f"""📸 **رسید جدید**

👤 {user.full_name}
🆔 {user.id}
📱 @{user.username if user.username else 'ندارد'}
📅 {get_today_shamsi()}

/approve {user.id} - تایید
/reject {user.id} - رد
"""
    
    for admin_id in ADMIN_IDS:
        try:
            await context.bot.send_photo(chat_id=admin_id, photo=file_id, caption=caption, parse_mode=ParseMode.HTML)
        except Exception as e:
            logger.error(f"خطا در ارسال به ادمین: {e}")
    
    await update.message.reply_text(
        "✅ رسید ارسال شد. پس از تایید، اشتراک فعال می‌شود.",
        reply_markup=get_main_keyboard()
    )

async def handle_profile(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = get_user_id_by_telegram(update.effective_user.id)
    if not user_id:
        await update.message.reply_text("❌ لطفاً اول /start رو بزن.")
        return
    
    user_data = get_user_data(str(update.effective_user.id))
    if not user_data:
        await update.message.reply_text("❌ اطلاعات یافت نشد.")
        return
    
    quota = get_user_quota(user_id)
    remaining = get_remaining_messages(user_id)
    plan_names = {"trial": "🌱 آزمایشی", "basic": "📘 پایه", "premium": "🚀 پیشرفته"}
    plan_type = quota.get("plan_type", "trial") if quota else "trial"
    
    level = user_data.get('plan_level', 0)
    
    text = f"""👤 **پروفایل**

📌 {user_data.get('full_name', 'نامشخص')}
🎯 {user_data.get('goal', 'نامشخص')}
🎓 {user_data.get('grade', 'نامشخص')}
🧪 {user_data.get('field', 'نامشخص')}

📊 سطح: {get_plan_level_emoji(level)} {get_plan_level_name(level)}
💬 پیام AI: {remaining}
💰 اشتراک: {plan_names.get(plan_type)}
"""
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)

# ==================== دستورات ادمین ====================

async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        await update.message.reply_text("❌ دسترسی denied.")
        return
    
    await update.message.reply_text(
        "👨‍💼 **پنل ادمین**\n\n"
        "/advice - ثبت توصیه\n"
        "/listadvice - لیست توصیه‌ها\n"
        "/removeadvice [id] - حذف\n"
        "/stats - آمار\n"
        "/aistats - آمار AI\n"
        "/testai - تست AI\n"
        "/payments - پرداخت‌ها\n"
        "/approve [user_id] - تایید\n"
        "/reject [user_id] - رد",
        parse_mode=ParseMode.HTML
    )

async def advice_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        await update.message.reply_text("❌ دسترسی denied.")
        return
    
    if not context.args:
        await update.message.reply_text("📝 /advice متن توصیه")
        return
    
    admin_text = " ".join(context.args)
    await update.message.reply_text("🧠 در حال پردازش...")
    processed = process_admin_advice_with_ai(admin_text)
    
    if not processed:
        await update.message.reply_text("❌ خطا در پردازش.")
        return
    
    saved = 0
    for advice in processed:
        advice["created_by"] = user_id
        if save_advice(advice):
            saved += 1
    
    await update.message.reply_text(f"✅ {saved} توصیه ثبت شد!")

async def list_advice_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("❌ دسترسی denied.")
        return
    
    results = execute_query(
        """SELECT id, topic, label, advice, priority, is_active, usage_count
           FROM advisory_rules ORDER BY priority DESC LIMIT 20""",
        fetchall=True
    )
    
    if not results:
        await update.message.reply_text("📭 توصیه‌ای نیست.")
        return
    
    text = "📋 **توصیه‌ها:**\n\n"
    for r in results:
        status = "✅" if r[5] else "❌"
        text += f"{status} #{r[0]} | {r[1]} | اولویت {r[4]} | {r[6]} بار\n   {r[3][:50]}...\n\n"
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)

async def remove_advice_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("❌ دسترسی denied.")
        return
    
    if not context.args:
        await update.message.reply_text("❌ /removeadvice 5")
        return
    
    try:
        advice_id = int(context.args[0])
        execute_query("UPDATE advisory_rules SET is_active = FALSE WHERE id = %s", (advice_id,))
        await update.message.reply_text(f"🗑 توصیه #{advice_id} غیرفعال شد.")
    except:
        await update.message.reply_text("❌ ID نامعتبر.")

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("❌ دسترسی denied.")
        return
    
    users = execute_query("SELECT COUNT(*) FROM users", fetch=True)
    onboarded = execute_query("SELECT COUNT(*) FROM users WHERE is_onboarded = TRUE", fetch=True)
    activities = execute_query("SELECT COUNT(*) FROM activity_log", fetch=True)
    advice = execute_query("SELECT COUNT(*) FROM advisory_rules WHERE is_active = TRUE", fetch=True)
    chat_msgs = execute_query("SELECT COUNT(*) FROM chat_messages", fetch=True)
    payments = execute_query("SELECT COUNT(*) FROM pending_payments WHERE status = 'pending'", fetch=True)
    
    text = "📊 **آمار**\n\n"
    text += f"👥 کاربران: {users[0] if users else 0}\n"
    text += f"✅ ثبت‌نام: {onboarded[0] if onboarded else 0}\n"
    text += f"📋 فعالیت: {activities[0] if activities else 0}\n"
    text += f"💡 توصیه: {advice[0] if advice else 0}\n"
    text += f"💬 چت: {chat_msgs[0] if chat_msgs else 0}\n"
    text += f"💰 پرداخت: {payments[0] if payments else 0}\n"
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)

async def ai_stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("❌ دسترسی denied.")
        return
    
    results = execute_query(
        """SELECT u.telegram_id, u.full_name, q.plan_type, q.daily_messages,
                  COUNT(c.id)
           FROM user_quota q
           LEFT JOIN users u ON u.id = q.user_id
           LEFT JOIN chat_messages c ON c.user_id = q.user_id AND c.role = 'assistant'
           GROUP BY u.telegram_id, u.full_name, q.plan_type, q.daily_messages
           ORDER BY q.daily_messages DESC LIMIT 20""",
        fetchall=True
    )
    
    if not results:
        await update.message.reply_text("📭 مصرفی نیست.")
        return
    
    text = "📊 **آمار AI**\n\n"
    for r in results:
        text += f"👤 {r[1] or r[0]}: {r[2] or 'trial'} | امروز: {r[3] or 0} | کل: {r[4] or 0}\n"
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)

async def test_ai_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("❌ دسترسی denied.")
        return
    
    await update.message.reply_text("🧠 تست AI...")
    try:
        start = time.time()
        response = await call_ai("سلام، فقط بگو 'AI وصل است'", max_tokens=20, temperature=0.1)
        elapsed = time.time() - start
        if response:
            await update.message.reply_text(
                f"✅ **AI وصل است!**\n⏱ {elapsed:.2f}s\n📝 {response}\n📌 {AI_MODEL}"
            )
        else:
            await update.message.reply_text("❌ AI پاسخ نداد.")
    except Exception as e:
        await update.message.reply_text(f"❌ خطا: {str(e)[:200]}")

async def approve_subscription(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        await update.message.reply_text("❌ دسترسی غیرمجاز.")
        return
    
    if not context.args:
        await update.message.reply_text("❌ /approve 123456789")
        return
    
    try:
        target_user_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ ID نامعتبر.")
        return
    
    user = execute_query(
        "SELECT id, telegram_id, full_name FROM users WHERE telegram_id = %s",
        (str(target_user_id),), fetch=True
    )
    
    if not user:
        await update.message.reply_text(f"❌ کاربر {target_user_id} یافت نشد.")
        return
    
    db_user_id, telegram_id, full_name = user[0], user[1], user[2] or "کاربر"
    
    try:
        expiry_date = (get_iran_now() + timedelta(days=30)).date()
        execute_query(
            """INSERT INTO user_quota (user_id, plan_type, plan_expiry, daily_messages, last_reset)
               VALUES (%s, 'premium', %s, 0, %s)
               ON CONFLICT (user_id) DO UPDATE SET
               plan_type = 'premium', plan_expiry = %s, daily_messages = 0, last_reset = %s""",
            (db_user_id, expiry_date, get_today_date(), expiry_date, get_today_date())
        )
        execute_query(
            "UPDATE pending_payments SET status = 'approved' WHERE user_id = %s AND status = 'pending'",
            (db_user_id,)
        )
        
        await update.message.reply_text(f"✅ اشتراک {full_name} تایید شد.")
        
        try:
            await context.bot.send_message(
                chat_id=telegram_id,
                text=f"🎉 **اشتراک شما فعال شد!**\n\n"
                     f"✅ پریمیوم برای یک ماه\n📅 انقضا: {expiry_date}\n\n"
                     f"• ۳۰ پیام AI روزانه\n• تحلیل عمیق\n• اولویت پشتیبانی\n\n"
                     f"📚 موفق باشی! 🚀",
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            logger.error(f"خطا در ارسال به کاربر: {e}")
    except Exception as e:
        logger.error(f"خطا در تایید اشتراک: {e}")
        await update.message.reply_text(f"❌ خطا: {str(e)[:100]}")

async def reject_subscription(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        await update.message.reply_text("❌ دسترسی غیرمجاز.")
        return
    
    if not context.args:
        await update.message.reply_text("❌ /reject 123456789")
        return
    
    try:
        target_user_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ ID نامعتبر.")
        return
    
    user = execute_query(
        "SELECT id, telegram_id, full_name FROM users WHERE telegram_id = %s",
        (str(target_user_id),), fetch=True
    )
    
    if not user:
        await update.message.reply_text(f"❌ کاربر {target_user_id} یافت نشد.")
        return
    
    db_user_id, telegram_id, full_name = user[0], user[1], user[2] or "کاربر"
    
    try:
        execute_query(
            "UPDATE pending_payments SET status = 'rejected' WHERE user_id = %s AND status = 'pending'",
            (db_user_id,)
        )
        
        await update.message.reply_text(f"❌ اشتراک {full_name} رد شد.")
        
        try:
            await context.bot.send_message(
                chat_id=telegram_id,
                text="❌ متأسفیم، اشتراک شما تایید نشد.\n\nلطفاً با پشتیبانی تماس بگیرید.",
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            logger.error(f"خطا در ارسال به کاربر: {e}")
    except Exception as e:
        logger.error(f"خطا در رد اشتراک: {e}")

async def list_pending_payments(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("❌ دسترسی غیرمجاز.")
        return
    
    results = execute_query(
        """SELECT p.id, u.telegram_id, u.full_name, p.created_at, p.photo_file_id
           FROM pending_payments p JOIN users u ON u.id = p.user_id
           WHERE p.status = 'pending' ORDER BY p.created_at DESC""",
        fetchall=True
    )
    
    if not results:
        await update.message.reply_text("📭 پرداخت در انتظاری نیست.")
        return
    
    text = "📸 **پرداخت‌های در انتظار:**\n\n"
    for r in results:
        text += f"👤 {r[2] or 'کاربر'} | 🆔 {r[1]}\n📅 {r[3].strftime('%Y-%m-%d %H:%M') if r[3] else 'نامشخص'}\n"
        text += f"/approve {r[1]} | /reject {r[1]}\n\n"
        if r[4]:
            try:
                await context.bot.send_photo(
                    chat_id=update.effective_chat.id, photo=r[4],
                    caption=f"رسید #{r[0]} - {r[2]} ({r[1]})"
                )
            except:
                pass
    
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)

# ==================== هندلر اصلی ====================

async def handle_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.message.text.strip()
    
    if text == "📝 برنامه امروز":
        await handle_today_plan(update, context)
    elif text == "📊 گزارش":
        await handle_report(update, context)
    elif text == "📅 تقویم":
        await handle_calendar(update, context)
    elif text == "💬 چت با AI":
        await handle_ai_chat(update, context)
    elif text == "💰 خرید اشتراک":
        await handle_subscription(update, context)
    elif text == "👤 پروفایل":
        await handle_profile(update, context)
    elif text == "🔙 برگشت به حالت قبل":
        await handle_undo(update, context)
    else:
        await update.message.reply_text("❓ از دکمه‌های منو استفاده کن.", reply_markup=get_main_keyboard())

async def handle_confirm_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """هندلر دکمه‌های تایید/رد"""
    text = update.message.text.strip()
    
    if text == "✅ تایید تغییر":
        if context.user_data.get("pending_change"):
            await apply_pending_change(update, context)
        else:
            await update.message.reply_text("❌ تغییری در انتظار نیست.")
        return
    
    if text == "❌ رد تغییر":
        if context.user_data.get("pending_change"):
            await reject_pending_change(update, context)
        else:
            await update.message.reply_text("❌ تغییری نیست.")
        return
    
    if text == "🗑 بله، همه را پاک کن":
        if context.user_data.get("pending_change") and context.user_data["pending_change"].get("action") == "clear":
            await apply_pending_change(update, context)
        return
    
    if text in ["✅ بله، حذف کن", "❌ نه، لغو"]:
        await handle_confirm_delete(update, context)
        return

async def handle_text_other(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """هندلر اصلی"""
    text = update.message.text.strip()
    
    # دکمه‌های تایید/رد
    if text == "✅ تایید تغییر":
        if context.user_data.get("pending_change"):
            await apply_pending_change(update, context)
        else:
            await update.message.reply_text("❌ تغییری در انتظار نیست.")
        return
    
    if text == "❌ رد تغییر":
        if context.user_data.get("pending_change"):
            await reject_pending_change(update, context)
        else:
            await update.message.reply_text("❌ تغییری نیست.")
        return
    
    if text == "🗑 بله، همه را پاک کن":
        if context.user_data.get("pending_change") and context.user_data["pending_change"].get("action") == "clear":
            await apply_pending_change(update, context)
        return
    
    if text in ["✅ بله، حذف کن", "❌ نه، لغو"]:
        await handle_confirm_delete(update, context)
        return
    
    # ثبت‌نام
    if context.user_data.get("onboarding_step") is not None:
        await onboarding_handler(update, context)
        return
    
    user_id = get_user_id_by_telegram(update.effective_user.id)
    if not user_id:
        if text == "🔄 شروع مجدد":
            await start_command(update, context)
            return
        await update.message.reply_text("❌ لطفاً اول /start رو بزن.")
        return
    
    # منو
    if text in ["📝 برنامه امروز", "📊 گزارش", "📅 تقویم", "💬 چت با AI", 
                "💰 خرید اشتراک", "👤 پروفایل", "🔙 برگشت به حالت قبل"]:
        await handle_main_menu(update, context)
        return
    
    # دکمه‌های برنامه
    plan_buttons = [
        "✅ تایید برنامه", "✅ اتمام برنامه", "✏️ ویرایش برنامه",
        "✏️ ویرایش دستی", "✏️ ویرایش آزاد (چت با AI)",
        "➕ اضافه کردن", "🔄 بازنشانی", "🔙 بازگشت",
        "✅ تایید تغییرات", "❌ لغو تغییرات",
        "⏱ تایمر", "▶️ ادامه تایمر", "⏹ توقف",
        "✅ تکمیل", "🗑 حذف پارت", "🧠 ساخت با AI", "✏️ ساخت دستی"
    ]
    
    if text in plan_buttons:
        await handle_plan_actions(update, context)
        return
    
    # کلیک روی پارت
    if "[" in text and "]" in text and re.search(r'\[(\d+)\]', text):
        await handle_part_click(update, context)
        return
    
    # تقویم
    if text.startswith("📅 "):
        await handle_calendar_date(update, context)
        return
    
    # حالت ساخت دستی
    if context.user_data.get("build_mode") == "manual":
        step = context.user_data.get("build_step")
        
        if text == "🔙 بازگشت":
            context.user_data.pop("build_mode", None)
            context.user_data.pop("build_step", None)
            context.user_data.pop("build_times", None)
            context.user_data.pop("build_time_slots", None)
            context.user_data.pop("build_activities", None)
            await update.message.reply_text("🔙 بازگشت", reply_markup=get_main_keyboard())
            return
        
        if text == "✅ تایید":
            if step == "times":
                await handle_build_manual(update, context)
            elif step == "activities":
                await handle_build_manual_activities(update, context)
            return
        
        if step == "times":
            await handle_build_manual(update, context)
        elif step == "activities":
            await handle_build_manual_activities(update, context)
        return
    
    # حالت اضافه کردن
    if context.user_data.get("add_activity_step"):
        step = context.user_data.get("add_activity_step")
        
        if text == "🔙 بازگشت":
            context.user_data.pop("add_activity_step", None)
            context.user_data.pop("add_activity_time_slot", None)
            context.user_data.pop("add_activity_part_id", None)
            context.user_data.pop("add_activity_text", None)
            plan = context.user_data.get("current_plan", {})
            if plan.get("parts"):
                await show_parts_final(update, context, plan["parts"])
            else:
                await update.message.reply_text("🔙 بازگشت", reply_markup=get_main_keyboard())
            return
        
        if text == "✅ تایید":
            if step == "enter_activity":
                await handle_add_activity_enter(update, context)
            return
        
        if step == "select_time":
            await handle_add_activity_time(update, context)
        elif step == "custom_time":
            await handle_add_activity_custom_time(update, context)
        elif step == "enter_activity":
            await handle_add_activity_enter(update, context)
        return
    
    # چت با AI
    if context.user_data.get("mode") == "ai_chat":
        await handle_ai_chat_message(update, context)
        return
    
    # هر چیز دیگر → AI
    remaining = get_remaining_messages(user_id)
    if remaining <= 0:
        await update.message.reply_text(
            "⛔️ سقف پیام تموم شده!\n💰 از دکمه خرید اشتراک استفاده کن.",
            reply_markup=get_main_keyboard()
        )
        return
    
    context.user_data["mode"] = "ai_chat"
    
    user_data = get_user_data(str(update.effective_user.id))
    context_summary = ""
    if user_data:
        weak = ", ".join(user_data.get("weak_subjects", [])) or "ندارد"
        context_summary = f"هدف: {user_data.get('goal', 'نامشخص')} | ضعیف: {weak}"
    context.user_data["ai_context_summary"] = context_summary
    
    await update.message.reply_text(
        "💬 وارد حالت چت شدم...",
        reply_markup=get_ai_chat_keyboard()
    )
    await handle_ai_chat_message(update, context)

# ==================== تسک‌های زمان‌بندی‌شده ====================

async def nightly_report(context: ContextTypes.DEFAULT_TYPE) -> None:
    users = execute_query(
        "SELECT id, telegram_id, full_name FROM users WHERE is_active = TRUE AND is_onboarded = TRUE",
        fetchall=True
    )
    if not users:
        return
    
    today_shamsi = get_today_shamsi()
    for user in users:
        user_id, telegram_id, full_name = user[0], user[1], user[2] or "کاربر"
        try:
            activities = get_today_activities(user_id)
            if not activities:
                continue
            total_time = sum(a.get("actual_duration", a.get("planned_duration", 0)) for a in activities)
            done = len([a for a in activities if a.get("status") == "done"])
            
            text = f"🌙 **گزارش شبانه - {today_shamsi}**\n\n👤 {full_name}\n\n"
            text += f"⏱ زمان: {format_time_hours_minutes(total_time)}\n"
            text += f"✅ تکمیل: {done}/{len(activities)}\n\n"
            text += "🔜 فردا منتظرت هستم! 🌟"
            
            await context.bot.send_message(telegram_id, text, parse_mode=ParseMode.HTML)
            await asyncio.sleep(0.1)
        except Exception as e:
            logger.error(f"خطا در ارسال گزارش به {telegram_id}: {e}")

def process_admin_advice_with_ai(admin_text: str) -> List[Dict]:
    prompt = f"""توصیه ادمین را به داده ساختاریافته تبدیل کن:
"{admin_text}"
خروجی JSON:
[
  {{
    "topic": "ریاضی",
    "label": "همه",
    "condition": "همیشه",
    "advice": "روزانه ۴۵ دقیقه صبح مطالعه کن",
    "priority": 9,
    "time": "morning",
    "frequency": "daily",
    "subjects": ["ریاضی"]
  }}
]"""
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        response = loop.run_until_complete(call_ai(prompt, max_tokens=1000, temperature=0.2))
        loop.close()
    except:
        response = None
    
    if not response:
        return []
    try:
        json_match = re.search(r'\[.*\]', response, re.DOTALL)
        if json_match:
            return json.loads(json_match.group())
        return []
    except:
        return []

# ==================== تابع اصلی ====================

def main() -> None:
    init_db_pool()
    create_tables()
    
    application = Application.builder() \
        .token(TOKEN) \
        .connect_timeout(60.0) \
        .read_timeout(60.0) \
        .write_timeout(60.0) \
        .pool_timeout(60.0) \
        .build()
    
    # دستورات
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("admin", admin_command))
    application.add_handler(CommandHandler("advice", advice_command))
    application.add_handler(CommandHandler("listadvice", list_advice_command))
    application.add_handler(CommandHandler("removeadvice", remove_advice_command))
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(CommandHandler("aistats", ai_stats_command))
    application.add_handler(CommandHandler("testai", test_ai_command))
    application.add_handler(CommandHandler("approve", approve_subscription))
    application.add_handler(CommandHandler("reject", reject_subscription))
    application.add_handler(CommandHandler("payments", list_pending_payments))
    
    # دکمه‌های تایید
    application.add_handler(MessageHandler(
        filters.Regex("^(✅ تایید تغییر|❌ رد تغییر|🗑 بله، همه را پاک کن|✅ بله، حذف کن|❌ نه، لغو)$"),
        handle_confirm_buttons
    ))
    
    # منو
    application.add_handler(MessageHandler(
        filters.Regex("^(📝 برنامه امروز|📊 گزارش|📅 تقویم|💬 چت با AI|💰 خرید اشتراک|👤 پروفایل|🔙 برگشت به حالت قبل)$"),
        handle_main_menu
    ))
    
    application.add_handler(MessageHandler(
        filters.Regex(r"^📅 \d{4}/\d{2}/\d{2}$"),
        handle_calendar_date
    ))
    
    application.add_handler(MessageHandler(
        filters.Regex("^(✅ تایید برنامه|✅ اتمام برنامه|✏️ ویرایش برنامه|✏️ ویرایش دستی|✏️ ویرایش آزاد \\(چت با AI\\)|➕ اضافه کردن|🔄 بازنشانی|🔙 بازگشت|✅ تایید تغییرات|❌ لغو تغییرات|🧠 ساخت با AI|✏️ ساخت دستی)$"),
        handle_plan_actions
    ))
    
    application.add_handler(MessageHandler(filters.Regex(r".*\[.*\].*"), handle_part_click))
    application.add_handler(MessageHandler(filters.PHOTO, handle_payment_photo))
    
    # هندلر اصلی
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_other))
    
    # تسک زمان‌بندی
    job_queue = application.job_queue
    if job_queue:
        now = get_iran_now()
        target = now.replace(hour=23, minute=0, second=0, microsecond=0)
        if now >= target:
            target += timedelta(days=1)
        seconds_until = (target - now).total_seconds()
        job_queue.run_repeating(nightly_report, interval=86400, first=seconds_until)
        logger.info("✅ تسک‌های زمان‌بندی‌شده تنظیم شدند")
    
    logger.info("🤖 ربات مطالعه هوشمند شروع به کار کرد!")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
