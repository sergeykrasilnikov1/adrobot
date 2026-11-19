import os

from dotenv import load_dotenv
from sqlalchemy import JSON, String, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

load_dotenv()
engine = create_engine(os.getenv("DATABASE_URL", "sqlite:///./adrobot.db"))
Session = sessionmaker(engine)


class Base(DeclarativeBase):
    pass


class FlowState(Base):
    __tablename__ = "flow_state"

    stream_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    baseline: Mapped[str] = mapped_column(String(64))
    draft: Mapped[list | None] = mapped_column(JSON)
    preferences: Mapped[list] = mapped_column(JSON)
    revision: Mapped[int] = mapped_column(default=1)
    __mapper_args__ = {"version_id_col": revision}
