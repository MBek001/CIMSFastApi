"""Example of corrected Alembic migration

Replace your auto-generated migration file with this content if you prefer to use Alembic
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = 'REPLACE_WITH_YOUR_REVISION'
down_revision = None  # Replace with your previous migration ID
branch_labels = None
depends_on = None


def upgrade():
    # Create customertype enum if it doesn't exist
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE customertype AS ENUM ('local', 'international');
        EXCEPTION
            WHEN duplicate_object THEN null;
        END $$;
    """)

    # Add type column to customer table
    op.add_column('customer',
        sa.Column('type',
                  postgresql.ENUM('local', 'international', name='customertype', create_type=False),
                  nullable=True)
    )

    # Create index for customer type
    op.create_index('idx_customer_type', 'customer', ['type'], unique=False, if_not_exists=True)


def downgrade():
    # Remove index
    op.drop_index('idx_customer_type', table_name='customer', if_exists=True)

    # Remove column
    op.drop_column('customer', 'type')

    # Drop enum type
    op.execute("DROP TYPE IF EXISTS customertype")
