"""Registration of the SAS workload in the shared attribution library."""

from . import sf_attribution as sf


def test_monthly_balance_registration():
    ep = sf.ENDPOINTS["cs_mig_sas_monthly_balance"]
    assert ep == sf.Endpoint("mig_sas", "monthly_balance")
    assert ep.csid == "cs_mig_sas_monthly_balance"
    assert sf.workload_csids("mig_sas") == [ep.csid]
    assert sf.resource_monitor_name("mig_sas") == "RM_SFEXT_20261105A_20261119_MIG_SAS"
    session = "https://example.com/sessions/" + "a" * 32
    tag = sf.query_tag(ep, phase="dev", commit="a" * 40,
                       run_id=sf.make_run_id("mig_sas", "dev"), session_url=session)
    assert list(tag) == ["csid", "workload", "phase", "commit", "run_id", "session_url"]
    assert tag["session_url"] == session
