def test_privacy_policy_discloses_current_personal_data_processing(client):
    response = client.get("/privacy")

    assert response.status_code == 200
    page = response.get_data(as_text=True)
    assert "Rekisterinpitäjä ja yhteystiedot" in page
    assert "Tietoturva ja lokit" in page
    assert "Katseluanalytiikka" in page
    assert "Joillekin muille lokeille ei ole vielä automaattista poistoa" in page
    assert "Valitus valvontaviranomaiselle" in page
