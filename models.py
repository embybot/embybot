# -*- coding: utf-8 -*-

from sqlalchemy import Column, Integer, String, Boolean, DateTime, BigInteger, Date
from sqlalchemy.sql import func
from .core.database import Base

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    telegram_user_id = Column(BigInteger, unique=True, index=True, nullable=False)
    username = Column(String, nullable=True)
    role = Column(String, nullable=False, server_default='user')
    created_at = Column(DateTime, nullable=False, server_default=func.now())

    emby_user_id = Column(String, unique=True, nullable=True)

    wecom_user_id = Column(String, unique=True, nullable=True)

    bark_key = Column(String, nullable=True)

    points = Column(Integer, nullable=False, server_default='0')
    last_checkin_date = Column(Date, nullable=True)

    subscription_expires_at = Column(DateTime, nullable=True)

    def __repr__(self):
        return f"<User(id={self.id}, telegram_user_id='{self.telegram_user_id}', role='{self.role}')>"

class DurationCode(Base):
    __tablename__ = "duration_codes"
    
    code = Column(String, primary_key=True, index=True)
    owner_telegram_id = Column(BigInteger, index=True)
    duration_days = Column(Integer, nullable=False)
    is_valid = Column(Boolean, default=True, nullable=False)
    is_used = Column(Boolean, default=False, nullable=False)
    used_by_emby_id = Column(String, nullable=True)
    used_by_telegram_id = Column(BigInteger, nullable=True)
    used_at = Column(DateTime, nullable=True)

class InvitationCode(Base):
    __tablename__ = "invitation_codes"

    code = Column(String, primary_key=True, index=True)
    owner_telegram_id = Column(BigInteger, index=True)
    is_valid = Column(Boolean, default=True, nullable=False)
    is_used = Column(Boolean, default=False, nullable=False)
    used_by_telegram_id = Column(BigInteger, nullable=True)
    used_by_emby_id = Column(String, nullable=True)
    used_at = Column(DateTime, nullable=True)

class BannedUser(Base):
    __tablename__ = "banned_users"

    telegram_user_id = Column(BigInteger, primary_key=True)
    ban_reason = Column(String, nullable=True)
    banned_at = Column(DateTime, server_default=func.now())