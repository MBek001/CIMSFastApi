"""Example of corrected Alembic migration

Replace your c0d3b6ad53b2_added_international_status.py with this content
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = 'c0d3b6ad53b2'
down_revision = None  # Replace with your previous migration ID
branch_labels = None
depends_on = None


def upgrade():
    # Create customertype enum if it doesn't exist
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE customertype AS ENUM ('default', 'international');
        EXCEPTION
            WHEN duplicate_object THEN null;
        END $$;
    """)

    # Add type column to customer table
    op.add_column('customer',
        sa.Column('type',
                  postgresql.ENUM('default', 'international', name='customertype', create_type=False),
                  nullable=True)
    )

    # Create index for customer type
    op.create_index('idx_customer_type', 'customer', ['type'], unique=False, if_not_exists=True)

    # Add international_sales to PageName enum
    op.execute("""
        DO $$ BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_enum
                WHERE enumlabel = 'international_sales'
                AND enumtypid = (
                    SELECT oid FROM pg_type WHERE typname = 'pagename'
                )
            ) THEN
                ALTER TYPE pagename ADD VALUE 'international_sales';
            END IF;
        END $$;
    """)


def downgrade():
    # Remove index
    op.drop_index('idx_customer_type', table_name='customer', if_exists=True)

    # Remove column
    op.drop_column('customer', 'type')

    # Drop enum type
    op.execute("DROP TYPE IF EXISTS customertype")

    # Note: Cannot remove value from pagename enum (PostgreSQL limitation)
    # Manual intervention required to remove 'international_sales' from pagename enum
