from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_deploy_scripts_forward_fmp_options_chain_path():
    powershell = (ROOT / "deploy.ps1").read_text(encoding="utf-8")
    shell = (ROOT / "deploy.sh").read_text(encoding="utf-8")
    bicep = (ROOT / "infra" / "db-refresh-job.bicep").read_text(encoding="utf-8")

    assert "FMP_OPTIONS_CHAIN_PATH" in powershell
    assert "FMP_OPTIONS_CHAIN_PATH" in shell
    assert "param fmpOptionsChainPath string = ''" in bicep
    assert "name: 'FMP_OPTIONS_CHAIN_PATH'" in bicep
    assert "value: fmpOptionsChainPath" in bicep
