def test_index_returns_page(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "СмартСклад" in resp.text