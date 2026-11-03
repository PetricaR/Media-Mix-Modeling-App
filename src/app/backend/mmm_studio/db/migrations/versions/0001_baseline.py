"""Baseline schema: the tables `Base.metadata.create_all` built before migrations existed.

Revision ID: 0001_baseline
Revises: (none)
Create Date: 2026-11-03 15:04:21.254588
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0001_baseline'
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('runs',
    sa.Column('id', sa.String(length=64), nullable=False),
    sa.Column('name', sa.String(length=255), nullable=False),
    sa.Column('filename', sa.String(length=255), nullable=False),
    sa.Column('sha256', sa.String(length=64), nullable=False),
    sa.Column('profile', sa.String(length=32), nullable=False),
    sa.Column('status', sa.String(length=32), nullable=False),
    sa.Column('config', sa.JSON(), nullable=False),
    sa.Column('schema_json', sa.JSON(), nullable=False),
    sa.Column('summary', sa.JSON(), nullable=True),
    sa.Column('versions', sa.JSON(), nullable=False),
    sa.Column('analysis_job_id', sa.Integer(), nullable=True),
    sa.Column('meridian_job_id', sa.Integer(), nullable=True),
    sa.Column('heartbeat_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_runs_is_active'), 'runs', ['is_active'], unique=False)
    op.create_index(op.f('ix_runs_status'), 'runs', ['status'], unique=False)
    op.create_table('forecasts',
    sa.Column('id', sa.String(length=64), nullable=False),
    sa.Column('run_id', sa.String(length=64), nullable=False),
    sa.Column('name', sa.String(length=255), nullable=False),
    sa.Column('status', sa.String(length=32), nullable=False),
    sa.Column('plan_path', sa.String(length=512), nullable=False),
    sa.Column('result_path', sa.String(length=512), nullable=True),
    sa.Column('summary', sa.JSON(), nullable=True),
    sa.Column('job_id', sa.Integer(), nullable=True),
    sa.Column('heartbeat_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['run_id'], ['runs.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_forecasts_run_id'), 'forecasts', ['run_id'], unique=False)
    op.create_index(op.f('ix_forecasts_status'), 'forecasts', ['status'], unique=False)
    op.create_table('optimisations',
    sa.Column('id', sa.String(length=64), nullable=False),
    sa.Column('run_id', sa.String(length=64), nullable=False),
    sa.Column('inputs', sa.JSON(), nullable=False),
    sa.Column('status', sa.String(length=32), nullable=False),
    sa.Column('result_path', sa.String(length=512), nullable=True),
    sa.Column('summary', sa.JSON(), nullable=True),
    sa.Column('job_id', sa.Integer(), nullable=True),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['run_id'], ['runs.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_optimisations_run_id'), 'optimisations', ['run_id'], unique=False)
    op.create_index(op.f('ix_optimisations_status'), 'optimisations', ['status'], unique=False)
    op.create_table('stages',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('run_id', sa.String(length=64), nullable=False),
    sa.Column('order_idx', sa.Integer(), nullable=False),
    sa.Column('key', sa.String(length=64), nullable=False),
    sa.Column('label', sa.String(length=255), nullable=False),
    sa.Column('queue', sa.String(length=32), nullable=False),
    sa.Column('status', sa.String(length=32), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('error', sa.Text(), nullable=True),
    sa.ForeignKeyConstraint(['run_id'], ['runs.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('run_id', 'key', name='uq_stages_run_key')
    )
    op.create_index(op.f('ix_stages_run_id'), 'stages', ['run_id'], unique=False)
    op.create_table('scenarios',
    sa.Column('id', sa.String(length=64), nullable=False),
    sa.Column('forecast_id', sa.String(length=64), nullable=False),
    sa.Column('run_id', sa.String(length=64), nullable=False),
    sa.Column('name', sa.String(length=255), nullable=False),
    sa.Column('inputs', sa.JSON(), nullable=False),
    sa.Column('status', sa.String(length=32), nullable=False),
    sa.Column('result_path', sa.String(length=512), nullable=True),
    sa.Column('summary', sa.JSON(), nullable=True),
    sa.Column('job_id', sa.Integer(), nullable=True),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['forecast_id'], ['forecasts.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_scenarios_forecast_id'), 'scenarios', ['forecast_id'], unique=False)
    op.create_index(op.f('ix_scenarios_run_id'), 'scenarios', ['run_id'], unique=False)
    op.create_index(op.f('ix_scenarios_status'), 'scenarios', ['status'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_scenarios_status'), table_name='scenarios')
    op.drop_index(op.f('ix_scenarios_run_id'), table_name='scenarios')
    op.drop_index(op.f('ix_scenarios_forecast_id'), table_name='scenarios')
    op.drop_table('scenarios')
    op.drop_index(op.f('ix_stages_run_id'), table_name='stages')
    op.drop_table('stages')
    op.drop_index(op.f('ix_optimisations_status'), table_name='optimisations')
    op.drop_index(op.f('ix_optimisations_run_id'), table_name='optimisations')
    op.drop_table('optimisations')
    op.drop_index(op.f('ix_forecasts_status'), table_name='forecasts')
    op.drop_index(op.f('ix_forecasts_run_id'), table_name='forecasts')
    op.drop_table('forecasts')
    op.drop_index(op.f('ix_runs_status'), table_name='runs')
    op.drop_index(op.f('ix_runs_is_active'), table_name='runs')
    op.drop_table('runs')
