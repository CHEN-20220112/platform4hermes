"""SQLAlchemy engine / session / Base 定义。"""
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

# 项目根目录（app/ 的上一级）
BASE_DIR = Path(__file__).resolve().parent.parent

# 运行时数据目录
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

# 本地渲染产物目录（data/profiles/{profile_name}/）
PROFILES_DIR = DATA_DIR / "profiles"
PROFILES_DIR.mkdir(parents=True, exist_ok=True)

DATABASE_URL = f"sqlite:///{(DATA_DIR / 'hermes.db').as_posix()}"

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    """FastAPI 依赖：每个请求一个 session。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
