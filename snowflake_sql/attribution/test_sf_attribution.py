import json
from datetime import UTC, datetime

import pytest

from . import sf_attribution as sf

COMMIT = "a271367cd476545f60e8982458d616c5dc7151bb"
SESSION = "https://partner-workshops.devinenterprise.com/sessions/563ff0e055ca499a8ee42b2b4e40d8b6"
RUN_ID = "SFEXT_20261105A-doc_usage_dashboard-dev-20261105T031900Z-0a1b2c3d"
EP = sf.endpoint("cs_doc_usage_dashboard_account_usage")


def test_endpoint_ids_and_service_users_are_stable():
    assert sorted(sf.ENDPOINTS) == [
        "cs_churn_model_account_score",
        "cs_doc_search_api_top_items",
        "cs_doc_usage_dashboard_account_usage",
        "cs_doc_usage_dashboard_freshness_responder",
        "cs_doc_usage_dashboard_support_lookup",
        "cs_mig_sas_monthly_balance",
    ]
    assert EP.csid == sf.csid("doc_usage_dashboard", "account_usage")
    assert EP.service_user == "SVC_CS_DOC_USAGE_DASHBOARD_ACCOUNT_USAGE"
    assert sf.Endpoint.from_csid("cs_churn_model_account_score", "churn_model") == sf.endpoint("cs_churn_model_account_score")


@pytest.mark.parametrize("workload,endpoint", [("Doc", "x"), ("doc-usage", "x"), ("doc", ""), ("doc", "a__b"), ("1doc", "x")])
def test_bad_identifiers_are_rejected(workload, endpoint):
    with pytest.raises(sf.AttributionError):
        sf.csid(workload, endpoint)


def test_unknown_or_foreign_csid_is_rejected():
    with pytest.raises(sf.AttributionError):
        sf.endpoint("cs_unknown_thing")
    with pytest.raises(sf.AttributionError):
        sf.Endpoint.from_csid("cs_doc_search_api_top_items", "churn_model")
    with pytest.raises(sf.AttributionError):
        sf.service_user("doc_search_api_top_items")


def test_object_names_carry_the_workload_and_monitor_carries_token_and_expiry():
    assert sf.database_name("churn_model") == "DB_CHURN_MODEL_SFEXT_20261105A"
    assert sf.warehouse_name("churn_model") == "WH_CHURN_MODEL"
    assert sf.task_name("churn_model") == "DB_CHURN_MODEL_SFEXT_20261105A.APP.REFRESH_CHURN_MODEL"
    assert sf.streamlit_name("churn_model") == "DB_CHURN_MODEL_SFEXT_20261105A.APP.CHURN_MODEL_APP"
    assert sf.resource_monitor_name("churn_model") == "RM_SFEXT_20261105A_20261119_CHURN_MODEL"


def test_dev_tag_has_session_url_and_fixed_key_order():
    tag = sf.query_tag(EP, phase="dev", commit=COMMIT, run_id=RUN_ID, session_url=SESSION)
    assert list(tag) == ["csid", "workload", "phase", "commit", "run_id", "session_url"]
    assert tag["csid"] == EP.csid and tag["workload"] == "doc_usage_dashboard" and tag["phase"] == "dev"
    assert sf.query_tag_json(EP, phase="dev", commit=COMMIT, run_id=RUN_ID, session_url=SESSION) == json.dumps(
        tag, separators=(",", ":")
    )


def test_runtime_tag_refuses_session_url_and_dev_tag_requires_it():
    run_id = RUN_ID.replace("-dev-", "-runtime-")
    assert "session_url" not in sf.query_tag(EP, phase="runtime", commit=COMMIT, run_id=run_id)
    with pytest.raises(sf.AttributionError, match="must not carry"):
        sf.query_tag(EP, phase="runtime", commit=COMMIT, run_id=run_id, session_url=SESSION)
    with pytest.raises(sf.AttributionError, match="session URL"):
        sf.query_tag(EP, phase="dev", commit=COMMIT, run_id=RUN_ID)
    with pytest.raises(sf.AttributionError, match="session URL"):
        sf.query_tag(EP, phase="dev", commit=COMMIT, run_id=RUN_ID, session_url="https://example.com/x")


@pytest.mark.parametrize(
    "kwargs",
    [
        {"phase": "prod"},
        {"commit": "a271367"},
        {"commit": COMMIT.upper()},
        {"run_id": "20261105-run"},
        {"run_id": "SFEXT_20261105A"},
    ],
)
def test_invalid_tag_fields_are_rejected(kwargs):
    base = {"phase": "dev", "commit": COMMIT, "run_id": RUN_ID, "session_url": SESSION} | kwargs
    with pytest.raises(sf.AttributionError):
        sf.query_tag(EP, **base)


def test_parse_query_tag_round_trips_and_rejects_extras():
    text = sf.query_tag_json(EP, phase="dev", commit=COMMIT, run_id=RUN_ID, session_url=SESSION)
    assert sf.parse_query_tag(text) == json.loads(text)
    runtime = json.loads(text)
    runtime["phase"] = "runtime"
    with pytest.raises(sf.AttributionError, match="unexpected keys"):
        sf.parse_query_tag(json.dumps(runtime))
    with pytest.raises(sf.AttributionError):
        sf.parse_query_tag("not json")
    with pytest.raises(sf.AttributionError, match="missing"):
        sf.parse_query_tag(json.dumps({"csid": EP.csid}))


def test_run_id_format():
    when = datetime(2026, 11, 5, 3, 19, tzinfo=UTC)
    assert (
        sf.make_run_id("churn_model", "runtime", when, nonce="0a1b2c3d")
        == "SFEXT_20261105A-churn_model-runtime-20261105T031900Z-0a1b2c3d"
    )
    assert sf.make_run_id("churn_model", "runtime", when) != sf.make_run_id("churn_model", "runtime", when)
    assert sf.RUN_ID_RE.match(sf.make_run_id("churn_model", "runtime", when))
    with pytest.raises(sf.AttributionError):
        sf.make_run_id("churn_model", "runtime", when, nonce="xyz")
    with pytest.raises(sf.AttributionError):
        sf.make_run_id("churn_model", "runtime", when, run_token="lp-20261105-ab")


@pytest.mark.parametrize("run_id", [
    RUN_ID,
    RUN_ID.replace("doc_usage_dashboard", "churn_model"),
    RUN_ID.replace("-dev-", "-runtime-"),
])
def test_run_id_must_match_tag_workload_and_phase_on_every_entry_point(run_id):
    ep = sf.endpoint("cs_churn_model_account_score")
    tag = {"csid": ep.csid, "workload": ep.workload, "phase": "runtime", "commit": COMMIT, "run_id": run_id}
    env = {"SNOWFLAKE_ACCOUNT": "a", "SNOWFLAKE_PRIVATE_KEY_PATH": "/k.p8",
           "GITHUB_SHA": COMMIT, "SF_RUN_ID": run_id}
    calls = [
        lambda: sf.query_tag(ep, phase="runtime", commit=COMMIT, run_id=run_id),
        lambda: sf.parse_query_tag(json.dumps(tag)),
        lambda: sf.params_from_env(ep, "runtime", env=env),
        lambda: sf.create_task_sql(ep, schedule="60 MINUTE", body="SELECT 1", commit=COMMIT, run_id=run_id),
        lambda: sf.streamlit_session_tag_sql(ep, commit=COMMIT, run_id=run_id),
    ]
    for call in calls:
        with pytest.raises(sf.AttributionError, match=r"run_id workload=.*phase=.*tag workload=.*phase="):
            call()


def test_matching_churn_runtime_run_id_is_accepted():
    ep = sf.endpoint("cs_churn_model_account_score")
    run_id = RUN_ID.replace("doc_usage_dashboard-dev", "churn_model-runtime")
    tag = sf.query_tag(ep, phase="runtime", commit=COMMIT, run_id=run_id)
    assert sf.parse_query_tag(json.dumps(tag)) == tag
    env = {"SNOWFLAKE_ACCOUNT": "a", "SNOWFLAKE_PRIVATE_KEY_PATH": "/k.p8",
           "GITHUB_SHA": COMMIT, "SF_RUN_ID": run_id}
    assert sf.parse_query_tag(sf.params_from_env(ep, "runtime", env=env)["session_parameters"]["QUERY_TAG"]) == tag
    assert run_id in sf.create_task_sql(ep, schedule="60 MINUTE", body="SELECT 1", commit=COMMIT, run_id=run_id)
    assert run_id in sf.streamlit_session_tag_sql(ep, commit=COMMIT, run_id=run_id)


@pytest.mark.parametrize("run_id", [
    None, "", RUN_ID.rsplit("-", 1)[0], RUN_ID + "\n",
    RUN_ID.replace("0a1b2c3d", "0A1B2C3D"),
    RUN_ID.replace("20261105", "20261305"),
    RUN_ID.replace("-dev-", "-prod-"),
])
def test_incomplete_or_malformed_run_ids_are_rejected(run_id):
    with pytest.raises(sf.AttributionError, match="run_id"):
        sf.query_tag(EP, phase="dev", commit=COMMIT, run_id=run_id, session_url=SESSION)


def test_connection_params_use_key_pair_service_user_application_and_tag():
    params = sf.connection_params(
        EP, phase="dev", account="org-acct", private_key_file="/keys/k.p8", commit=COMMIT, run_id=RUN_ID, session_url=SESSION
    )
    assert params["user"] == "SVC_CS_DOC_USAGE_DASHBOARD_ACCOUNT_USAGE"
    assert params["authenticator"] == "SNOWFLAKE_JWT"
    assert params["private_key_file"] == "/keys/k.p8"
    assert params["application"] == "Devin_Demo"
    assert params["warehouse"] == "WH_DOC_USAGE_DASHBOARD"
    assert params["database"] == "DB_DOC_USAGE_DASHBOARD_SFEXT_20261105A"
    assert "password" not in params
    assert sf.parse_query_tag(params["session_parameters"]["QUERY_TAG"])["session_url"] == SESSION


def test_connection_params_require_account_and_key():
    common = {"phase": "runtime", "commit": COMMIT, "run_id": RUN_ID}
    with pytest.raises(sf.AttributionError, match="account"):
        sf.connection_params(EP, account="", private_key_file="/k.p8", **common)
    with pytest.raises(sf.AttributionError, match="key pair"):
        sf.connection_params(EP, account="a", private_key_file="", **common)


def test_params_from_env_prefers_per_endpoint_key_and_only_dev_reads_session_url():
    env = {
        "SNOWFLAKE_ACCOUNT": "org-acct",
        "SNOWFLAKE_PRIVATE_KEY_PATH": "/keys/default.p8",
        "SNOWFLAKE_PRIVATE_KEY_PATH_CS_DOC_USAGE_DASHBOARD_ACCOUNT_USAGE": "/keys/usage.p8",
        "GITHUB_SHA": COMMIT,
        "DEVIN_SESSION_URL": SESSION,
        "SF_RUN_ID": RUN_ID,
    }
    dev = sf.params_from_env(EP, "dev", env=env)
    assert dev["private_key_file"] == "/keys/usage.p8"
    assert json.loads(dev["session_parameters"]["QUERY_TAG"])["session_url"] == SESSION
    env["SF_RUN_ID"] = RUN_ID.replace("doc_usage_dashboard-dev", "churn_model-runtime")
    runtime = sf.params_from_env(sf.endpoint("cs_churn_model_account_score"), "runtime", env=env)
    assert runtime["private_key_file"] == "/keys/default.p8"
    assert "session_url" not in json.loads(runtime["session_parameters"]["QUERY_TAG"])


def test_connect_passes_params_to_connector():
    seen = {}

    def fake_connect(**kwargs):
        seen.update(kwargs)
        return "conn"

    conn = sf.connect(
        EP, "runtime", connector=fake_connect, account="a", private_key_file="/k.p8",
        commit=COMMIT, run_id=RUN_ID.replace("-dev-", "-runtime-"),
    )
    assert conn == "conn"
    assert seen["application"] == "Devin_Demo"
    assert json.loads(seen["session_parameters"]["QUERY_TAG"])["phase"] == "runtime"


def test_real_connector_accepts_every_parameter():
    connection = pytest.importorskip("snowflake.connector.connection")
    params = sf.connection_params(
        EP,
        phase="runtime",
        account="a",
        private_key_file="/k.p8",
        private_key_file_pwd="x",
        role="R",
        commit=COMMIT,
        run_id=RUN_ID.replace("-dev-", "-runtime-"),
    )
    config = connection.ConnectionConfig
    assert set(params) <= set(dir(config)) | set(config._ALIAS_MAP)


def test_resolve_commit_order():
    assert sf.resolve_commit(COMMIT, env={}) == COMMIT
    assert sf.resolve_commit(None, env={"GITHUB_SHA": COMMIT}) == COMMIT
    assert sf.COMMIT_RE.match(sf.resolve_commit(None, env={}))
    with pytest.raises(sf.AttributionError):
        sf.resolve_commit("abc", env={})


def test_task_carries_its_own_runtime_tag_and_comment():
    run_id = RUN_ID.replace("-dev-", "-runtime-")
    sql = sf.create_task_sql(EP, schedule="USING CRON 0 * * * * UTC", body="SELECT 1", commit=COMMIT, run_id=run_id)
    assert "WAREHOUSE = WH_DOC_USAGE_DASHBOARD" in sql
    tag_line = next(line for line in sql.splitlines() if "QUERY_TAG" in line)
    assert '"phase":"runtime"' in tag_line and "session_url" not in tag_line
    assert '"run_token":"SFEXT_20261105A"' in sql and '"expires":"2026-11-19"' in sql
    assert sf.streamlit_session_tag_sql(EP, commit=COMMIT, run_id=run_id).startswith("ALTER SESSION SET QUERY_TAG = '{")


def test_task_deploy_tags_before_resuming():
    run_id = RUN_ID.replace("-dev-", "-runtime-")
    create, tag, resume = sf.task_deploy_sql(EP, schedule="60 MINUTE", body="SELECT 1", commit=COMMIT, run_id=run_id)
    assert create.startswith(f"CREATE OR REPLACE TASK {sf.task_name(EP.workload)}")
    assert tag == (
        f"ALTER TASK {sf.task_name(EP.workload)} SET TAG "
        "DEVIN_GOVERNANCE.TAGS.DEVIN_CSID = 'cs_doc_usage_dashboard_account_usage,cs_doc_usage_dashboard_freshness_responder,cs_doc_usage_dashboard_support_lookup'"
    )
    assert resume == f"ALTER TASK {sf.task_name(EP.workload)} RESUME"


def test_warehouse_is_xs_with_60s_suspend_and_monitor():
    monitor, warehouse = sf.warehouse_ddl("doc_search_api")
    assert "CREATE RESOURCE MONITOR IF NOT EXISTS RM_SFEXT_20261105A_20261119_DOC_SEARCH_API" in monitor
    assert "WAREHOUSE_SIZE = 'XSMALL'" in warehouse and "AUTO_SUSPEND = 60" in warehouse
    assert "RESOURCE_MONITOR = RM_SFEXT_20261105A_20261119_DOC_SEARCH_API" in warehouse
    assert "SFEXT_20261105A" in warehouse and "2026-11-19" in warehouse
    with pytest.raises(sf.AttributionError):
        sf.warehouse_ddl("doc_search_api", credit_quota=0)


def test_every_external_object_names_token_and_expiry():
    statements = (
        sf.tag_ddl()
        + sf.database_ddl("churn_model")
        + sf.warehouse_ddl("churn_model")
        + [
            sf.service_user_ddl(
                sf.endpoint("cs_churn_model_account_score"),
                rsa_public_key="-----BEGIN PUBLIC KEY-----\nMIIB\nAQAB\n-----END PUBLIC KEY-----",
            )
        ]
    )
    for statement in statements:
        if statement.startswith("CREATE"):
            assert "SFEXT_20261105A" in statement, statement
            assert "2026-11-19" in statement or "20261119" in statement, statement


def test_service_user_is_key_pair_only():
    sql = sf.service_user_ddl(EP, rsa_public_key="-----BEGIN PUBLIC KEY-----\nMIIB\nAQAB\n-----END PUBLIC KEY-----")
    assert sql.startswith("CREATE USER IF NOT EXISTS SVC_CS_DOC_USAGE_DASHBOARD_ACCOUNT_USAGE TYPE = SERVICE")
    assert "RSA_PUBLIC_KEY = 'MIIBAQAB'" in sql and "PASSWORD" not in sql
    with pytest.raises(sf.AttributionError):
        sf.service_user_ddl(EP, rsa_public_key="-----BEGIN PUBLIC KEY-----\n-----END PUBLIC KEY-----")


def test_devin_csid_tag_on_database_warehouse_task_and_streamlit():
    statements = sf.object_tag_ddl("doc_usage_dashboard")
    kinds = [s.split()[1] for s in statements]
    assert kinds == ["DATABASE", "WAREHOUSE", "TASK", "STREAMLIT"]
    for s in statements:
        assert s.endswith(f"SET TAG {sf.TAG_FQN} = '{sf.devin_csid_value(sf.workload_csids('doc_usage_dashboard'))}'")
    assert sf.devin_csid_value(["cs_b_x", "cs_a_y", "cs_b_x"]) == "cs_a_y,cs_b_x"
    with pytest.raises(sf.AttributionError):
        sf.devin_csid_value([])


def test_missing_usage_reads_pending_and_cortex_stays_separate():
    assert sf.usage_value(None) == "pending"
    assert sf.usage_value("") == "pending"
    assert sf.usage_value(0) == 0
    assert sf.usage_value("1.25") == 1.25
    with pytest.raises(sf.AttributionError):
        sf.usage_value(-1)
    with pytest.raises(sf.AttributionError):
        sf.usage_value("lots")
    summary = sf.usage_summary({"COMPUTE_CREDITS": 0.5, "QUERY_COUNT": 3, "CORTEX_CREDITS": 2.0})
    assert summary["compute"] == {"compute_credits": 0.5, "query_acceleration_credits": "pending", "query_count": 3}
    assert summary["cortex"] == {"cortex_credits": 2.0, "cortex_tokens": "pending"}
    assert sf.usage_summary(None)["compute"]["compute_credits"] == "pending"


def test_usage_sql_groups_by_tag_and_keeps_cortex_columns_apart():
    sql = sf.USAGE_BY_TAG_SQL
    for column in ("csid", "workload", "phase", "commit", "run_id"):
        assert f"t.tag:{column}::string AS {column}" in sql
    assert "r.graph_run_group_id AS task_execution_id" in sql
    assert "GROUP BY 1, 2, 3, 4, 5, 6" in sql
    assert "CORTEX_FUNCTIONS_QUERY_USAGE_HISTORY" in sql
    assert "SUM(m.compute_credits) AS compute_credits" in sql
    assert "SUM(c.cortex_credits) AS cortex_credits" in sql
    assert "LIKE" not in sql


def test_application_is_labelled_unregistered():
    assert sf.APPLICATION == "Devin_Demo"
    assert sf.APPLICATION_REGISTRATION == "unregistered"


def test_usage_sql_falls_back_to_trailing_runtime_comment():
    sql = sf.USAGE_BY_TAG_SQL
    assert "TRY_PARSE_JSON(q.query_tag) AS session_tag" in sql
    assert "TRY_PARSE_JSON(REGEXP_SUBSTR(" in sql
    assert "q.query_text, '/[*] ([{].*[}]) [*]/[[:space:]]*$', 1, 1, 'es', 1" in sql
    assert "THEN session_tag ELSE comment_tag END AS tag" in sql
    assert "THEN 'query_tag' ELSE 'query_text' END AS tag_source" in sql
    assert "OR (STARTSWITH(comment_tag:csid::string, 'cs_') AND comment_tag:phase::string = 'runtime'" in sql
    assert "AND session_tag:StreamlitEngine::string = 'ExecuteStreamlit')" in sql
    assert "GROUP BY 1, 2, 3, 4, 5, 6, t.tag_source" in sql
    assert sql.count("%(start)s") == 3
    assert sql.count("%(end)s") == 3


def test_comment_fallback_keeps_cortex_separate():
    sql = sf.USAGE_BY_TAG_SQL
    assert "LEFT JOIN compute m ON m.query_id = t.query_id" in sql
    assert "LEFT JOIN cortex c ON c.query_id = t.query_id" in sql
    assert "SUM(m.compute_credits) AS compute_credits" in sql
    assert "SUM(c.cortex_credits) AS cortex_credits" in sql
    assert "SUM(c.cortex_tokens) AS cortex_tokens" in sql
    assert "SUM(m.compute_credits +" not in sql
