"""The Flask detail page offers a back link only when there is a page to go back to."""


def test_detail_page_without_referrer_has_no_none_back_link(client, seeded_data):
    response = client.get(f"/demonstration/{seeded_data['demo_id']}")
    assert response.status_code == 200
    assert b'href="None"' not in response.data


def test_detail_page_with_referrer_links_back(client, seeded_data):
    response = client.get(
        f"/demonstration/{seeded_data['demo_id']}",
        headers={"Referer": "http://localhost/demonstrations"},
    )
    assert b'href="http://localhost/demonstrations"' in response.data
