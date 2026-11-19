import sqlalchemy as sa
from alembic import op

revision = "001_flow_state"
down_revision = None


def upgrade():
    op.create_table(
        "flow_state",
        sa.Column("stream_id", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("baseline", sa.String(64), nullable=False),
        sa.Column("draft", sa.JSON(), nullable=True),
        sa.Column("preferences", sa.JSON(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
    )


def downgrade():
    op.drop_table("flow_state")
