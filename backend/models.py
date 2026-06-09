from sqlalchemy import Column, Integer, String, Float, ForeignKey, DateTime, Boolean, Text
from sqlalchemy.orm import relationship
from backend.database import Base
from datetime import datetime


class Organization(Base):
    __tablename__ = "organizations"
    id           = Column(Integer, primary_key=True, index=True)
    name         = Column(String, unique=True, index=True)
    created_at   = Column(DateTime, default=datetime.utcnow)
    webhook_url  = Column(String, nullable=True)

    users        = relationship("User", back_populates="organization")
    aois         = relationship("AOI",  back_populates="organization")
    invite_codes = relationship("InviteCode", back_populates="organization")
    
    officer_contacts = relationship(
        "OfficerContact",
        back_populates="organization",
        cascade="all, delete-orphan"
    )


class InviteCode(Base):
    """
    Invite codes allow users to join an existing organization.
    Admin generates a code → shares it → new user registers with it.
    """
    __tablename__ = "invite_codes"
    id           = Column(Integer, primary_key=True, index=True)
    code         = Column(String, unique=True, index=True)   # e.g. "ECO-XK9F2"
    org_id       = Column(Integer, ForeignKey("organizations.id"))
    role         = Column(String, default="analyst")          # role granted on use
    created_by   = Column(Integer, ForeignKey("users.id"))
    created_at   = Column(DateTime, default=datetime.utcnow)
    expires_at   = Column(DateTime, nullable=True)            # None = never expires
    max_uses     = Column(Integer, default=1)                 # -1 = unlimited
    use_count    = Column(Integer, default=0)
    is_active    = Column(Boolean, default=True)

    organization = relationship("Organization", back_populates="invite_codes")


class User(Base):
    __tablename__ = "users"
    id              = Column(Integer, primary_key=True, index=True)
    email           = Column(String, unique=True, index=True)
    hashed_password = Column(String)
    full_name       = Column(String)
    role            = Column(String, default="viewer")   # "admin" | "analyst" | "viewer"
    org_id          = Column(Integer, ForeignKey("organizations.id"))
    created_at      = Column(DateTime, default=datetime.utcnow)
    is_active       = Column(Boolean, default=True)

    organization = relationship("Organization", back_populates="users")


class AOI(Base):
    __tablename__ = "aois"
    id               = Column(Integer, primary_key=True, index=True)
    name             = Column(String, index=True)
    org_id           = Column(Integer, ForeignKey("organizations.id"))
    geojson_polygon  = Column(Text)
    created_at       = Column(DateTime, default=datetime.utcnow)
    last_scanned     = Column(DateTime, nullable=True)
    is_active        = Column(Boolean, default=True)
    last_risk_level  = Column(String, nullable=True)
    last_carbon_loss = Column(Float, nullable=True)

    organization = relationship("Organization", back_populates="aois")
    alerts       = relationship("Alert", back_populates="aoi", cascade="all, delete-orphan")


class Alert(Base):
    __tablename__ = "alerts"
    id               = Column(Integer, primary_key=True, index=True)
    aoi_id           = Column(Integer, ForeignKey("aois.id"))
    risk_level       = Column(String)
    details          = Column(Text)
    created_at       = Column(DateTime, default=datetime.utcnow)
    resolved         = Column(Boolean, default=False)
    resolved_at      = Column(DateTime, nullable=True)
    confidence_score = Column(Float, nullable=True)
    carbon_loss_tons = Column(Float, nullable=True)

    aoi = relationship("AOI", back_populates="alerts")
    
class OfficerContact(Base):
    __tablename__ = "officer_contacts"

    id          = Column(Integer, primary_key=True, index=True)
    org_id      = Column(Integer, ForeignKey("organizations.id"), nullable=False)
    name        = Column(String, nullable=False)
    email       = Column(String, nullable=True)
    phone       = Column(String, nullable=True)          # E.164 e.g. +911234567890
    alert_types = Column(String, default="HIGH,MEDIUM")  # comma-separated risk levels
    is_active   = Column(Boolean, default=True)
    created_at  = Column(DateTime, default=datetime.utcnow)

    organization = relationship("Organization", back_populates="officer_contacts")
