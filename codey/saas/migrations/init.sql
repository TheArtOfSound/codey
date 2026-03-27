-- Codey Database Schema
-- Run this in Supabase SQL Editor to initialize the database.
-- All tables use UUID primary keys with gen_random_uuid().

-- ============================================================================
-- USERS
-- ============================================================================
CREATE TABLE IF NOT EXISTS users (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email           VARCHAR(255) NOT NULL UNIQUE,
    password_hash   VARCHAR(255),
    github_id       VARCHAR(100),
    github_token    TEXT,
    google_id       VARCHAR(100),
    name            VARCHAR(255),
    avatar_url      TEXT,
    stripe_customer_id VARCHAR(100) UNIQUE,
    plan            VARCHAR(50) NOT NULL DEFAULT 'free',
    plan_status     VARCHAR(50) NOT NULL DEFAULT 'active',
    subscription_id VARCHAR(100),
    subscription_period_end TIMESTAMPTZ,
    credits_remaining INTEGER NOT NULL DEFAULT 10,
    credits_used_this_month INTEGER NOT NULL DEFAULT 0,
    topup_credits   INTEGER NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_active     TIMESTAMPTZ DEFAULT now()
);

-- ============================================================================
-- CREDIT TRANSACTIONS
-- ============================================================================
CREATE TABLE IF NOT EXISTS credit_transactions (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id                 UUID NOT NULL REFERENCES users(id),
    amount                  INTEGER NOT NULL,
    type                    VARCHAR(50) NOT NULL,
    description             TEXT,
    stripe_payment_intent_id VARCHAR(100),
    session_id              UUID,
    credits_before          INTEGER,
    credits_after           INTEGER,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_credit_transactions_user_id ON credit_transactions(user_id);

-- ============================================================================
-- CODING SESSIONS
-- ============================================================================
CREATE TABLE IF NOT EXISTS coding_sessions (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id           UUID NOT NULL REFERENCES users(id),
    mode              VARCHAR(50) NOT NULL,
    prompt            TEXT,
    files_uploaded    TEXT[],
    repo_connected    VARCHAR(255),
    status            VARCHAR(50) NOT NULL DEFAULT 'pending',
    credits_charged   INTEGER NOT NULL DEFAULT 0,
    lines_generated   INTEGER NOT NULL DEFAULT 0,
    files_modified    INTEGER NOT NULL DEFAULT 0,
    nfet_phase_before VARCHAR(20),
    nfet_phase_after  VARCHAR(20),
    es_score_before   DOUBLE PRECISION,
    es_score_after    DOUBLE PRECISION,
    output_summary    TEXT,
    error_message     TEXT,
    started_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at      TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_coding_sessions_user_id ON coding_sessions(user_id);

-- ============================================================================
-- REPOSITORIES
-- ============================================================================
CREATE TABLE IF NOT EXISTS repositories (
    id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id                  UUID NOT NULL REFERENCES users(id),
    github_repo_id           INTEGER,
    full_name                VARCHAR(255),
    clone_url                TEXT,
    default_branch           VARCHAR(100),
    language                 VARCHAR(100),
    autonomous_mode_enabled  BOOLEAN NOT NULL DEFAULT false,
    autonomous_config        JSONB,
    last_analyzed            TIMESTAMPTZ,
    nfet_phase               VARCHAR(20),
    es_score                 DOUBLE PRECISION,
    created_at               TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_repositories_user_id ON repositories(user_id);

-- ============================================================================
-- USER MEMORY
-- ============================================================================
CREATE TABLE IF NOT EXISTS user_memory (
    user_id                 UUID PRIMARY KEY,
    style_model             JSONB NOT NULL DEFAULT '{}'::jsonb,
    work_patterns           JSONB NOT NULL DEFAULT '{}'::jsonb,
    project_knowledge       JSONB NOT NULL DEFAULT '{}'::jsonb,
    communication_style     JSONB NOT NULL DEFAULT '{}'::jsonb,
    structural_preferences  JSONB NOT NULL DEFAULT '{}'::jsonb,
    skill_profile           JSONB NOT NULL DEFAULT '{}'::jsonb,
    explicit_preferences    JSONB NOT NULL DEFAULT '[]'::jsonb,
    proactive_queue         JSONB NOT NULL DEFAULT '[]'::jsonb,
    memory_version          INTEGER NOT NULL DEFAULT 1,
    last_updated            TIMESTAMPTZ NOT NULL DEFAULT now(),
    total_sessions_analyzed INTEGER NOT NULL DEFAULT 0
);

-- ============================================================================
-- MEMORY UPDATE LOGS
-- ============================================================================
CREATE TABLE IF NOT EXISTS memory_update_logs (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id                 UUID NOT NULL REFERENCES users(id),
    session_id              UUID REFERENCES coding_sessions(id),
    update_type             VARCHAR(50) NOT NULL,
    field_updated           VARCHAR(100) NOT NULL,
    previous_value          JSONB,
    new_value               JSONB,
    extraction_confidence   DOUBLE PRECISION,
    source_description      TEXT,
    memory_version_before   INTEGER,
    memory_version_after    INTEGER,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_memory_update_logs_user_id ON memory_update_logs(user_id);

-- ============================================================================
-- PROJECTS
-- ============================================================================
CREATE TABLE IF NOT EXISTS projects (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id          UUID NOT NULL REFERENCES users(id),
    name             VARCHAR(255) NOT NULL,
    language         VARCHAR(100),
    framework        VARCHAR(100),
    description      TEXT,
    is_archived      BOOLEAN NOT NULL DEFAULT false,
    file_tree        JSONB,
    latest_nfet_phase VARCHAR(20),
    latest_es_score  DOUBLE PRECISION,
    total_versions   INTEGER NOT NULL DEFAULT 0,
    total_sessions   INTEGER NOT NULL DEFAULT 0,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_activity    TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_projects_user_id ON projects(user_id);

-- ============================================================================
-- PROJECT VERSIONS
-- ============================================================================
CREATE TABLE IF NOT EXISTS project_versions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id      UUID NOT NULL REFERENCES projects(id),
    session_id      UUID REFERENCES coding_sessions(id),
    version_number  INTEGER NOT NULL,
    commit_message  TEXT,
    files_changed   JSONB,
    diff            TEXT,
    file_snapshot   JSONB,
    nfet_phase      VARCHAR(20),
    es_score        DOUBLE PRECISION,
    nfet_state      JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_project_versions_project_id ON project_versions(project_id);

-- ============================================================================
-- EXPORTS
-- ============================================================================
CREATE TABLE IF NOT EXISTS exports (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES users(id),
    project_id      UUID NOT NULL REFERENCES projects(id),
    export_type     VARCHAR(50) NOT NULL,
    destination     VARCHAR(255),
    status          VARCHAR(50) NOT NULL DEFAULT 'pending',
    file_url        TEXT,
    file_size_bytes INTEGER,
    metadata        JSONB,
    error_message   TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at    TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_exports_user_id ON exports(user_id);

-- ============================================================================
-- REFERRALS
-- ============================================================================
CREATE TABLE IF NOT EXISTS referrals (
    id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    referrer_id              UUID NOT NULL REFERENCES users(id),
    referred_id              UUID REFERENCES users(id),
    status                   VARCHAR(50) NOT NULL DEFAULT 'pending',
    created_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
    converted_at             TIMESTAMPTZ,
    credits_issued_referrer  INTEGER NOT NULL DEFAULT 0,
    credits_issued_referred  INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_referrals_referrer_id ON referrals(referrer_id);
CREATE INDEX IF NOT EXISTS idx_referrals_referred_id ON referrals(referred_id);

-- ============================================================================
-- SESSION COSTS
-- ============================================================================
CREATE TABLE IF NOT EXISTS session_costs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id      UUID NOT NULL REFERENCES coding_sessions(id),
    user_id         UUID NOT NULL REFERENCES users(id),
    input_tokens    INTEGER,
    output_tokens   INTEGER,
    api_cost_usd    DOUBLE PRECISION,
    credits_charged INTEGER,
    margin_ratio    DOUBLE PRECISION,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_session_costs_user_id ON session_costs(user_id);
CREATE INDEX IF NOT EXISTS idx_session_costs_session_id ON session_costs(session_id);

-- ============================================================================
-- API KEYS
-- ============================================================================
CREATE TABLE IF NOT EXISTS api_keys (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID NOT NULL REFERENCES users(id),
    name        VARCHAR(255),
    key_hash    VARCHAR(255) NOT NULL,
    key_prefix  VARCHAR(20),
    last_used   TIMESTAMPTZ,
    expires_at  TIMESTAMPTZ,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_api_keys_user_id ON api_keys(user_id);

-- ============================================================================
-- SECURITY AUDIT LOG (no FK to users — survives user deletion)
-- ============================================================================
CREATE TABLE IF NOT EXISTS security_audit_log (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID,
    action          VARCHAR(100) NOT NULL,
    resource_type   VARCHAR(100),
    resource_id     UUID,
    ip_address      VARCHAR(45),
    user_agent      TEXT,
    result          VARCHAR(20),
    failure_reason  TEXT,
    metadata        JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_security_audit_log_user_id ON security_audit_log(user_id);
CREATE INDEX IF NOT EXISTS idx_security_audit_log_action ON security_audit_log(action);

-- ============================================================================
-- BUILD PROJECTS
-- ============================================================================
CREATE TABLE IF NOT EXISTS build_projects (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             UUID NOT NULL REFERENCES users(id),
    session_id          UUID REFERENCES coding_sessions(id),
    name                VARCHAR(255),
    description         TEXT,
    status              VARCHAR(50) NOT NULL DEFAULT 'planning',
    current_phase       INTEGER NOT NULL DEFAULT 0,
    total_phases        INTEGER,
    files_planned       INTEGER,
    files_completed     INTEGER NOT NULL DEFAULT 0,
    lines_generated     INTEGER NOT NULL DEFAULT 0,
    credits_charged     INTEGER NOT NULL DEFAULT 0,
    nfet_es_score_final DOUBLE PRECISION,
    nfet_phase_final    VARCHAR(20),
    project_plan        JSONB,
    file_tree           JSONB,
    stack               JSONB,
    download_url        TEXT,
    github_repo_url     TEXT,
    started_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at        TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_build_projects_user_id ON build_projects(user_id);

-- ============================================================================
-- BUILD FILES
-- ============================================================================
CREATE TABLE IF NOT EXISTS build_files (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id          UUID NOT NULL REFERENCES build_projects(id) ON DELETE CASCADE,
    file_path           VARCHAR(500) NOT NULL,
    content             TEXT,
    line_count          INTEGER,
    phase               INTEGER,
    status              VARCHAR(50) NOT NULL DEFAULT 'pending',
    stress_score        DOUBLE PRECISION,
    generation_attempts INTEGER NOT NULL DEFAULT 1,
    validation_passed   BOOLEAN,
    credits_charged     DOUBLE PRECISION,
    generated_at        TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_build_files_project_id ON build_files(project_id);

-- ============================================================================
-- BUILD CHECKPOINTS
-- ============================================================================
CREATE TABLE IF NOT EXISTS build_checkpoints (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id      UUID NOT NULL REFERENCES build_projects(id) ON DELETE CASCADE,
    phase           INTEGER,
    phase_name      VARCHAR(255),
    files_in_phase  INTEGER,
    tests_passed    INTEGER,
    tests_failed    INTEGER,
    nfet_es_score   DOUBLE PRECISION,
    nfet_kappa      DOUBLE PRECISION,
    nfet_sigma      DOUBLE PRECISION,
    user_action     VARCHAR(50),
    user_notes      TEXT,
    checkpoint_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_build_checkpoints_project_id ON build_checkpoints(project_id);

-- ============================================================================
-- ROW LEVEL SECURITY
-- ============================================================================

-- Enable RLS on all user-scoped tables
ALTER TABLE users ENABLE ROW LEVEL SECURITY;
ALTER TABLE credit_transactions ENABLE ROW LEVEL SECURITY;
ALTER TABLE coding_sessions ENABLE ROW LEVEL SECURITY;
ALTER TABLE repositories ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_memory ENABLE ROW LEVEL SECURITY;
ALTER TABLE memory_update_logs ENABLE ROW LEVEL SECURITY;
ALTER TABLE projects ENABLE ROW LEVEL SECURITY;
ALTER TABLE project_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE exports ENABLE ROW LEVEL SECURITY;
ALTER TABLE referrals ENABLE ROW LEVEL SECURITY;
ALTER TABLE session_costs ENABLE ROW LEVEL SECURITY;
ALTER TABLE api_keys ENABLE ROW LEVEL SECURITY;
ALTER TABLE security_audit_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE build_projects ENABLE ROW LEVEL SECURITY;
ALTER TABLE build_files ENABLE ROW LEVEL SECURITY;
ALTER TABLE build_checkpoints ENABLE ROW LEVEL SECURITY;

-- Users can only read/update their own row
CREATE POLICY users_self ON users
    FOR ALL USING (id = auth.uid());

-- Credit transactions: users see only their own
CREATE POLICY credit_transactions_owner ON credit_transactions
    FOR ALL USING (user_id = auth.uid());

-- Coding sessions: users see only their own
CREATE POLICY coding_sessions_owner ON coding_sessions
    FOR ALL USING (user_id = auth.uid());

-- Repositories: users see only their own
CREATE POLICY repositories_owner ON repositories
    FOR ALL USING (user_id = auth.uid());

-- User memory: users see only their own
CREATE POLICY user_memory_owner ON user_memory
    FOR ALL USING (user_id = auth.uid());

-- Memory update logs: users see only their own
CREATE POLICY memory_update_logs_owner ON memory_update_logs
    FOR ALL USING (user_id = auth.uid());

-- Projects: users see only their own
CREATE POLICY projects_owner ON projects
    FOR ALL USING (user_id = auth.uid());

-- Project versions: users see versions of their own projects
CREATE POLICY project_versions_owner ON project_versions
    FOR ALL USING (
        project_id IN (SELECT id FROM projects WHERE user_id = auth.uid())
    );

-- Exports: users see only their own
CREATE POLICY exports_owner ON exports
    FOR ALL USING (user_id = auth.uid());

-- Referrals: users see referrals they created or were referred by
CREATE POLICY referrals_owner ON referrals
    FOR ALL USING (referrer_id = auth.uid() OR referred_id = auth.uid());

-- Session costs: users see only their own
CREATE POLICY session_costs_owner ON session_costs
    FOR ALL USING (user_id = auth.uid());

-- API keys: users see only their own
CREATE POLICY api_keys_owner ON api_keys
    FOR ALL USING (user_id = auth.uid());

-- Security audit log: users see only their own entries
CREATE POLICY security_audit_log_owner ON security_audit_log
    FOR SELECT USING (user_id = auth.uid());

-- Build projects: users see only their own
CREATE POLICY build_projects_owner ON build_projects
    FOR ALL USING (user_id = auth.uid());

-- Build files: users see files of their own build projects
CREATE POLICY build_files_owner ON build_files
    FOR ALL USING (
        project_id IN (SELECT id FROM build_projects WHERE user_id = auth.uid())
    );

-- Build checkpoints: users see checkpoints of their own build projects
CREATE POLICY build_checkpoints_owner ON build_checkpoints
    FOR ALL USING (
        project_id IN (SELECT id FROM build_projects WHERE user_id = auth.uid())
    );

-- ============================================================================
-- SERVICE ROLE BYPASS
-- Supabase service_role key bypasses RLS automatically.
-- The FastAPI backend connects with the service_role key so it has full access.
-- ============================================================================
