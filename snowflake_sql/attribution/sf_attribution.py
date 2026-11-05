"""Snowflake connection and consumption attribution for OtterWorks workloads.

Every endpoint has a stable client-side ID ``cs_<workload>_<endpoint>`` and its own
key-pair service user ``SVC_<CSID>``. Connections built here set
``application=Devin_Demo`` and a JSON ``QUERY_TAG`` (csid, workload, phase, commit,
run_id; dev tags also carry the session URL) so every credit can be traced to the
client, workload, commit and phase that used it. Tasks carry their own runtime tag
because a task run does not inherit the tag of the session that created it.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import subprocess
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

APPLICATION = "Devin_Demo"
APPLICATION_REGISTRATION = "unregistered"
RUN_TOKEN = "SFEXT_20261105A"
EXPIRES = "2026-11-19"
PHASES = ("dev", "runtime")
QUERY_TAG_KEYS = ("csid", "workload", "phase", "commit", "run_id")
DEV_ONLY_TAG_KEYS = ("session_url",)
TAG_DATABASE = "DEVIN_GOVERNANCE"
TAG_SCHEMA = "TAGS"
TAG_FQN = f"{TAG_DATABASE}.{TAG_SCHEMA}.DEVIN_CSID"
TAGGED_OBJECT_TYPES = ("database", "warehouse", "task", "streamlit")
WAREHOUSE_SIZE = "XSMALL"
AUTO_SUSPEND_SECONDS = 60
MONTHLY_CREDIT_QUOTA = 10
APP_SCHEMA = "APP"
PENDING = "pending"
QUERY_TAG_MAX_CHARS = 2000
TAG_VALUE_MAX_CHARS = 256

IDENT_RE = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")
CSID_RE = re.compile(r"^cs_[a-z][a-z0-9]*(?:_[a-z0-9]+)+$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
RUN_TOKEN_RE = re.compile(r"^SF[A-Z]+_[0-9]{8}[A-Z]?$")
RUN_ID_RE = re.compile(
    r"(?P<token>SF[A-Z]+_[0-9]{8}[A-Z]?)-(?P<workload>[a-z][a-z0-9]*(?:_[a-z0-9]+)*)-"
    r"(?P<phase>dev|runtime)-(?P<stamp>[0-9]{8}T[0-9]{6}Z)-(?P<nonce>[0-9a-f]{8})"
)
SESSION_URL_RE = re.compile(r"^https://[A-Za-z0-9.-]+/sessions/[0-9a-f]{32}$")


class AttributionError(ValueError):
    """A name, tag or connection would break the attribution rules."""


def _identifier(kind: str, value: object) -> str:
    if not isinstance(value, str) or not IDENT_RE.match(value):
        raise AttributionError(f"{kind} must be lower snake_case, got {value!r}")
    return value


def _check_phase(phase: object) -> str:
    if phase not in PHASES:
        raise AttributionError(f"phase must be one of {PHASES}, got {phase!r}")
    return phase  # type: ignore[return-value]


def csid(workload: str, endpoint: str) -> str:
    return f"cs_{_identifier('workload', workload)}_{_identifier('endpoint', endpoint)}"


def service_user(client_side_id: str) -> str:
    if not isinstance(client_side_id, str) or not CSID_RE.match(client_side_id):
        raise AttributionError(f"not a client-side ID: {client_side_id!r}")
    return f"SVC_{client_side_id.upper()}"


def database_name(workload: str) -> str:
    return f"DB_{_identifier('workload', workload).upper()}_{RUN_TOKEN}"


def warehouse_name(workload: str) -> str:
    return f"WH_{_identifier('workload', workload).upper()}"


def resource_monitor_name(workload: str) -> str:
    """Resource monitors take no COMMENT, so the run token and expiry go in the name."""
    return f"RM_{RUN_TOKEN}_{EXPIRES.replace('-', '')}_{_identifier('workload', workload).upper()}"


def task_name(workload: str) -> str:
    return f"{database_name(workload)}.{APP_SCHEMA}.REFRESH_{workload.upper()}"


def streamlit_name(workload: str) -> str:
    return f"{database_name(workload)}.{APP_SCHEMA}.{workload.upper()}_APP"


def devin_csid_value(csids: Iterable[str]) -> str:
    """DEVIN_CSID value for an object a whole workload owns: its csids, sorted and comma-joined."""
    values = sorted(set(csids))
    if not values:
        raise AttributionError("DEVIN_CSID needs at least one csid")
    for value in values:
        service_user(value)
    joined = ",".join(values)
    if len(joined) > TAG_VALUE_MAX_CHARS:
        raise AttributionError(f"DEVIN_CSID value is {len(joined)} characters, Snowflake allows {TAG_VALUE_MAX_CHARS}")
    return joined


@dataclass(frozen=True)
class Endpoint:
    workload: str
    endpoint: str

    def __post_init__(self) -> None:
        _identifier("workload", self.workload)
        _identifier("endpoint", self.endpoint)

    @classmethod
    def from_csid(cls, client_side_id: str, workload: str) -> Endpoint:
        prefix = f"cs_{_identifier('workload', workload)}_"
        if not isinstance(client_side_id, str) or not client_side_id.startswith(prefix):
            raise AttributionError(f"{client_side_id!r} is not an endpoint of workload {workload!r}")
        return cls(workload, client_side_id[len(prefix) :])

    @property
    def csid(self) -> str:
        return csid(self.workload, self.endpoint)

    @property
    def service_user(self) -> str:
        return service_user(self.csid)

    @property
    def database(self) -> str:
        return database_name(self.workload)

    @property
    def warehouse(self) -> str:
        return warehouse_name(self.workload)


ENDPOINTS: dict[str, Endpoint] = {
    e.csid: e
    for e in (
        Endpoint("doc_usage_dashboard", "account_usage"),
        Endpoint("doc_usage_dashboard", "freshness_responder"),
        Endpoint("doc_usage_dashboard", "support_lookup"),
        Endpoint("doc_search_api", "top_items"),
        Endpoint("churn_model", "account_score"),
        Endpoint("mig_sas", "monthly_balance"),
    )
}


def endpoint(client_side_id: str) -> Endpoint:
    try:
        return ENDPOINTS[client_side_id]
    except KeyError:
        raise AttributionError(f"unknown endpoint {client_side_id!r}; known: {sorted(ENDPOINTS)}") from None


def workload_csids(workload: str) -> list[str]:
    return sorted(c for c, e in ENDPOINTS.items() if e.workload == workload)


def make_run_id(
    workload: str,
    phase: str,
    when: datetime | None = None,
    run_token: str = RUN_TOKEN,
    nonce: str | None = None,
) -> str:
    """``<run token>-<workload>-<phase>-<UTC yyyymmddThhmmssZ>-<nonce>``, unique per invocation.

    Callers that want several connections grouped as one run share a single ID (``SF_RUN_ID``).
    """
    _identifier("workload", workload)
    _check_phase(phase)
    if not RUN_TOKEN_RE.match(run_token):
        raise AttributionError(f"run token must look like {RUN_TOKEN}, got {run_token!r}")
    stamp = (when or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    nonce = secrets.token_hex(4) if nonce is None else nonce
    if not re.fullmatch(r"[0-9a-f]{8}", nonce):
        raise AttributionError(f"nonce must be 8 lower-case hex characters, got {nonce!r}")
    return f"{run_token}-{workload}-{phase}-{stamp}-{nonce}"


def parse_run_id(run_id: str) -> dict[str, str]:
    """Parse the complete invocation ID generated by make_run_id."""
    match = RUN_ID_RE.fullmatch(run_id) if isinstance(run_id, str) else None
    if match is None:
        raise AttributionError(
            "run_id must have format <RUN_TOKEN>-<workload>-<phase>-<yyyymmddThhmmssZ>-<8 hex nonce>, "
            f"got {run_id!r}"
        )
    try:
        datetime.strptime(match["stamp"], "%Y%m%dT%H%M%SZ")
    except ValueError as err:
        raise AttributionError(f"run_id has invalid UTC timestamp {match['stamp']!r}") from err
    return match.groupdict()


def resolve_commit(commit: str | None = None, env: Mapping[str, str] | None = None, repo: str = ".") -> str:
    """Explicit commit, else GITHUB_SHA, else ``git rev-parse HEAD``."""
    env = os.environ if env is None else env
    value = commit or env.get("GITHUB_SHA")
    if not value:
        value = subprocess.run(
            ["git", "-C", repo, "rev-parse", "HEAD"], check=True, capture_output=True, text=True
        ).stdout.strip()
    if not COMMIT_RE.match(value):
        raise AttributionError(f"commit must be a full 40-character lower-case SHA, got {value!r}")
    return value


def query_tag(
    ep: Endpoint,
    *,
    phase: str,
    commit: str,
    run_id: str,
    session_url: str | None = None,
) -> dict[str, str]:
    """QUERY_TAG object in fixed key order. Dev tags need the session URL; runtime tags refuse it."""
    _check_phase(phase)
    if not isinstance(commit, str) or not COMMIT_RE.match(commit):
        raise AttributionError(f"commit must be a full 40-character lower-case SHA, got {commit!r}")
    run = parse_run_id(run_id)
    if run["workload"] != ep.workload or run["phase"] != phase:
        raise AttributionError(
            f"run_id workload={run['workload']!r}, phase={run['phase']!r} must match "
            f"tag workload={ep.workload!r}, phase={phase!r}"
        )
    tag = {"csid": ep.csid, "workload": ep.workload, "phase": phase, "commit": commit, "run_id": run_id}
    if phase == "dev":
        if not isinstance(session_url, str) or not SESSION_URL_RE.match(session_url):
            raise AttributionError(f"dev tags need the Devin session URL, got {session_url!r}")
        tag["session_url"] = session_url
    elif session_url is not None:
        raise AttributionError("runtime tags must not carry a session URL")
    return tag


def query_tag_json(ep: Endpoint, **kwargs: Any) -> str:
    text = json.dumps(query_tag(ep, **kwargs), separators=(",", ":"))
    if len(text) > QUERY_TAG_MAX_CHARS:
        raise AttributionError(f"QUERY_TAG is {len(text)} characters, Snowflake allows {QUERY_TAG_MAX_CHARS}")
    return text


def parse_query_tag(text: str) -> dict[str, str]:
    """Inverse of query_tag_json; rejects tags that do not follow the rules."""
    try:
        tag = json.loads(text)
    except (TypeError, json.JSONDecodeError) as err:
        raise AttributionError(f"QUERY_TAG is not JSON: {text!r}") from err
    if not isinstance(tag, dict):
        raise AttributionError("QUERY_TAG must be a JSON object")
    allowed = set(QUERY_TAG_KEYS) | (set(DEV_ONLY_TAG_KEYS) if tag.get("phase") == "dev" else set())
    extra = set(tag) - allowed
    if extra:
        raise AttributionError(f"QUERY_TAG has unexpected keys {sorted(extra)}")
    missing = [k for k in QUERY_TAG_KEYS if k not in tag]
    if missing:
        raise AttributionError(f"QUERY_TAG is missing {missing}")
    ep = Endpoint.from_csid(tag["csid"], tag["workload"])
    return query_tag(ep, phase=tag["phase"], commit=tag["commit"], run_id=tag["run_id"], session_url=tag.get("session_url"))


def sql_string(value: str) -> str:
    """Single-quoted Snowflake string literal."""
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def external_comment(**fields: str) -> str:
    """COMMENT for anything created outside the repo: run token and expiry first, then context."""
    return json.dumps({"run_token": RUN_TOKEN, "expires": EXPIRES, **fields}, separators=(",", ":"))


def connection_params(
    ep: Endpoint,
    *,
    phase: str,
    account: str,
    private_key_file: str,
    commit: str,
    run_id: str,
    session_url: str | None = None,
    role: str | None = None,
    schema: str = APP_SCHEMA,
    private_key_file_pwd: str | None = None,
) -> dict[str, Any]:
    """Keyword arguments for ``snowflake.connector.connect`` for one endpoint (key-pair auth only)."""
    if not account:
        raise AttributionError("account is required")
    if not private_key_file:
        raise AttributionError("endpoints authenticate with a key pair; private_key_file is required")
    params: dict[str, Any] = {
        "account": account,
        "user": ep.service_user,
        "authenticator": "SNOWFLAKE_JWT",
        "private_key_file": private_key_file,
        "application": APPLICATION,
        "warehouse": ep.warehouse,
        "database": ep.database,
        "schema": schema,
        "session_parameters": {
            "QUERY_TAG": query_tag_json(ep, phase=phase, commit=commit, run_id=run_id, session_url=session_url)
        },
    }
    if role:
        params["role"] = role
    if private_key_file_pwd:
        params["private_key_file_pwd"] = private_key_file_pwd
    return params


def _env_key(prefix: str, client_side_id: str) -> str:
    return f"{prefix}_{client_side_id.upper()}"


def params_from_env(
    ep: Endpoint,
    phase: str,
    *,
    env: Mapping[str, str] | None = None,
    commit: str | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    """connection_params from the environment.

    SNOWFLAKE_ACCOUNT, SNOWFLAKE_PRIVATE_KEY_PATH_<CSID> (falls back to SNOWFLAKE_PRIVATE_KEY_PATH),
    SNOWFLAKE_PRIVATE_KEY_PASSPHRASE_<CSID> / SNOWFLAKE_PRIVATE_KEY_PASSPHRASE, SNOWFLAKE_ROLE,
    SF_RUN_ID and, for dev, DEVIN_SESSION_URL.
    """
    env = os.environ if env is None else env
    _check_phase(phase)
    return connection_params(
        ep,
        phase=phase,
        account=env.get("SNOWFLAKE_ACCOUNT", ""),
        private_key_file=env.get(_env_key("SNOWFLAKE_PRIVATE_KEY_PATH", ep.csid)) or env.get("SNOWFLAKE_PRIVATE_KEY_PATH", ""),
        private_key_file_pwd=env.get(_env_key("SNOWFLAKE_PRIVATE_KEY_PASSPHRASE", ep.csid))
        or env.get("SNOWFLAKE_PRIVATE_KEY_PASSPHRASE"),
        role=env.get("SNOWFLAKE_ROLE"),
        commit=resolve_commit(commit, env),
        run_id=run_id or env.get("SF_RUN_ID") or make_run_id(ep.workload, phase),
        session_url=env.get("DEVIN_SESSION_URL") if phase == "dev" else None,
    )


def connect(ep: Endpoint, phase: str, *, connector: Callable[..., Any] | None = None, **kwargs: Any) -> Any:
    """Open a tagged connection. Pass explicit connection_params kwargs, or none to read the environment."""
    params = connection_params(ep, phase=phase, **kwargs) if kwargs else params_from_env(ep, phase)
    if connector is None:
        import snowflake.connector

        connector = snowflake.connector.connect
    return connector(**params)


def tag_ddl() -> list[str]:
    return [
        f"CREATE DATABASE IF NOT EXISTS {TAG_DATABASE} COMMENT = {sql_string(external_comment(purpose='governance tags'))}",
        f"CREATE SCHEMA IF NOT EXISTS {TAG_DATABASE}.{TAG_SCHEMA} COMMENT = {sql_string(external_comment(purpose='governance tags'))}",
        f"CREATE TAG IF NOT EXISTS {TAG_FQN} COMMENT = {sql_string(external_comment(purpose='client-side ID'))}",
    ]


def warehouse_ddl(workload: str, *, credit_quota: int = MONTHLY_CREDIT_QUOTA) -> list[str]:
    if not isinstance(credit_quota, int) or isinstance(credit_quota, bool) or credit_quota <= 0:
        raise AttributionError("credit_quota must be a positive integer")
    monitor, warehouse = resource_monitor_name(workload), warehouse_name(workload)
    return [
        f"CREATE RESOURCE MONITOR IF NOT EXISTS {monitor} WITH CREDIT_QUOTA = {credit_quota} "
        "FREQUENCY = MONTHLY START_TIMESTAMP = IMMEDIATELY "
        "TRIGGERS ON 75 PERCENT DO NOTIFY ON 100 PERCENT DO SUSPEND ON 110 PERCENT DO SUSPEND_IMMEDIATE",
        f"CREATE WAREHOUSE IF NOT EXISTS {warehouse} WITH WAREHOUSE_SIZE = '{WAREHOUSE_SIZE}' "
        f"AUTO_SUSPEND = {AUTO_SUSPEND_SECONDS} AUTO_RESUME = TRUE INITIALLY_SUSPENDED = TRUE "
        f"RESOURCE_MONITOR = {monitor} COMMENT = {sql_string(external_comment(workload=workload))}",
    ]


def database_ddl(workload: str) -> list[str]:
    db = database_name(workload)
    return [
        f"CREATE DATABASE IF NOT EXISTS {db} COMMENT = {sql_string(external_comment(workload=workload))}",
        f"CREATE SCHEMA IF NOT EXISTS {db}.RAW COMMENT = {sql_string(external_comment(workload=workload))}",
        f"CREATE SCHEMA IF NOT EXISTS {db}.{APP_SCHEMA} COMMENT = {sql_string(external_comment(workload=workload))}",
    ]


def service_user_ddl(ep: Endpoint, *, rsa_public_key: str, role: str | None = None) -> str:
    key = "".join(line.strip() for line in rsa_public_key.strip().splitlines() if not line.startswith("-----"))
    if not key:
        raise AttributionError("rsa_public_key is empty")
    parts = [
        f"CREATE USER IF NOT EXISTS {ep.service_user} TYPE = SERVICE",
        f"RSA_PUBLIC_KEY = {sql_string(key)}",
        f"DEFAULT_WAREHOUSE = {ep.warehouse}",
    ]
    if role:
        parts.append(f"DEFAULT_ROLE = {role}")
    parts.append(f"COMMENT = {sql_string(external_comment(csid=ep.csid))}")
    return " ".join(parts)


def create_task_sql(ep: Endpoint, *, schedule: str, body: str, commit: str, run_id: str) -> str:
    """CREATE TASK with its own runtime QUERY_TAG; a task never inherits the creating session's tag.

    The tag is stored on the task, so ``run_id`` identifies this task deployment, not one execution;
    ``USAGE_BY_TAG_SQL`` reports each execution separately as ``task_execution_id``. Snowflake creates
    the task suspended: use ``task_deploy_sql`` to create, tag and then resume it.
    """
    tag = query_tag_json(ep, phase="runtime", commit=commit, run_id=run_id)
    return (
        f"CREATE OR REPLACE TASK {task_name(ep.workload)}\n"
        f"  WAREHOUSE = {ep.warehouse}\n"
        f"  SCHEDULE = {sql_string(schedule)}\n"
        f"  QUERY_TAG = {sql_string(tag)}\n"
        f"  COMMENT = {sql_string(external_comment(csid=ep.csid))}\n"
        f"AS\n{body}"
    )


def resume_task_sql(workload: str) -> str:
    return f"ALTER TASK {task_name(workload)} RESUME"


def task_deploy_sql(ep: Endpoint, *, schedule: str, body: str, commit: str, run_id: str) -> list[str]:
    """Create, tag with DEVIN_CSID, then resume, so the task never runs untagged."""
    return [
        create_task_sql(ep, schedule=schedule, body=body, commit=commit, run_id=run_id),
        f"ALTER TASK {task_name(ep.workload)} SET TAG {TAG_FQN} = {sql_string(devin_csid_value(workload_csids(ep.workload)))}",
        resume_task_sql(ep.workload),
    ]


def streamlit_session_tag_sql(ep: Endpoint, *, commit: str, run_id: str) -> str:
    """First statement a Streamlit app runs: it has no client connection to configure."""
    return f"ALTER SESSION SET QUERY_TAG = {sql_string(query_tag_json(ep, phase='runtime', commit=commit, run_id=run_id))}"


def object_tag_ddl(workload: str) -> list[str]:
    """Set DEVIN_CSID on the workload's database, warehouse, task and Streamlit app."""
    value = sql_string(devin_csid_value(workload_csids(workload)))
    return [
        f"ALTER DATABASE {database_name(workload)} SET TAG {TAG_FQN} = {value}",
        f"ALTER WAREHOUSE {warehouse_name(workload)} SET TAG {TAG_FQN} = {value}",
        f"ALTER TASK {task_name(workload)} SET TAG {TAG_FQN} = {value}",
        f"ALTER STREAMLIT {streamlit_name(workload)} SET TAG {TAG_FQN} = {value}",
    ]


def usage_value(value: object) -> float | int | str:
    """A reported number, or ``pending`` when Snowflake has not reported it yet."""
    if value is None or value == "" or value == PENDING:
        return PENDING
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        try:
            value = float(str(value))
        except ValueError:
            raise AttributionError(f"usage value must be a number or {PENDING!r}, got {value!r}") from None
    if value < 0:
        raise AttributionError(f"usage value must not be negative, got {value!r}")
    return value


COMPUTE_USAGE_KEYS = ("compute_credits", "query_acceleration_credits", "query_count")
CORTEX_USAGE_KEYS = ("cortex_credits", "cortex_tokens")


def usage_summary(row: Mapping[str, object] | None) -> dict[str, dict[str, float | int | str]]:
    """Split one usage row into compute and Cortex blocks; Cortex credits never enter compute."""
    row = {k.lower(): v for k, v in (row or {}).items()}
    return {
        "compute": {k: usage_value(row.get(k)) for k in COMPUTE_USAGE_KEYS},
        "cortex": {k: usage_value(row.get(k)) for k in CORTEX_USAGE_KEYS},
    }


USAGE_BY_TAG_SQL = """\
WITH parsed AS (
  SELECT q.query_id, TRY_PARSE_JSON(q.query_tag) AS session_tag,
         TRY_PARSE_JSON(REGEXP_SUBSTR(
           q.query_text, '/[*] ([{].*[}]) [*]/[[:space:]]*$', 1, 1, 'es', 1
         )) AS comment_tag
  FROM SNOWFLAKE.ACCOUNT_USAGE.QUERY_HISTORY q
  WHERE q.start_time >= %(start)s AND q.start_time < %(end)s
), tagged AS (
  SELECT query_id,
         CASE WHEN STARTSWITH(session_tag:csid::string, 'cs_')
              THEN session_tag ELSE comment_tag END AS tag,
         CASE WHEN STARTSWITH(session_tag:csid::string, 'cs_')
              THEN 'query_tag' ELSE 'query_text' END AS tag_source
  FROM parsed
  WHERE STARTSWITH(session_tag:csid::string, 'cs_')
     OR (STARTSWITH(comment_tag:csid::string, 'cs_') AND comment_tag:phase::string = 'runtime'
         AND session_tag:StreamlitEngine::string = 'ExecuteStreamlit')
), compute AS (
  SELECT a.query_id,
         a.credits_attributed_compute AS compute_credits,
         a.credits_used_query_acceleration AS query_acceleration_credits
  FROM SNOWFLAKE.ACCOUNT_USAGE.QUERY_ATTRIBUTION_HISTORY a
  WHERE a.start_time >= %(start)s AND a.start_time < %(end)s
), task_runs AS (
  SELECT h.query_id, h.graph_run_group_id
  FROM SNOWFLAKE.ACCOUNT_USAGE.TASK_HISTORY h
  WHERE h.scheduled_time >= DATEADD(day, -1, %(start)s) AND h.scheduled_time < %(end)s
), cortex AS (
  SELECT c.query_id, SUM(c.token_credits) AS cortex_credits, SUM(c.tokens) AS cortex_tokens
  FROM SNOWFLAKE.ACCOUNT_USAGE.CORTEX_FUNCTIONS_QUERY_USAGE_HISTORY c
  GROUP BY c.query_id
)
SELECT t.tag:csid::string AS csid,
       t.tag:workload::string AS workload,
       t.tag:phase::string AS phase,
       t.tag:commit::string AS commit,
       t.tag:run_id::string AS run_id,
       r.graph_run_group_id AS task_execution_id,
       SUM(m.compute_credits) AS compute_credits,
       SUM(m.query_acceleration_credits) AS query_acceleration_credits,
       COUNT(*) AS query_count,
       SUM(c.cortex_credits) AS cortex_credits,
       SUM(c.cortex_tokens) AS cortex_tokens,
       t.tag_source
FROM tagged t
LEFT JOIN compute m ON m.query_id = t.query_id
LEFT JOIN cortex c ON c.query_id = t.query_id
LEFT JOIN task_runs r ON r.query_id = t.query_id
GROUP BY 1, 2, 3, 4, 5, 6, t.tag_source
ORDER BY 1, 3, 4, 5, 6
"""
