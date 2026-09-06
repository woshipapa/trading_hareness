from app.platform.data_product_registry import data_product_contract


def test_longhu_supplemental_evidence_is_registered_for_replay_archive():
    contract = data_product_contract("longhu_supplemental_evidence")
    assert contract.layer == "raw"
    assert contract.replay_role == "provider_contract_replay"
    assert "exchange_date" in contract.partition_keys
