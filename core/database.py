# -*- coding: utf-8 -*-

import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.ext.declarative import declarative_base

from ..core import config
from .. import i18n

db_url_from_config = config.get_setting('database.url')

engine = None
DATABASE_URL = None

if db_url_from_config:
    DATABASE_URL = db_url_from_config
    safe_url = DATABASE_URL.split('@')[-1] if '@' in DATABASE_URL else DATABASE_URL
    print(i18n._("🗃️ External database configured, connecting to: {url}").format(url=safe_url))
    engine = create_engine(DATABASE_URL, pool_pre_ping=True)
else:
    db_dir = "/config/data"
    db_path = os.path.join(db_dir, "embybot.db")
    DATABASE_URL = f"sqlite:///{db_path}"
    
    print(i18n._("🗃️ No external database configured, falling back to SQLite. Database file located at: {path}").format(path=db_path))
    
    os.makedirs(db_dir, exist_ok=True)
    
    engine = create_engine(
        DATABASE_URL, connect_args={"check_same_thread": False}
    )

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

def init_db():
    print(i18n._("ℹ️ Initializing database, tables will be created if they do not exist..."))
    from .. import models
    Base.metadata.create_all(bind=engine)
    print(i18n._("✅ Database initialization complete."))