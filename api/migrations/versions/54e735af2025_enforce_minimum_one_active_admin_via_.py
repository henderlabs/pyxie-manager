"""enforce minimum one active admin via trigger

Revision ID: 54e735af2025
Revises: dce388acf391
Create Date: 2026-09-14 20:53:47.016633

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '54e735af2025'
down_revision = 'dce388acf391'
branch_labels = None
depends_on = None


def upgrade():
    # Defense in depth alongside the app-level check in auth.py::update_user
    # (which gives a clean 400 for the common case). This trigger is the
    # actual hard guarantee: it fires for ANY update or delete on `users`
    # regardless of which code path touches the row, closing both the
    # narrow TOCTOU window between two concurrent self-demotions and any
    # future endpoint that forgets to re-implement the same check.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION enforce_min_one_admin() RETURNS trigger AS $$
        DECLARE
          remaining_admins integer;
        BEGIN
          IF TG_OP = 'DELETE' THEN
            IF OLD.is_admin AND OLD.is_active THEN
              SELECT count(*) INTO remaining_admins FROM users
                WHERE is_admin AND is_active AND id <> OLD.id;
              IF remaining_admins = 0 THEN
                RAISE EXCEPTION 'cannot remove the last active admin';
              END IF;
            END IF;
            RETURN OLD;
          ELSE
            IF (OLD.is_admin AND OLD.is_active) AND NOT (NEW.is_admin AND NEW.is_active) THEN
              SELECT count(*) INTO remaining_admins FROM users
                WHERE is_admin AND is_active AND id <> OLD.id;
              IF remaining_admins = 0 THEN
                RAISE EXCEPTION 'cannot demote or deactivate the last active admin';
              END IF;
            END IF;
            RETURN NEW;
          END IF;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_users_min_one_admin
        BEFORE UPDATE OR DELETE ON users
        FOR EACH ROW EXECUTE FUNCTION enforce_min_one_admin();
        """
    )


def downgrade():
    op.execute("DROP TRIGGER IF EXISTS trg_users_min_one_admin ON users;")
    op.execute("DROP FUNCTION IF EXISTS enforce_min_one_admin();")
